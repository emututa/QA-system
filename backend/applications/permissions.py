from rest_framework.permissions import BasePermission


class HasQAProfile(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and (
            request.user.is_superuser or hasattr(request.user, "qa_profile")
        )


class IsApplicationOwnerOrQA(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        application = obj if hasattr(obj, "team") else getattr(obj, "application", None)
        if not application:
            return False
        if application.owner_id == request.user.id or request.user.is_staff:
            return True
        profile = getattr(request.user, "qa_profile", None)
        return bool(profile and application.team.organization_id == profile.organization_id)
