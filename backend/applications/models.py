import uuid
from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)


class Team(TimeStampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=200)

    class Meta:
        unique_together = ("organization", "name")


class Role(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)


class UserProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="qa_profile")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="members")
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="members")
    roles = models.ManyToManyField(Role, blank=True)


class Application(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_applications")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="applications")
    technology_stack = models.JSONField(default=list, blank=True)
    staging_url = models.URLField(max_length=1000)
    production_url = models.URLField(max_length=1000, blank=True)
    github_repository = models.URLField(max_length=1000, blank=True)
    default_branch = models.CharField(max_length=200, default="main")
    openapi_url = models.URLField(max_length=1000, blank=True)
    testing_permissions = models.TextField(blank=True)
    approved_test_scope = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ("name",)
        indexes = [models.Index(fields=("team", "status")), models.Index(fields=("owner",))]


class Environment(TimeStampedModel):
    class Type(models.TextChoices):
        DEVELOPMENT = "development", "Development"
        STAGING = "staging", "Staging"
        PRODUCTION = "production", "Production"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="environments")
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=20, choices=Type.choices)
    base_url = models.URLField(max_length=1000)
    allow_destructive_actions = models.BooleanField(default=False)

    class Meta:
        unique_together = ("application", "name")


class ApplicationCredential(TimeStampedModel):
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="credentials")
    name = models.CharField(max_length=100)
    secret_ciphertext = models.TextField(help_text="Encrypted secret; never store plaintext")

    class Meta:
        unique_together = ("environment", "name")


class RepositoryIntegration(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="repositories")
    repository_url = models.URLField(max_length=1000)
    default_branch = models.CharField(max_length=200, default="main")
    installation_id = models.CharField(max_length=200, blank=True)


class DiscoveryRun(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="discovery_runs")
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="discovery_runs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    error_message = models.TextField(blank=True)


class DiscoveredEndpoint(TimeStampedModel):
    discovery_run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name="endpoints")
    method = models.CharField(max_length=12)
    path = models.CharField(max_length=2000)
    parameters = models.JSONField(default=dict, blank=True)
    request_schema = models.JSONField(default=dict, blank=True)
    response_schema = models.JSONField(default=dict, blank=True)
    authentication_required = models.BooleanField(null=True)
    source = models.CharField(max_length=100)


class DiscoveredPage(TimeStampedModel):
    discovery_run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name="pages")
    url = models.URLField(max_length=2000)
    title = models.CharField(max_length=500, blank=True)
    screenshot_path = models.CharField(max_length=1000, blank=True)


class TestSuite(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="test_suites")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)


class TestCase(TimeStampedModel):
    suite = models.ForeignKey(TestSuite, on_delete=models.CASCADE, related_name="cases")
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=50)
    definition = models.JSONField(default=dict)
    approved = models.BooleanField(default=False)


class TestCaseVersion(TimeStampedModel):
    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    definition = models.JSONField(default=dict)

    class Meta:
        unique_together = ("test_case", "version")


class TestRun(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="test_runs")
    environment = models.ForeignKey(Environment, on_delete=models.PROTECT, related_name="test_runs")
    suite = models.ForeignKey(TestSuite, on_delete=models.PROTECT, related_name="test_runs")
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)


class TestExecution(TimeStampedModel):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="executions")
    test_case = models.ForeignKey(TestCase, on_delete=models.PROTECT)
    status = models.CharField(max_length=20)
    expected = models.JSONField(default=dict)
    actual = models.JSONField(default=dict)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)


class TestEvidence(TimeStampedModel):
    execution = models.ForeignKey(TestExecution, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(max_length=50)
    storage_path = models.CharField(max_length=2000)
    redacted = models.BooleanField(default=True)


class QualityPolicy(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="quality_policies")
    name = models.CharField(max_length=200)
    rules = models.JSONField(default=dict)
    active = models.BooleanField(default=True)


class QualityGateResult(TimeStampedModel):
    class Decision(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Needs Review"
        NOT_EVALUATED = "NOT_EVALUATED", "Not Evaluated"

    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="gate_results")
    policy = models.ForeignKey(QualityPolicy, on_delete=models.PROTECT)
    decision = models.CharField(max_length=20, choices=Decision.choices)
    evidence = models.JSONField(default=dict)


class Defect(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="defects")
    title = models.CharField(max_length=300)
    severity = models.CharField(max_length=20)
    status = models.CharField(max_length=30, default="open")
    details = models.TextField(blank=True)


class ReviewDecision(TimeStampedModel):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=30)
    notes = models.TextField(blank=True)


class Deployment(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="deployments")
    environment = models.ForeignKey(Environment, on_delete=models.PROTECT)
    commit_sha = models.CharField(max_length=100)
    deployed_at = models.DateTimeField()


class Incident(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="incidents")
    title = models.CharField(max_length=300)
    severity = models.CharField(max_length=20)
    occurred_at = models.DateTimeField()


class DORAMetricSnapshot(TimeStampedModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="dora_snapshots")
    period_start = models.DateField()
    period_end = models.DateField()
    metrics = models.JSONField(default=dict)


class AuditLog(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.UUIDField()
    changes = models.JSONField(default=dict)
