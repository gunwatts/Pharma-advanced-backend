from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from apps.accounts.models import PERMISSIONS, Role, UserProfile, UserScope
from apps.accounts.permissions import has_full_scope, user_permissions
from apps.dashboards.models import Dashboard
from apps.dashboards.services.access import default_dashboard_id

User = get_user_model()


class RoleSerializer(serializers.ModelSerializer):
    user_count = serializers.IntegerField(source="users.count", read_only=True)

    class Meta:
        model = Role
        fields = ["id", "key", "name", "description", "permissions", "is_system", "default_dashboard", "user_count"]
        read_only_fields = ["is_system"]

    def validate_permissions(self, value):
        unknown = [p for p in value if p not in PERMISSIONS]
        if unknown:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(unknown)}")
        return sorted(set(value))


class ScopeSerializer(serializers.ModelSerializer):
    unit_name = serializers.CharField(source="unit.name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = UserScope
        fields = ["unit", "department", "unit_name", "department_name"]


class UserSerializer(serializers.ModelSerializer):
    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.all(), source="profile.role", allow_null=True, required=False)
    all_data_scope = serializers.BooleanField(source="profile.all_data_scope", required=False)
    default_dashboard = serializers.PrimaryKeyRelatedField(source="profile.default_dashboard", allow_null=True, required=False,
                                                           queryset=Dashboard.objects.all())
    scopes = ScopeSerializer(many=True, source="data_scopes", required=False)
    password = serializers.CharField(write_only=True, required=False, allow_blank=False, style={"input_type": "password"})
    role_name = serializers.CharField(source="profile.role.name", read_only=True, default=None)

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "is_active", "is_superuser", "last_login",
                  "role", "role_name", "all_data_scope", "default_dashboard", "scopes", "password"]
        read_only_fields = ["is_superuser", "last_login"]

    def validate(self, attrs):
        password = attrs.get("password")
        if self.instance is None and not password:
            raise serializers.ValidationError({"password": "A password is required for new users."})
        if password:
            candidate = self.instance or User(username=attrs.get("username", ""), email=attrs.get("email", ""))
            validate_password(password, user=candidate)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        return self._save(User(), validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        return self._save(instance, validated_data)

    def _save(self, user, data):
        profile_data = data.pop("profile", {})
        scopes = data.pop("data_scopes", None)
        password = data.pop("password", None)
        for key, value in data.items():
            setattr(user, key, value)
        if password:
            user.set_password(password)
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        for key, value in profile_data.items():
            setattr(profile, key, value)
        profile.save()
        if scopes is not None:
            user.data_scopes.all().delete()
            seen = set()
            for scope in scopes:
                key = (getattr(scope.get("unit"), "pk", None), getattr(scope.get("department"), "pk", None))
                if key not in seen:
                    seen.add(key)
                    UserScope.objects.create(user=user, unit=scope.get("unit"), department=scope.get("department"))
        return user


def me_payload(user):
    profile = getattr(user, "profile", None)
    return {
        "id": user.pk,
        "username": user.username,
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "role": profile.role.name if profile and profile.role else ("Superuser" if user.is_superuser else None),
        "permissions": sorted(user_permissions(user)),
        "all_data_scope": has_full_scope(user),
        "scopes": ScopeSerializer(user.data_scopes.select_related("unit", "department"), many=True).data,
        "default_dashboard_id": default_dashboard_id(user),
    }
