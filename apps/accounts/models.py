import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# Fixed permission vocabulary. Roles are configurable sets of these keys.
PERMISSIONS = {
    "admin.configure": "Configure sources, mappings, rules, templates, senders and settings",
    "admin.users": "Manage users, roles and data scopes",
    "runs.view": "View sync logs, runs and delivery history",
    "runs.manage": "Trigger runs, dry runs, test emails and retries",
    "records.view": "View records within data scope",
    "records.export": "Export records within data scope",
    "dashboards.view": "View assigned dashboards",
    "dashboards.manage": "Create, edit, publish and assign dashboards",
    "audit.view": "View the audit trail",
}


class Role(models.Model):
    key = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False, help_text="System roles cannot be deleted.")
    default_dashboard = models.ForeignKey("dashboards.Dashboard", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    all_data_scope = models.BooleanField(default=False, help_text="See every unit and department.")
    default_dashboard = models.ForeignKey("dashboards.Dashboard", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return f"Profile<{self.user.username}>"


class UserScope(models.Model):
    """Grants visibility of a unit/department pair. Null means 'any'."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="data_scopes")
    unit = models.ForeignKey("catalog.Unit", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    department = models.ForeignKey("catalog.Department", null=True, blank=True, on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "unit", "department"], name="uniq_user_scope")]


def hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AuthToken(models.Model):
    """Expiring API token. Only a SHA-256 hash is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="auth_tokens")
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, user):
        raw = secrets.token_urlsafe(32)
        expires = timezone.now() + timedelta(hours=settings.AUTH_TOKEN_TTL_HOURS)
        token = cls.objects.create(user=user, key_hash=hash_token(raw), expires_at=expires)
        cls.objects.filter(user=user, expires_at__lt=timezone.now()).delete()
        return raw, token
