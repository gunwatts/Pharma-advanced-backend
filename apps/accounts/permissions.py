"""Role-based permission checks and server-side unit/department data scoping."""

from django.db.models import Q
from rest_framework.permissions import BasePermission

from apps.accounts.models import PERMISSIONS


def user_permissions(user):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return set(PERMISSIONS)
    profile = getattr(user, "profile", None)
    if profile is None or profile.role is None:
        return set()
    return {p for p in profile.role.permissions if p in PERMISSIONS}


def has_perm(user, perm):
    return perm in user_permissions(user)


def has_full_scope(user):
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.all_data_scope)


def scope_q(user, unit_field="unit", department_field="department"):
    """Q object restricting a queryset to the user's unit/department scope.

    Returns ``None`` for full scope. A user with no scope rows sees nothing.
    """
    if has_full_scope(user):
        return None
    clauses = Q(pk__in=[])
    for scope in user.data_scopes.all():
        clause = Q()
        if scope.unit_id:
            clause &= Q(**{f"{unit_field}_id": scope.unit_id})
        if scope.department_id:
            clause &= Q(**{f"{department_field}_id": scope.department_id})
        clauses |= clause
    return clauses


def scope_queryset(qs, user, unit_field="unit", department_field="department"):
    q = scope_q(user, unit_field, department_field)
    return qs if q is None else qs.filter(q)


def in_scope(user, unit_id, department_id):
    if has_full_scope(user):
        return True
    for scope in user.data_scopes.all():
        if (scope.unit_id is None or scope.unit_id == unit_id) and (scope.department_id is None or scope.department_id == department_id):
            return True
    return False


class HasPermission(BasePermission):
    """Usage: permission_classes = [HasPermission.of("admin.configure")]."""

    required = None

    @classmethod
    def of(cls, *perms):
        return type(f"HasPermission_{'_'.join(p.replace('.', '_') for p in perms)}", (cls,), {"required": perms})

    def has_permission(self, request, view):
        perms = user_permissions(request.user)
        return bool(request.user and request.user.is_authenticated and all(p in perms for p in (self.required or ())))


class ReadOrConfigure(BasePermission):
    """Reads need ``read_perm`` (default: any authenticated); writes need admin.configure."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in ("GET", "HEAD", "OPTIONS"):
            read_perm = getattr(view, "read_permission", None)
            return read_perm is None or has_perm(request.user, read_perm)
        return has_perm(request.user, getattr(view, "write_permission", "admin.configure"))
