from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import *
from .permissions import HasQAProfile, IsApplicationOwnerOrQA
from .serializers import *


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = (HasQAProfile,)


class ApplicationViewSet(viewsets.ModelViewSet):
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


class EnvironmentViewSet(viewsets.ModelViewSet):
    queryset = Environment.objects.select_related("application")
    serializer_class = EnvironmentSerializer


class DiscoveryRunViewSet(viewsets.ModelViewSet):
    queryset = DiscoveryRun.objects.select_related("application", "environment")
    serializer_class = DiscoveryRunSerializer


class TestSuiteViewSet(viewsets.ModelViewSet):
    queryset = TestSuite.objects.select_related("application")
    serializer_class = TestSuiteSerializer


class TestCaseViewSet(viewsets.ModelViewSet):
    queryset = TestCase.objects.select_related("suite")
    serializer_class = TestCaseSerializer


class TestRunViewSet(viewsets.ModelViewSet):
    queryset = TestRun.objects.select_related("application", "environment", "suite", "initiated_by")
    serializer_class = TestRunSerializer

    def perform_create(self, serializer):
        serializer.save(initiated_by=self.request.user)


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
        executions = TestExecution.objects.all()
        defects = Defect.objects.exclude(status="closed")
        runs = TestRun.objects.all()
        return Response({
            "total_applications": apps.count(),
            "active_runs": runs.filter(status=TestRun.Status.RUNNING).count(),
            "passed_tests": executions.filter(status="passed").count(),
            "failed_tests": executions.filter(status="failed").count(),
            "critical_defects": defects.filter(severity="critical").count(),
            "recent_runs": TestRunSerializer(runs.order_by("-created_at")[:8], many=True).data,
        })
