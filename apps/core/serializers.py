from zoneinfo import available_timezones

from rest_framework import serializers

from apps.catalog.models import validate_email_list
from apps.core.models import AuditEvent, SystemSettings


class SystemSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemSettings
        fields = ["business_timezone", "default_window_days", "send_no_pending_email", "alert_recipients", "alert_sender_profile",
                  "max_send_attempts", "reminders_active", "activated_at", "activated_by", "config_version", "updated_at"]
        read_only_fields = ["reminders_active", "activated_at", "activated_by", "config_version", "updated_at"]

    def validate_business_timezone(self, value):
        if value not in available_timezones():
            raise serializers.ValidationError("Unknown timezone.")
        return value

    def validate_alert_recipients(self, value):
        try:
            validate_email_list(value)
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "messages", [str(exc)]))
        return value

    def validate_max_send_attempts(self, value):
        if not 1 <= value <= 10:
            raise serializers.ValidationError("Must be between 1 and 10.")
        return value

    def validate_default_window_days(self, value):
        if value > 365:
            raise serializers.ValidationError("Must be 365 days or less.")
        return value


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["id", "created_at", "actor_username", "action", "object_type", "object_id", "object_repr", "changes", "ip_address"]
