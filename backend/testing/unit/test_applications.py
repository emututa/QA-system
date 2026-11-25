import base64
import json
from types import SimpleNamespace

import pytest
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from applications.models import (
    Application, Defect, Organization, Team, TestCase as QATestCase,
    TestExecution as QATestExecution, TestRun as QATestRun,
    TestSuite as QATestSuite, UserProfile, Environment,
)
from integrations.github.repository import (
    inspect_github_repository,
    parse_github_repository,
    retrieve_repository_file,
)
from testing.integration.validators import validate_external_url
from applications.tasks import execute_test_run
from ai_engine.failure_analysis import analyze_failure
from ai_engine.provider import (
    AIConfigurationError,
    AIProviderError,
    redact_secrets,
    request_json,
    validate_ai_configuration,
)
from ai_engine.test_generator import (
    generate_test_file,
    module_name_for_source_path,
    suggest_tests,
    validate_generated_test_code,
)


@pytest.mark.django_db
def test_url_validator_blocks_loopback():
    with pytest.raises(ValidationError):
        validate_external_url("http://127.0.0.1/admin")


@pytest.mark.django_db
def test_application_registration_for_profile_user():
    user = get_user_model().objects.create_user(username="qa", password="test-pass")
    organization = Organization.objects.create(name="Zimworx")
    team = Team.objects.create(organization=organization, name="Platform")
    UserProfile.objects.create(user=user, organization=organization, team=team)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post("/api/applications/", {
        "name": "Customer Portal",
        "description": "Public staging application",
        "owner": user.id,
        "team": str(team.id),
        "technology_stack": ["Django", "React"],
        "staging_url": "https://example.com",
        "status": "active",
    }, format="json")
    assert response.status_code == 405


@pytest.mark.django_db
def test_monitoring_reports_real_organization_test_data():
    user = get_user_model().objects.create_user(username="monitor", password="test-pass")
    organization = Organization.objects.create(name="Monitoring Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Staging App",
        owner=user,
        team=team,
        staging_url="https://example.com",
    )
    suite = QATestSuite.objects.create(application=application, name="Smoke suite")
    test_case = QATestCase.objects.create(
        suite=suite,
        name="API responds",
        category="api",
        approved=True,
    )
    run = QATestRun.objects.create(
        application=application,
        environment=application.environments.create(
            name="Staging",
            type="staging",
            base_url="https://example.com",
        ),
        suite=suite,
        initiated_by=user,
        status=QATestRun.Status.COMPLETED,
    )
    QATestExecution.objects.create(
        run=run,
        test_case=test_case,
        status="passed",
        started_at=timezone.now(),
    )
    Defect.objects.create(
        application=application,
        title="Open critical defect",
        severity="critical",
    )

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/monitoring/?period=24h")

    assert response.status_code == 200
    assert response.data["summary"]["test_pass_rate"] == 100
    assert response.data["summary"]["passed_tests"] == 1
    assert response.data["summary"]["critical_defects"] == 1
    assert response.data["applications"][0]["id"] == application.id
    assert response.data["integrations"]["browser"]["configured"] is False
    assert response.data["integrations"]["api_latency"]["p95_ms"] is None


@pytest.mark.django_db
def test_monitoring_rejects_unsupported_period():
    user = get_user_model().objects.create_user(username="monitor-invalid", password="test-pass")
    organization = Organization.objects.create(name="Period Org")
    UserProfile.objects.create(user=user, organization=organization)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/monitoring/?period=all-time")

    assert response.status_code == 400


