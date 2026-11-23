from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("applications", ApplicationViewSet, basename="application")
router.register("environments", EnvironmentViewSet, basename="environment")
router.register("discoveries", DiscoveryRunViewSet, basename="discovery")
router.register("test-suites", TestSuiteViewSet, basename="test-suite")
router.register("test-cases", TestCaseViewSet, basename="test-case")
router.register("test-runs", TestRunViewSet, basename="test-run")
router.register("test-executions", TestExecutionViewSet, basename="test-execution")
router.register("quality-gates", QualityGateResultViewSet, basename="quality-gate")
router.register("defects", DefectViewSet, basename="defect")
router.register("dashboard", DashboardViewSet, basename="dashboard")

urlpatterns = [path("", include(router.urls))]
