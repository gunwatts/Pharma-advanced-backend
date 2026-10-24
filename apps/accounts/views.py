from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts import lockout
from apps.accounts.models import PERMISSIONS, AuthToken, Role
from apps.accounts.serializers import RoleSerializer, UserSerializer, me_payload
from apps.core import audit
from apps.core.viewsets import AuditedModelViewSet

User = get_user_model()
GENERIC_LOGIN_ERROR = "Invalid username or password."


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"].strip()
        if lockout.is_locked(username):
            return Response({"detail": "Too many failed attempts. Try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        user = authenticate(request, username=username, password=serializer.validated_data["password"])
        if user is None or not user.is_active:
            count = lockout.register_failure(username)
            audit.record("login_failed", object_type="auth.user", object_repr=username[:150], changes={"failures": count})
            return Response({"detail": GENERIC_LOGIN_ERROR}, status=status.HTTP_401_UNAUTHORIZED)
        lockout.reset(username)
        raw, token = AuthToken.issue(user)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        audit.record("login", actor=user, obj=user)
        return Response({"token": raw, "expires_at": token.expires_at, "user": me_payload(user)})


class LogoutView(APIView):
    def post(self, request):
        token = getattr(request, "auth_token_obj", None)
        if token is not None:
            token.delete()
        audit.record("logout", obj=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(me_payload(request.user))


class ChangePasswordView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "sensitive"

    def post(self, request):
        current = request.data.get("current_password", "")
        new = request.data.get("new_password", "")
        if not request.user.check_password(current):
            return Response({"current_password": ["Current password is incorrect."]}, status=400)
        try:
            validate_password(new, user=request.user)
        except ValidationError as exc:
            return Response({"new_password": exc.messages}, status=400)
        request.user.set_password(new)
        request.user.save()
        AuthToken.objects.filter(user=request.user).exclude(pk=getattr(request.auth_token_obj, "pk", None)).delete()
        audit.record("password_changed", obj=request.user)
        return Response({"detail": "Password changed."})


class UserViewSet(AuditedModelViewSet):
    serializer_class = UserSerializer
    read_permission = "admin.users"
    write_permission = "admin.users"
    bumps_config_version = False

    def get_queryset(self):
        return User.objects.select_related("profile__role").prefetch_related("data_scopes__unit", "data_scopes__department").order_by("username")

    def perform_update(self, serializer):
        before = audit.snapshot(serializer.instance)
        user = serializer.save()
        if "password" in self.request.data or not user.is_active:
            AuthToken.objects.filter(user=user).delete()  # force re-login after admin password reset/deactivation
        audit.record("permission_change" if {"role", "scopes", "all_data_scope"} & set(self.request.data) else "update",
                     obj=user, changes=audit.diff(before, audit.snapshot(user)))

    def perform_destroy(self, instance):
        if instance == self.request.user:
            raise serializers.ValidationError("You cannot delete your own account.")
        super().perform_destroy(instance)


class RoleViewSet(AuditedModelViewSet):
    serializer_class = RoleSerializer
    queryset = Role.objects.all()
    read_permission = "admin.users"
    write_permission = "admin.users"
    bumps_config_version = False

    def perform_destroy(self, instance):
        if instance.is_system:
            raise serializers.ValidationError("System roles cannot be deleted.")
        super().perform_destroy(instance)

    @action(detail=False, methods=["get"])
    def permissions(self, request):
        return Response([{"key": k, "description": v} for k, v in PERMISSIONS.items()])
