import httpx
from django.core.exceptions import ValidationError
from django.utils import timezone
from celery import shared_task

from ai_engine.provider import redact_secrets
from ai_engine.test_generator import (
    module_name_for_source_path,
    validate_generated_test_code,
)
from .models import (
    Application, Environment, TestCase, TestExecution, TestRun, TestSuite,
)
from integrations.github.repository import (
    GitHubAPIError,
    inspect_github_repository,
    retrieve_repository_file,
)
from testing.integration.httpx_runner import probe_staging_url
from django.conf import settings

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def execute_discovery(self, discovery_run_id):
    # Phase 2 replaces this bounded task with the OpenAPI/HTTPX discovery engine.
    return {"discovery_run_id": str(discovery_run_id), "status": "queued_for_phase_2"}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def execute_test_run(self, test_run_id):
    run = TestRun.objects.select_related("application", "environment", "suite").get(pk=test_run_id)
    if run.status in {TestRun.Status.CANCELLED, TestRun.Status.COMPLETED, TestRun.Status.FAILED}:
        return {"test_run_id": str(run.id), "status": run.status}

    run.status = TestRun.Status.RUNNING
    run.save(update_fields=("status", "updated_at"))
    application = run.application
    has_failure = False

    checks = [
        (
            "GitHub repository access",
            "repository",
            lambda: inspect_github_repository(application.github_repository, application.default_branch),
        ),
        (
            "Staging URL availability",
            "staging",
            lambda: probe_staging_url(run.environment.base_url),
        ),
    ]

    for name, category, check in checks:
        started_at = timezone.now()
        try:
            result = check()
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            result = {
                "passed": False,
                "message": f"Check failed ({type(exc).__name__}).",
                "details": {},
            }
        else:
            if category == "repository":
                passed = result.get("accessible", False)
                result = {
                    "passed": passed,
                    "message": result.get("message", "Repository check completed."),
                    "details": {
                        key: result.get(key)
                        for key in (
                            "language",
                            "languages",
                            "frameworks",
                            "testing_frameworks",
                            "test_files",
                            "tree_truncated",
                            "selected_branch",
                            "status_code",
                        )
                    },
                }
            else:
                passed = result.get("reachable", False)
                result = {
                    "passed": passed,
                    "message": result.get("error") or (
                        f"Staging returned HTTP {result.get('status_code')}."
                    ),
                    "details": {
                        "status_code": result.get("status_code"),
                        "latency_ms": result.get("latency_ms"),
                    },
                }

        test_case, _ = TestCase.objects.get_or_create(
            suite=run.suite,
            name=name,
            defaults={
                "category": category,
                "definition": {"mode": "read_only_assessment"},
                "approved": True,
            },
        )
        TestExecution.objects.create(
            run=run,
            test_case=test_case,
            status="passed" if result["passed"] else "failed",
            expected={"description": "Read-only QA preflight check"},
            actual=result["details"],
            error_message="" if result["passed"] else result["message"],
            started_at=started_at,
            ended_at=timezone.now(),
        )
        has_failure = has_failure or not result["passed"]

    run.status = TestRun.Status.FAILED if has_failure else TestRun.Status.COMPLETED
    run.save(update_fields=("status", "updated_at"))
    return {"test_run_id": str(run.id), "status": run.status}


@shared_task
def execute_approved_candidate(test_run_id, test_case_id):
    run = TestRun.objects.select_related("application", "suite").get(pk=test_run_id)
    test_case = TestCase.objects.select_related("suite").get(pk=test_case_id)
    definition = test_case.definition
    execution = TestExecution.objects.create(
        run=run,
        test_case=test_case,
        status="running",
        expected={"description": "Human-approved AI-generated pytest candidate"},
        started_at=timezone.now(),
    )
    run.status = TestRun.Status.RUNNING
    run.save(update_fields=("status", "updated_at"))

    try:
        if not test_case.approved or definition.get("kind") != "ai_generated":
            raise ValueError("Only explicitly approved AI-generated tests can run.")
        if not settings.RUNNER_URL:
            raise ValueError("The isolated Python test runner is not configured.")
        source_path = definition.get("source_file", "")
        test_code = definition.get("test_code", "")
        source = retrieve_repository_file(
            run.application.github_repository,
            definition.get("source_branch", run.application.default_branch),
            source_path,
        )
        if not source.get("sha") or source["sha"] != definition.get("source_sha"):
            raise ValueError("The source file changed after test generation; generate and review a fresh candidate.")
        validate_generated_test_code(
            test_code,
            module_name_for_source_path(source_path),
        )
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"{settings.RUNNER_URL.rstrip('/')}/run",
                json={
                    "source_path": source_path,
                    "source_code": source["content"],
                    "test_code": test_code,
                },
            )
        if response.status_code != 200:
            raise ValueError("The isolated test runner returned an error.")
        result = response.json()
        if not isinstance(result, dict) or result.get("status") not in {"passed", "failed"}:
            raise ValueError("The isolated test runner returned an invalid result.")
        execution.status = result["status"]
        execution.actual = {
            "exit_code": result.get("exit_code"),
            "output": redact_secrets(str(result.get("output", "")))[:20000],
            "source_sha": source["sha"],
        }
        execution.error_message = "" if result["status"] == "passed" else "Generated pytest candidate failed."
    except (httpx.HTTPError, GitHubAPIError, ValidationError, ValueError) as exc:
        execution.status = "failed"
        execution.actual = {"detail": redact_secrets(str(exc))[:2000]}
        execution.error_message = "Approved test could not complete safely."
    finally:
        execution.ended_at = timezone.now()
        execution.save(update_fields=("status", "actual", "error_message", "ended_at", "updated_at"))
        run.status = (
            TestRun.Status.COMPLETED
            if execution.status == "passed"
            else TestRun.Status.FAILED
        )
        run.save(update_fields=("status", "updated_at"))
    definition["status"] = execution.status
    test_case.save(update_fields=("definition", "updated_at"))
    return {
        "test_run_id": str(run.id),
        "test_case_id": str(test_case.id),
        "status": execution.status,
    }
