from datetime import timedelta

import httpx
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import *
from .permissions import HasQAProfile, IsApplicationOwnerOrQA
from .serializers import *
from .tasks import execute_approved_candidate, execute_test_run
from integrations.github.repository import GitHubAPIError, retrieve_repository_file
from ai_engine.failure_analysis import analyze_failure
from ai_engine.provider import (
    AIConfigurationError,
    AIProviderError,
    request_json,
    validate_ai_configuration,
)
from ai_engine.test_generator import generate_test_file, suggest_tests
from ai_engine.throttles import AIAssistantThrottle


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = (HasQAProfile,)


class ApplicationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ApplicationSerializer
    permission_classes = (HasQAProfile, IsApplicationOwnerOrQA)

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Application.objects.all()
        profile = getattr(user, "qa_profile", None)
        if not profile:
            return Application.objects.none()
        return Application.objects.filter(team__organization=profile.organization)

    @action(detail=True, methods=("get",), url_path="repository-file")
    def repository_file(self, request, pk=None):
        application = self.get_object()
        if "github_source_read" not in application.approved_test_scope:
            return Response(
                {"detail": "Source retrieval is not authorized for this application. Re-register with explicit authorization."},
                status=status.HTTP_403_FORBIDDEN,
            )
        file_path = request.query_params.get("path", "")
        try:
            result = retrieve_repository_file(
                application.github_repository,
                application.default_branch,
                file_path,
            )
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        except GitHubAPIError as exc:
            response_status = exc.status_code if exc.status_code in {401, 403, 404, 429} else 502
            return Response({"detail": str(exc)}, status=response_status)
        except httpx.RequestError:
            return Response(
                {"detail": "GitHub could not be reached while retrieving the repository file."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)

    @action(
        detail=True,
        methods=("post",),
        url_path="ai/test-suggestions",
        throttle_classes=(AIAssistantThrottle,),
    )
    def ai_test_suggestions(self, request, pk=None):
        application = self.get_object()
        if "github_source_read" not in application.approved_test_scope:
            return Response(
                {"detail": "AI suggestions require explicit authorization to read repository source."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not isinstance(request.data, dict):
            return Response({"detail": "A JSON object containing the source path is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_ai_configuration()
        except AIConfigurationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        file_path = request.data.get("path", "")
        if not isinstance(file_path, str) or not file_path:
            return Response({"detail": "A repository-relative source file path is required."}, status=status.HTTP_400_BAD_REQUEST)
        filename = file_path.lower().rsplit("/", 1)[-1]
        if filename.startswith(".env") or filename in {
            "credentials.json",
            "secrets.json",
            "service-account.json",
        }:
            return Response({"detail": "AI source analysis is disabled for credential files."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            source = retrieve_repository_file(
                application.github_repository,
                application.default_branch,
                file_path,
            )
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        except GitHubAPIError as exc:
            response_status = exc.status_code if exc.status_code in {401, 403, 404, 429} else 502
            return Response({"detail": str(exc)}, status=response_status)
        except httpx.RequestError:
            return Response(
                {"detail": "GitHub could not be reached while retrieving the source file."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        try:
            result = suggest_tests(source["path"], source["content"])
        except AIConfigurationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except AIProviderError as exc:
            response_status = 429 if exc.status_code == 429 else 502
            return Response({"detail": str(exc)}, status=response_status)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        result["source_file"] = source["path"]
        result["source_file_size_bytes"] = source["size_bytes"]
        result["source_characters_considered"] = min(len(source["content"]), 20000)
        return Response(result)

    @action(
        detail=True,
        methods=("post",),
        url_path="ai/provider-check",
        throttle_classes=(AIAssistantThrottle,),
    )
    def ai_provider_check(self, request, pk=None):
        self.get_object()
        try:
            _, model = validate_ai_configuration()
            result = request_json(
                instructions="Return valid JSON only.",
                prompt='Confirm model access by returning exactly {"ok": true}.',
            )
        except AIConfigurationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except AIProviderError as exc:
            response_status = 429 if exc.status_code == 429 else 502
            return Response({"detail": str(exc)}, status=response_status)
        if result.get("ok") is not True:
            return Response(
                {"detail": "OpenAI responded, but the model did not return the expected connection-check result."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"connected": True, "provider": "openai", "model": model})

    @action(
        detail=True,
        methods=("get",),
        url_path="ai/candidates",
    )
    def ai_candidates(self, request, pk=None):
        application = self.get_object()
        candidates = TestCase.objects.filter(
            suite__application=application,
            definition__kind="ai_generated",
        ).order_by("-created_at")[:20]
        return Response([
            {
                "id": str(candidate.id),
                "title": candidate.name,
                "rationale": candidate.definition.get("rationale", ""),
                "source_file": candidate.definition.get("source_file", ""),
                "source_sha": candidate.definition.get("source_sha", ""),
                "framework": candidate.definition.get("framework", ""),
                "test_code": candidate.definition.get("test_code", ""),
                "approved": candidate.approved,
                "status": candidate.definition.get("status", "pending_review"),
            }
            for candidate in candidates
        ])

    @action(
        detail=True,
        methods=("post",),
        url_path="ai/generate-test",
        throttle_classes=(AIAssistantThrottle,),
    )
    def ai_generate_test(self, request, pk=None):
        application = self.get_object()
        if "github_source_read" not in application.approved_test_scope:
            return Response(
                {"detail": "AI test generation requires explicit authorization to read repository source."},
                status=status.HTTP_403_FORBIDDEN,
            )
        file_path = request.data.get("path", "") if isinstance(request.data, dict) else ""
        if not isinstance(file_path, str) or not file_path:
            return Response({"detail": "A repository-relative Python source path is required."}, status=status.HTTP_400_BAD_REQUEST)
        filename = file_path.lower().rsplit("/", 1)[-1]
        if filename.startswith(".env") or filename in {
            "credentials.json",
            "secrets.json",
            "service-account.json",
        }:
            return Response({"detail": "AI source analysis is disabled for credential files."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_ai_configuration()
            source = retrieve_repository_file(
                application.github_repository,
                application.default_branch,
                file_path,
            )
            if not source.get("sha"):
                return Response(
                    {"detail": "GitHub did not provide a source revision to pin this test candidate to."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            result = generate_test_file(source["path"], source["content"])
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        except GitHubAPIError as exc:
            response_status = exc.status_code if exc.status_code in {401, 403, 404, 429} else 502
            return Response({"detail": str(exc)}, status=response_status)
        except httpx.RequestError:
            return Response(
                {"detail": "GitHub could not be reached while retrieving the source file."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except AIConfigurationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except AIProviderError as exc:
            response_status = 429 if exc.status_code == 429 else 502
            return Response({"detail": str(exc)}, status=response_status)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        suite, _ = TestSuite.objects.get_or_create(
            application=application,
            name="AI-generated candidate tests",
            defaults={"description": "Untrusted AI-generated pytest candidates requiring explicit human approval."},
        )
        definition = {
            **result,
            "kind": "ai_generated",
            "source_file": source["path"],
            "source_sha": source["sha"],
            "source_branch": application.default_branch,
            "status": "pending_review",
        }
        candidate = TestCase.objects.create(
            suite=suite,
            name=result["title"],
            category="unit",
            definition=definition,
            approved=False,
        )
        return Response(
            {
                "id": str(candidate.id),
                "title": candidate.name,
                "rationale": definition["rationale"],
                "source_file": source["path"],
                "source_sha": source["sha"],
                "framework": definition["framework"],
                "test_code": definition["test_code"],
                "approved": False,
                "status": "pending_review",
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=("post",),
        url_path=r"ai/candidates/(?P<candidate_id>[^/.]+)/approve",
    )
    def approve_ai_candidate(self, request, pk=None, candidate_id=None):
        application = self.get_object()
        candidate = TestCase.objects.filter(
            pk=candidate_id,
            suite__application=application,
            definition__kind="ai_generated",
        ).first()
        if candidate is None:
            return Response({"detail": "AI test candidate not found."}, status=status.HTTP_404_NOT_FOUND)
        if not candidate.approved:
            candidate.approved = True
            candidate.definition["status"] = "approved"
            candidate.definition["approved_by"] = request.user.get_username()
            candidate.definition["approved_at"] = timezone.now().isoformat()
            candidate.save(update_fields=("approved", "definition", "updated_at"))
        return Response({"id": str(candidate.id), "approved": True, "status": "approved"})

    @action(
        detail=True,
        methods=("post",),
        url_path=r"ai/candidates/(?P<candidate_id>[^/.]+)/run",
    )
    def run_ai_candidate(self, request, pk=None, candidate_id=None):
        application = self.get_object()
        candidate = TestCase.objects.filter(
            pk=candidate_id,
            suite__application=application,
            definition__kind="ai_generated",
        ).first()
        if candidate is None:
            return Response({"detail": "AI test candidate not found."}, status=status.HTTP_404_NOT_FOUND)
        if not candidate.approved:
            return Response(
                {"detail": "Review and explicitly approve this candidate before execution."},
                status=status.HTTP_409_CONFLICT,
            )
        environment = application.environments.filter(name="Staging").first()
        if environment is None:
            return Response({"detail": "Register a staging environment before running tests."}, status=status.HTTP_400_BAD_REQUEST)
        run = TestRun.objects.create(
            application=application,
            environment=environment,
            suite=candidate.suite,
            initiated_by=request.user,
            status=TestRun.Status.QUEUED,
        )
        transaction.on_commit(
            lambda: execute_approved_candidate.delay(str(run.id), str(candidate.id))
        )
        return Response({"id": str(run.id), "status": run.status}, status=status.HTTP_202_ACCEPTED)

    @action(
        detail=True,
        methods=("post",),
        url_path="ai/failure-analysis",
        throttle_classes=(AIAssistantThrottle,),
    )
    def ai_failure_analysis(self, request, pk=None):
        application = self.get_object()
        if not isinstance(request.data, dict):
            return Response({"detail": "A JSON object containing failure details is required."}, status=status.HTTP_400_BAD_REQUEST)
        test_name = request.data.get("test_name", "")
        error_message = request.data.get("error_message", "")
        stack_trace = request.data.get("stack_trace", "")
        if not isinstance(test_name, str) or not test_name.strip() or len(test_name) > 500:
            return Response({"detail": "Test name is required and must be at most 500 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(error_message, str) or not error_message.strip() or len(error_message) > 8000:
            return Response({"detail": "Failure message is required and must be at most 8,000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(stack_trace, str) or len(stack_trace) > 12000:
            return Response({"detail": "Stack trace must be at most 12,000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_ai_configuration()
            result = analyze_failure(test_name.strip(), error_message, stack_trace)
        except AIConfigurationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except AIProviderError as exc:
            response_status = 429 if exc.status_code == 429 else 502
            return Response({"detail": str(exc)}, status=response_status)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        result["review_required"] = True
        return Response(result)

    @action(detail=True, methods=("post",), url_path="run-assessment")
    def run_assessment(self, request, pk=None):
        application = self.get_object()
        if not application.github_repository:
            return Response(
                {"detail": "Add a GitHub repository before starting an assessment."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        environment, _ = Environment.objects.get_or_create(
            application=application,
            name="Staging",
            defaults={
                "type": Environment.Type.STAGING,
                "base_url": application.staging_url,
                "allow_destructive_actions": False,
            },
        )
        if environment.base_url != application.staging_url:
            environment.base_url = application.staging_url
            environment.save(update_fields=("base_url", "updated_at"))
        suite, _ = TestSuite.objects.get_or_create(
            application=application,
            name="Repository and staging preflight",
            defaults={
                "description": (
                    "Read-only GitHub repository inspection and an HTTP GET availability "
                    "probe for the registered staging URL."
                )
            },
        )
        run = TestRun.objects.create(
            application=application,
            environment=environment,
            suite=suite,
            initiated_by=request.user,
            status=TestRun.Status.QUEUED,
        )
        transaction.on_commit(lambda: execute_test_run.delay(str(run.id)))
        return Response(
            {
                "id": str(run.id),
                "status": run.status,
                "created_at": run.created_at,
                "scope": "repository metadata and read-only staging availability",
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=("get",), url_path="assessment-runs")
    def assessment_runs(self, request, pk=None):
        application = self.get_object()
        runs = application.test_runs.select_related("suite").prefetch_related(
            "executions__test_case"
        ).order_by("-created_at")[:10]
        return Response([
            {
                "id": str(run.id),
                "status": run.status,
                "created_at": run.created_at,
                "suite": run.suite.name,
                "executions": [
                    {
                        "id": str(execution.id),
                        "test_name": execution.test_case.name,
                        "status": execution.status,
                        "actual": execution.actual,
                        "error_message": execution.error_message,
                    }
                    for execution in run.executions.all()
                ],
            }
            for run in runs
        ])


class ApplicationRegistrationView(APIView):
    permission_classes = (HasQAProfile,)

    def post(self, request):
        serializer = ApplicationRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = getattr(request.user, "qa_profile", None)
        if not profile or not profile.team_id:
            return Response(
                {
                    "detail": (
                        "Your account needs an organization and team before registering applications. "
                        "Ask an administrator to finish account setup."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if profile.team.organization_id != profile.organization_id:
            return Response(
                {"detail": "Your assigned team does not belong to your organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            application_data = dict(serializer.validated_data)
            application_data.pop("authorized_testing")
            application = Application.objects.create(
                **application_data,
                owner=request.user,
                team=profile.team,
                testing_permissions=(
                    "User confirmed read-only GitHub source-file access and staging HTTP GET assessment."
                ),
                approved_test_scope=[
                    "github_read_metadata",
                    "github_source_read",
                    "staging_http_get",
                ],
            )
            Environment.objects.create(
                application=application,
                name="Staging",
                type=Environment.Type.STAGING,
                base_url=application.staging_url,
                allow_destructive_actions=False,
            )
            TestSuite.objects.create(
                application=application,
                name="Repository and staging preflight",
                description=(
                    "Read-only GitHub repository inspection and an HTTP GET availability "
                    "probe for the registered staging URL."
                ),
            )
        return Response(ApplicationSerializer(application).data, status=status.HTTP_201_CREATED)


class OrganizationBootstrapView(APIView):
    permission_classes = (IsAdminUser,)

    def post(self, request):
        organization_name = str(request.data.get("organization", "")).strip()
        team_name = str(request.data.get("team", "")).strip()
        if not organization_name or not team_name:
            return Response(
                {"detail": "Organization and team names are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(organization_name) > 200 or len(team_name) > 200:
            return Response(
                {"detail": "Organization and team names must be 200 characters or fewer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = getattr(request.user, "qa_profile", None)
        if profile and profile.team_id:
            return Response(
                {"detail": "Your account already has an organization and team."},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            if profile:
                if profile.organization.name != organization_name:
                    return Response(
                        {"detail": "Organization name does not match your existing account profile."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                organization = profile.organization
                team, _ = Team.objects.get_or_create(organization=organization, name=team_name)
                profile.team = team
                profile.save(update_fields=("team", "updated_at"))
            else:
                organization, _ = Organization.objects.get_or_create(name=organization_name)
                team, _ = Team.objects.get_or_create(organization=organization, name=team_name)
                UserProfile.objects.create(
                    user=request.user,
                    organization=organization,
                    team=team,
                )
        return Response(
            {"organization": organization.name, "team": team.name},
            status=status.HTTP_201_CREATED,
        )


class CurrentUserView(APIView):
    def get(self, request):
        profile = getattr(request.user, "qa_profile", None)
        return Response({
            "username": request.user.get_username(),
            "is_admin": request.user.is_staff,
            "organization": profile.organization.name if profile else None,
            "team": profile.team.name if profile and profile.team_id else None,
            "needs_setup": profile is None or profile.team_id is None,
        })


class EnvironmentViewSet(viewsets.ModelViewSet):
    queryset = Environment.objects.select_related("application")
    serializer_class = EnvironmentSerializer


class DiscoveryRunViewSet(viewsets.ModelViewSet):
    queryset = DiscoveryRun.objects.select_related("application", "environment")
    serializer_class = DiscoveryRunSerializer


class TestSuiteViewSet(viewsets.ModelViewSet):
    queryset = TestSuite.objects.select_related("application")
    serializer_class = TestSuiteSerializer


class TestCaseViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TestCaseSerializer
    permission_classes = (HasQAProfile,)

    def get_queryset(self):
        cases = TestCase.objects.select_related("suite", "suite__application")
        user = self.request.user
        if user.is_superuser:
            return cases
        profile = getattr(user, "qa_profile", None)
        if not profile:
            return cases.none()
        return cases.filter(suite__application__team__organization=profile.organization)


class TestRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TestRunSerializer
    permission_classes = (HasQAProfile,)

    def get_queryset(self):
        runs = TestRun.objects.select_related("application", "environment", "suite", "initiated_by")
        user = self.request.user
        if user.is_superuser:
            return runs
        profile = getattr(user, "qa_profile", None)
        if not profile:
            return runs.none()
        return runs.filter(application__team__organization=profile.organization)

class TestExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TestExecution.objects.select_related("run", "test_case")
    serializer_class = TestExecutionSerializer


class QualityGateResultViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = QualityGateResult.objects.select_related("run", "policy")
    serializer_class = QualityGateResultSerializer


class DefectViewSet(viewsets.ModelViewSet):
    queryset = Defect.objects.select_related("application")
    serializer_class = DefectSerializer


class DashboardViewSet(viewsets.ViewSet):
    permission_classes = (HasQAProfile,)

    def list(self, request):
        apps = Application.objects.filter(status=Application.Status.ACTIVE)
        user = request.user
        if not user.is_superuser:
            profile = getattr(user, "qa_profile", None)
            if not profile:
                return Response({"detail": "A QA organization profile is required."}, status=403)
            apps = apps.filter(team__organization=profile.organization)
        executions = TestExecution.objects.filter(run__application__in=apps)
        defects = Defect.objects.filter(application__in=apps).exclude(status="closed")
        runs = TestRun.objects.filter(application__in=apps)
        return Response({
            "total_applications": apps.count(),
            "active_runs": runs.filter(
                status__in=(TestRun.Status.QUEUED, TestRun.Status.RUNNING)
            ).count(),
            "passed_tests": executions.filter(status="passed").count(),
            "failed_tests": executions.filter(status="failed").count(),
            "critical_defects": defects.filter(severity="critical").count(),
            "recent_runs": TestRunSerializer(runs.order_by("-created_at")[:8], many=True).data,
        })


class MonitoringView(APIView):
    permission_classes = (HasQAProfile,)

    def get(self, request):
        periods = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
        period = request.query_params.get("period", "24h")
        if period not in periods:
            return Response({"detail": "period must be one of: 24h, 7d, 30d"}, status=400)

        user = request.user
        applications = Application.objects.filter(status=Application.Status.ACTIVE)
        if not user.is_superuser:
            profile = getattr(user, "qa_profile", None)
            if not profile:
                return Response({"detail": "A QA organization profile is required."}, status=403)
            applications = applications.filter(team__organization=profile.organization)

        available_applications = list(applications.values("id", "name"))
        selected_id = request.query_params.get("application")
        if selected_id:
            applications = applications.filter(id=selected_id)
            if not applications.exists():
                return Response({"detail": "Application not found."}, status=404)

        now = timezone.now()
        since = now - periods[period]
        runs = TestRun.objects.filter(application__in=applications, created_at__gte=since)
        executions = TestExecution.objects.filter(run__in=runs)
        defects = Defect.objects.filter(application__in=applications).exclude(status="closed")
        gates = QualityGateResult.objects.filter(run__in=runs)
        reviews = ReviewDecision.objects.filter(run__in=runs)

        execution_totals = {
            row["status"]: row["total"]
            for row in executions.values("status").annotate(total=Count("id"))
        }
        passed = execution_totals.get("passed", 0)
        failed = execution_totals.get("failed", 0)
        skipped = execution_totals.get("skipped", 0)
        total = sum(execution_totals.values())

        trend_rows = (
            executions.filter(started_at__gte=since)
            .annotate(day=TruncDate("started_at"))
            .values("day", "status")
            .annotate(total=Count("id"))
            .order_by("day")
        )
        trend_lookup = {}
        for row in trend_rows:
            day = row["day"].isoformat()
            trend_lookup.setdefault(day, {"passed": 0, "failed": 0})
            if row["status"] in {"passed", "failed"}:
                trend_lookup[day][row["status"]] = row["total"]

        categories = list(
            executions.values("test_case__category")
            .annotate(total=Count("id"))
            .order_by("test_case__category")
        )
        latest_gate = gates.order_by("-created_at").first()
        latest_dora = DORAMetricSnapshot.objects.filter(
            application__in=applications, period_end__lte=timezone.localdate()
        ).order_by("-period_end").first()

        return Response({
            "generated_at": now,
            "period": period,
            "applications": available_applications,
            "summary": {
                "test_pass_rate": round(passed / total * 100, 1) if total else None,
                "passed_tests": passed,
                "failed_tests": failed,
                "skipped_tests": skipped,
                "active_runs": runs.filter(status__in=[TestRun.Status.QUEUED, TestRun.Status.RUNNING]).count(),
                "critical_defects": defects.filter(severity__iexact="critical").count(),
                "open_high_defects": defects.filter(severity__iexact="high").count(),
                "quality_gate_passed": gates.filter(decision=QualityGateResult.Decision.PASS).count(),
                "quality_gate_total": gates.count(),
                "latest_gate_decision": latest_gate.decision if latest_gate else None,
                "human_review_decisions": reviews.count(),
            },
            "execution_trend": [
                {"date": day, **counts} for day, counts in sorted(trend_lookup.items())
            ],
            "test_categories": [
                {"category": row["test_case__category"], "count": row["total"]}
                for row in categories
            ],
            "recent_runs": list(
                runs.order_by("-created_at").values(
                    "id", "status", "created_at", "suite__name"
                )[:10]
            ),
            "recent_reviews": list(
                reviews.order_by("-created_at").values(
                    "id", "decision", "created_at", "run__suite__name"
                )[:8]
            ),
            "integrations": {
                "api_latency": {"configured": False, "p50_ms": None, "p95_ms": None, "p99_ms": None, "requests_per_second": None, "error_rate": None},
                "browser": {"configured": False, "provider": "Playwright"},
                "performance": {"configured": False, "provider": "Locust"},
                "security": {"configured": False, "provider": "OWASP ZAP"},
                "ci_cd": {"configured": False, "provider": "GitHub Actions"},
                "infrastructure": {"configured": False, "provider": "Prometheus / Node Exporter"},
                "ai_assistant": {"configured": False},
            },
            "dora_metrics": latest_dora.metrics if latest_dora else None,
        })
