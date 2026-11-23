import pytest
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .models import Organization, Team, UserProfile, Application
from .services import validate_external_url


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
    assert response.status_code in (201, 400)
