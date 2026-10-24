from django.conf import settings
from django.db import models


class SystemSettings(models.Model):
    """Singleton with system-wide operational settings."""

    business_timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    default_window_days = models.PositiveSmallIntegerField(default=7)
    send_no_pending_email = models.BooleanField(default=True)
    alert_recipients = models.JSONField(default=list, blank=True)
    alert_sender_profile = models.ForeignKey(
        "reminders.SenderProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    max_send_attempts = models.PositiveSmallIntegerField(default=3)
    reminders_active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    config_version = models.PositiveIntegerField(default=1)
    wizard_state = models.JSONField(default=dict, blank=True, help_text="Quick Start progress (save/resume).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "system settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"business_timezone": settings.BUSINESS_TIMEZONE})
        return obj

    @classmethod
    def bump_config_version(cls):
        cls.objects.filter(pk=1).update(config_version=models.F("config_version") + 1)


class AuditEvent(models.Model):
    """Append-only audit trail for configuration changes and sensitive actions."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    object_type = models.CharField(max_length=64, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.CharField(max_length=64, blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_username} {self.action} {self.object_type}#{self.object_id}"


class JobLock(models.Model):
    """Cross-process lock row used to prevent concurrent duplicate job runs."""

    name = models.CharField(max_length=64, unique=True)
    holder = models.CharField(max_length=128)
    acquired_at = models.DateTimeField()
    expires_at = models.DateTimeField()
