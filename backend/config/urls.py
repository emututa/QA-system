from django.http import JsonResponse
from django.urls import include, path
from django.http import HttpResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


def health(request):
    return JsonResponse({"status": "healthy", "service": "zimworx-qa-api"})


def metrics(request):
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


urlpatterns = [
    path("api/health/", health),
    path("metrics", metrics),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/", include("applications.urls")),
]
