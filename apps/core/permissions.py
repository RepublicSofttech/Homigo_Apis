from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsCustomer(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.user_type == "customer"


class IsPartner(BasePermission):
    message = "Partner account required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.user_type == "partner"


class IsCoworker(BasePermission):
    message = "Coworker account required."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.user_type == "coworker"


class IsPlatformAdmin(BasePermission):
    message = "Admin access required."

    def has_permission(self, request, view):
        u = request.user
        return u.is_authenticated and (u.user_type == "admin" or u.is_superuser)


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        u = request.user
        return u.is_authenticated and (u.user_type == "admin" or u.is_superuser)
