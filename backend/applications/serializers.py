from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from .models import *
from integrations.github.repository import parse_github_repository
from testing.integration.validators import validate_external_url


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = "__all__"


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = "__all__"


class ApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = "__all__"
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        for field in ("staging_url", "production_url", "github_repository", "openapi_url"):
            value = attrs.get(field)
            if value:
                try:
                    validate_external_url(value)
                except DjangoValidationError as exc:
                    raise serializers.ValidationError({field: exc.messages})
        return attrs


class ApplicationRegistrationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    github_repository = serializers.CharField(max_length=1000)
    default_branch = serializers.CharField(max_length=200, required=False, default="main")
    staging_url = serializers.URLField(max_length=1000)
    authorized_testing = serializers.BooleanField(write_only=True)
    technology_stack = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )

    def validate_github_repository(self, value):
        try:
            parse_github_repository(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value.rstrip("/").removesuffix(".git")

    def validate_staging_url(self, value):
        try:
            validate_external_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

    def validate_authorized_testing(self, value):
        if not value:
            raise serializers.ValidationError(
                "Confirm authorization to read repository files and run read-only checks against this staging application."
            )
        return value


class EnvironmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Environment
        fields = "__all__"


class DiscoveryRunSerializer(serializers.ModelSerializer):
    endpoints_count = serializers.IntegerField(source="endpoints.count", read_only=True)
    pages_count = serializers.IntegerField(source="pages.count", read_only=True)

    class Meta:
        model = DiscoveryRun
        fields = "__all__"


class TestSuiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestSuite
        fields = "__all__"


class TestCaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestCase
        fields = "__all__"


class TestRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestRun
        fields = "__all__"
        read_only_fields = ("initiated_by", "status")


class TestExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestExecution
        fields = "__all__"


class QualityGateResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityGateResult
        fields = "__all__"


class DefectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Defect
        fields = "__all__"