@pytest.mark.django_db
def test_monitoring_allows_superuser_without_profile():
    user = get_user_model().objects.create_superuser(
        username="monitor-admin",
        email="monitor-admin@example.com",
        password="test-pass",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/monitoring/")

    assert response.status_code == 200
    assert response.data["applications"] == []


@pytest.mark.django_db
def test_admin_can_complete_first_organization_setup():
    user = get_user_model().objects.create_superuser(
        username="bootstrap-admin",
        email="bootstrap@example.com",
        password="test-pass",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/organizations/bootstrap/",
        {"organization": "QA Org", "team": "Platform"},
        format="json",
    )

    assert response.status_code == 201
    user.refresh_from_db()
    assert user.qa_profile.organization.name == "QA Org"
    assert user.qa_profile.team.name == "Platform"


def test_github_repository_url_is_restricted_to_github():
    assert parse_github_repository("https://github.com/acme/training.git") == (
        "acme",
        "training",
    )
    with pytest.raises(ValidationError):
        parse_github_repository("https://example.com/acme/training")


@pytest.mark.django_db
def test_registration_creates_application_and_staging_scope():
    user = get_user_model().objects.create_user(username="register", password="test-pass")
    organization = Organization.objects.create(name="Registration Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("applications.serializers.validate_external_url", return_value="https://staging.example.com"):
        response = client.post(
            "/api/applications/register/",
            {
                "name": "RCM Training Platform",
                "github_repository": "https://github.com/acme/training",
                "default_branch": "staging",
                "staging_url": "https://staging.example.com",
                "authorized_testing": True,
                "technology_stack": ["Django"],
            },
            format="json",
        )

    assert response.status_code == 201
    application = Application.objects.get(id=response.data["id"])
    assert application.owner == user
    assert application.team == team
    assert application.environments.get(name="Staging").allow_destructive_actions is False
    assert application.test_suites.get(name="Repository and staging preflight")
    assert "github_source_read" in application.approved_test_scope


@pytest.mark.django_db
def test_registration_requires_explicit_testing_authorization():
    user = get_user_model().objects.create_user(username="no-consent", password="test-pass")
    organization = Organization.objects.create(name="Consent Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("applications.serializers.validate_external_url", return_value="https://staging.example.com"):
        response = client.post(
            "/api/applications/register/",
            {
                "name": "Unauthorized App",
                "github_repository": "https://github.com/acme/training",
                "staging_url": "https://staging.example.com",
                "authorized_testing": False,
            },
            format="json",
        )

    assert response.status_code == 400
    assert not Application.objects.filter(name="Unauthorized App").exists()


@pytest.mark.django_db(transaction=True)
def test_application_assessment_queues_a_scoped_run():
    user = get_user_model().objects.create_user(username="queue-run", password="test-pass")
    organization = Organization.objects.create(name="Queue Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Queue assessment",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        staging_url="https://staging.example.com",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("applications.views.execute_test_run.delay") as enqueue:
        response = client.post(f"/api/applications/{application.id}/run-assessment/")

    assert response.status_code == 202
    run = QATestRun.objects.get(id=response.data["id"])
    assert run.application == application
    assert run.initiated_by == user
    enqueue.assert_called_once_with(str(run.id))


@pytest.mark.django_db
def test_assessment_checks_github_metadata_and_staging_without_running_repo_code():
    user = get_user_model().objects.create_user(username="runner", password="test-pass")
    organization = Organization.objects.create(name="Runner Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Safe assessment",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        staging_url="https://staging.example.com",
    )
    environment = Environment.objects.create(
        application=application,
        name="Staging",
        type=Environment.Type.STAGING,
        base_url=application.staging_url,
    )
    suite = QATestSuite.objects.create(application=application, name="Repository and staging preflight")
    run = QATestRun.objects.create(
        application=application,
        environment=environment,
        suite=suite,
        initiated_by=user,
    )

    with (
        patch(
            "applications.tasks.inspect_github_repository",
            return_value={
                "accessible": True,
                "language": "Python",
                "frameworks": ["Python", "Pytest"],
                "test_files": ["test_api.py"],
                "selected_branch": "main",
                "status_code": 200,
                "message": "Repository metadata inspected; code not executed.",
            },
        ),
        patch(
            "applications.tasks.probe_staging_url",
            return_value={"reachable": True, "status_code": 200, "latency_ms": 14.2, "error": ""},
        ),
    ):
        result = execute_test_run.run(str(run.id))

    run.refresh_from_db()
    assert result["status"] == QATestRun.Status.COMPLETED
    assert run.executions.filter(status="passed").count() == 2
    assert run.executions.filter(test_case__name="GitHub repository access").exists()
    assert run.executions.filter(test_case__name="Staging URL availability").exists()


def test_github_inspection_detects_nested_languages_frameworks_and_test_files():
    responses = [
        SimpleNamespace(status_code=200, json=lambda: {"default_branch": "main", "language": "Python"}),
        SimpleNamespace(
            status_code=200,
            json=lambda: {"commit": {"commit": {"tree": {"sha": "tree-sha"}}}},
        ),
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "truncated": False,
                "tree": [
                    {"type": "blob", "path": "backend/conftest.py"},
                    {"type": "blob", "path": "backend/tests/test_api.py"},
                    {"type": "blob", "path": "frontend/playwright.config.ts"},
                    {"type": "blob", "path": "frontend/tests/login.spec.ts"},
                    {"type": "blob", "path": "src/App.tsx"},
                ],
            },
        ),
        SimpleNamespace(status_code=200, json=lambda: {"Python": 2000, "TypeScript": 1000}),
    ]
    with patch("integrations.github.repository.GitHubClient") as github_client:
        github_client.return_value.get_repository_resource.side_effect = responses

        result = inspect_github_repository("https://github.com/acme/training", "main")

    assert result["accessible"] is True
    assert result["languages"] == ["Python", "TypeScript"]
    assert "Pytest" in result["testing_frameworks"]
    assert "Playwright" in result["testing_frameworks"]
    assert result["test_files"] == [
        "backend/tests/test_api.py",
        "frontend/tests/login.spec.ts",
    ]
    assert github_client.return_value.get_repository_resource.call_count == 4


def test_github_file_retrieval_decodes_small_utf8_source_file():
    content = "def test_health():\n    assert True\n"
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "type": "file",
            "path": "tests/test_health.py",
            "sha": "source-sha",
            "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        },
    )
    with patch("integrations.github.repository.GitHubClient") as github_client:
        github_client.return_value.get_repository_resource.return_value = response

        result = retrieve_repository_file(
            "https://github.com/acme/training",
            "main",
            "tests/test_health.py",
        )

    assert result == {
        "path": "tests/test_health.py",
        "sha": "source-sha",
        "size_bytes": len(content.encode()),
        "content": content,
    }


@pytest.mark.parametrize("file_path", ["../secrets.txt", "/etc/passwd", "tests\\test.py", ""])
def test_github_file_retrieval_rejects_non_repository_paths(file_path):
    with pytest.raises(ValidationError):
        retrieve_repository_file("https://github.com/acme/training", "main", file_path)


@pytest.mark.django_db
def test_repository_file_api_requires_explicit_source_read_scope():
    user = get_user_model().objects.create_user(username="source-read-denied", password="pw")
    organization = Organization.objects.create(name="Source Read Denied Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="No source consent",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        default_branch="main",
        staging_url="https://example.com",
        approved_test_scope=["github_read_metadata"],
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("applications.views.retrieve_repository_file") as retrieve:
        response = client.get(
            f"/api/applications/{application.id}/repository-file/?path=README.md"
        )

    assert response.status_code == 403
    retrieve.assert_not_called()


@pytest.mark.django_db
def test_repository_file_api_returns_file_for_explicitly_authorized_application():
    user = get_user_model().objects.create_user(username="source-read", password="pw")
    organization = Organization.objects.create(name="Source Read Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Source read app",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        default_branch="main",
        staging_url="https://example.com",
        approved_test_scope=["github_read_metadata", "github_source_read"],
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with patch(
        "applications.views.retrieve_repository_file",
        return_value={"path": "README.md", "sha": "sha", "size_bytes": 4, "content": "test"},
    ) as retrieve:
        response = client.get(
            f"/api/applications/{application.id}/repository-file/?path=README.md"
        )

    assert response.status_code == 200
    assert response.data["content"] == "test"
    retrieve.assert_called_once_with(
        "https://github.com/acme/training",
        "main",
        "README.md",
    )


def test_ai_provider_posts_json_response_request_and_redacts_secrets(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"output_text": json.dumps({"ok": True})},
    )
    with patch("ai_engine.provider.httpx.Client") as http_client:
        http_client.return_value.__enter__.return_value.post.return_value = response

        result = request_json(
            instructions="test instructions",
            prompt="API_KEY = 'do-not-send-this-secret'\nprint('safe')",
        )

    assert result == {"ok": True}
    request = http_client.return_value.__enter__.return_value.post.call_args
    assert request.args[0] == "https://api.openai.com/v1/responses"
    assert request.kwargs["headers"]["Authorization"] == "Bearer unit-test-key"
    assert request.kwargs["json"]["store"] is False
    assert "do-not-send-this-secret" not in request.kwargs["json"]["input"]
    assert "[REDACTED]" in request.kwargs["json"]["input"]


def test_ai_provider_rejects_missing_key_before_network_call(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    with patch("ai_engine.provider.httpx.Client") as http_client:
        with pytest.raises(AIConfigurationError):
            validate_ai_configuration()
        http_client.assert_not_called()


def test_ai_provider_sanitizes_unauthorized_error(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    response = SimpleNamespace(status_code=401)
    with patch("ai_engine.provider.httpx.Client") as http_client:
        http_client.return_value.__enter__.return_value.post.return_value = response

        with pytest.raises(AIProviderError) as error:
            request_json(instructions="test", prompt="test")

    assert error.value.status_code == 401
    assert "unit-test-key" not in str(error.value)


def test_ai_secret_redaction_handles_common_key_formats():
    source = (
        "const API_KEY = 'secret-value'\n"
        "Authorization: Bearer abc.def.token\n"
        "OPENAI_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456\n"
    )
    redacted = redact_secrets(source)

    assert "secret-value" not in redacted
    assert "abc.def.token" not in redacted
    assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in redacted


def test_test_generator_returns_review_only_suggestions():
    with patch(
        "ai_engine.test_generator.request_json",
        return_value={
            "suggestions": [
                {"title": "Reject missing name", "rationale": "Name is required.", "test_outline": "Submit no name."}
            ]
        },
    ):
        result = suggest_tests("src/app.py", "def create(name): return name")

    assert result["review_required"] is True
    assert result["code_executed"] is False
    assert result["suggestions"][0]["title"] == "Reject missing name"


def test_generated_test_code_validation_accepts_safe_pytest_candidate():
    code = (
        "import pytest\n"
        "from src.app import normalize\n\n"
        "@pytest.mark.parametrize('value, expected', [(' A ', 'a')])\n"
        "def test_normalize(value, expected):\n"
        "    assert normalize(value) == expected\n"
    )

    assert validate_generated_test_code(code, "src.app") == code


@pytest.mark.parametrize(
    "code",
    [
        "import os\ndef test_runs(): pass\n",
        "import pytest\ndef test_runs():\n    open('/tmp/x', 'w')\n",
        "import pytest\ndef test_runs(): pass\nvalue = pytest.main([])\n",
        "import pytest\n@pytest.mark.parametrize('x', evil())\ndef test_runs(x): pass\n",
        "import pytest\ndef test_runs():\n    import subprocess\n",
        "import pytest\ndef test_runs():\n    return ().__class__\n",
        "import pytest\ndef test_runs(:\n    pass\n",
    ],
)
def test_generated_test_code_validation_rejects_unsafe_or_invalid_code(code):
    with pytest.raises(ValueError):
        validate_generated_test_code(code, "src.app")


@pytest.mark.parametrize(
    "path",
    ["../secret.py", "/root.py", "src/not-python.js", "src/invalid-name.py"],
)
def test_generated_test_path_must_map_to_python_module(path):
    with pytest.raises(ValueError):
        module_name_for_source_path(path)


def test_generate_test_file_validates_model_output():
    with patch(
        "ai_engine.test_generator.request_json",
        return_value={
            "title": "Normalizes values",
            "rationale": "The function lowercases input.",
            "test_code": "from src.app import normalize\n\ndef test_normalize():\n    assert normalize('A') == 'a'\n",
        },
    ):
        result = generate_test_file("src/app.py", "def normalize(value): return value.lower()")

    assert result["framework"] == "pytest"
    assert result["review_required"] is True
    assert result["code_executed"] is False
    assert "test_normalize" in result["test_code"]


def test_generate_test_file_rejects_malformed_model_output():
    with patch("ai_engine.test_generator.request_json", return_value={"title": "Missing test code"}):
        with pytest.raises(ValueError, match="non-empty title"):
            generate_test_file("src/app.py", "def app(): pass")


def test_failure_analyzer_returns_bounded_human_review_guidance():
    with patch(
        "ai_engine.failure_analysis.request_json",
        return_value={
            "summary": "The request timed out.",
            "likely_causes": ["The service may be unavailable."],
            "next_steps": ["Check service health."],
            "confidence": "medium",
        },
    ):
        result = analyze_failure("API smoke", "Read timed out")

    assert result["confidence"] == "medium"
    assert result["likely_causes"] == ["The service may be unavailable."]


@pytest.mark.django_db
def test_ai_suggestions_endpoint_requires_source_read_consent():
    user = get_user_model().objects.create_user(username="ai-no-source-consent", password="pw")
    organization = Organization.objects.create(name="AI Consent Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="AI no consent",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        staging_url="https://example.com",
        approved_test_scope=["github_read_metadata"],
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with patch("applications.views.suggest_tests") as suggest:
        response = client.post(
            f"/api/applications/{application.id}/ai/test-suggestions/",
            {"path": "src/app.py"},
            format="json",
        )

    assert response.status_code == 403
    suggest.assert_not_called()


@pytest.mark.django_db
def test_ai_suggestions_endpoint_retrieves_source_and_returns_proposal():
    user = get_user_model().objects.create_user(username="ai-source-consent", password="pw")
    organization = Organization.objects.create(name="AI Source Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="AI source",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        default_branch="main",
        staging_url="https://example.com",
        approved_test_scope=["github_source_read"],
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with (
        patch("applications.views.validate_ai_configuration"),
        patch(
            "applications.views.retrieve_repository_file",
            return_value={"path": "src/app.py", "size_bytes": 100, "content": "def app(): pass"},
        ) as retrieve,
        patch(
            "applications.views.suggest_tests",
            return_value={"suggestions": [], "review_required": True, "code_executed": False},
        ),
    ):
        response = client.post(
            f"/api/applications/{application.id}/ai/test-suggestions/",
            {"path": "src/app.py"},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["review_required"] is True
    assert response.data["source_file"] == "src/app.py"
    retrieve.assert_called_once_with(
        "https://github.com/acme/training",
        "main",
        "src/app.py",
    )


@pytest.mark.django_db
def test_ai_failure_analysis_endpoint_returns_human_review_result():
    user = get_user_model().objects.create_user(username="ai-failure", password="pw")
    organization = Organization.objects.create(name="AI Failure Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="AI failure",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        staging_url="https://example.com",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with (
        patch("applications.views.validate_ai_configuration"),
        patch(
            "applications.views.analyze_failure",
            return_value={
                "summary": "Likely a timeout.",
                "likely_causes": ["Slow response."],
                "next_steps": ["Check logs."],
                "confidence": "medium",
            },
        ) as analyze,
    ):
        response = client.post(
            f"/api/applications/{application.id}/ai/failure-analysis/",
            {"test_name": "API check", "error_message": "Timed out"},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["review_required"] is True
    assert response.data["summary"] == "Likely a timeout."
    analyze.assert_called_once_with("API check", "Timed out", "")


@pytest.mark.django_db
def test_ai_provider_check_requires_a_valid_model_response():
    user = get_user_model().objects.create_user(username="ai-provider-check", password="qa-password")
    organization = Organization.objects.create(name="AI Provider Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Provider check",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        staging_url="https://example.com",
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with (
        patch("applications.views.validate_ai_configuration", return_value=("not-a-real-key", "test-model")),
        patch("applications.views.request_json", return_value={"ok": True}) as request,
    ):
        response = client.post(
            f"/api/applications/{application.id}/ai/provider-check/",
            {},
            format="json",
        )

    assert response.status_code == 200
    assert response.data == {"connected": True, "provider": "openai", "model": "test-model"}
    request.assert_called_once()


@pytest.mark.django_db
def test_generated_candidate_is_saved_for_human_review():
    user = get_user_model().objects.create_user(username="ai-generate", password="qa-password")
    organization = Organization.objects.create(name="AI Generate Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Generate candidate",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        default_branch="main",
        staging_url="https://example.com",
        approved_test_scope=["github_source_read"],
    )
    client = APIClient()
    client.force_authenticate(user=user)

    with (
        patch("applications.views.validate_ai_configuration"),
        patch(
            "applications.views.retrieve_repository_file",
            return_value={
                "path": "src/app.py",
                "sha": "source-revision-1",
                "size_bytes": 100,
                "content": "def normalize(value): return value.lower()",
            },
        ),
        patch(
            "applications.views.generate_test_file",
            return_value={
                "title": "Normalizes values",
                "rationale": "Checks lowercasing.",
                "test_code": "from src.app import normalize\n\ndef test_normalize():\n    assert normalize('A') == 'a'\n",
                "framework": "pytest",
                "review_required": True,
                "code_executed": False,
            },
        ),
    ):
        response = client.post(
            f"/api/applications/{application.id}/ai/generate-test/",
            {"path": "src/app.py"},
            format="json",
        )

    candidate = QATestCase.objects.get(id=response.data["id"])
    assert response.status_code == 201
    assert candidate.approved is False
    assert candidate.definition["source_sha"] == "source-revision-1"
    assert candidate.definition["status"] == "pending_review"


@pytest.mark.django_db
def test_ai_candidate_must_be_approved_before_execution_is_queued():
    user = get_user_model().objects.create_user(username="ai-approve", password="qa-password")
    organization = Organization.objects.create(name="AI Approval Org")
    team = Team.objects.create(organization=organization, name="QA")
    UserProfile.objects.create(user=user, organization=organization, team=team)
    application = Application.objects.create(
        name="Candidate approval",
        owner=user,
        team=team,
        github_repository="https://github.com/acme/training",
        staging_url="https://example.com",
    )
    suite = QATestSuite.objects.create(application=application, name="AI-generated candidate tests")
    candidate = QATestCase.objects.create(
        suite=suite,
        name="Candidate",
        category="unit",
        definition={"kind": "ai_generated", "status": "pending_review"},
    )
    client = APIClient()
    client.force_authenticate(user=user)

    rejected = client.post(
        f"/api/applications/{application.id}/ai/candidates/{candidate.id}/run/",
        {},
        format="json",
    )
    assert rejected.status_code == 409

    approved = client.post(
        f"/api/applications/{application.id}/ai/candidates/{candidate.id}/approve/",
        {},
        format="json",
    )
    assert approved.status_code == 200
    candidate.refresh_from_db()
    assert candidate.approved is True
    assert candidate.definition["status"] == "approved"
