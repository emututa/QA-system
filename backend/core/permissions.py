from rest_framework.permissions import BasePermission


class HasQAProfile(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, "qa_profile")


class IsApplicationOwnerOrQA(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True
        application = obj if hasattr(obj, "team") else getattr(obj, "application", None)
        return bool(application and (application.owner_id == request.user.id or getattr(request.user, "is_staff", False)))
