import httpx
from django.core.exceptions import ValidationError
from django.utils import timezone
from celery import shared_task

from .models import (
    Application, Environment, TestCase, TestExecution, TestRun, TestSuite,
)
from integrations.github.repository import inspect_github_repository
from testing.integration.httpx_runner import probe_staging_url

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
                        for key in ("language", "frameworks", "test_files", "selected_branch", "status_code")
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
