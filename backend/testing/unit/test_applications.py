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
from integrations.github.repository import parse_github_repository
from testing.integration.validators import validate_external_url
from applications.tasks import execute_test_run


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
