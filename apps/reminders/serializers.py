from rest_framework import serializers

from apps.catalog.models import FieldDefinition, StatusDefinition
from apps.reminders.models import DailyRun, Delivery, EmailTemplate, JobSchedule, ReminderRule, SenderProfile
from apps.reminders.services import providers
from apps.reminders.services.rules import validate_conditions
from apps.reminders.services.scheduler import next_run_at
from apps.reminders.services.templating import validate_template_text

SMTP_SECURITY = {"starttls", "ssl", "none"}


class SenderProfileSerializer(serializers.ModelSerializer):
    secret_status = serializers.SerializerMethodField()
    delivers = serializers.SerializerMethodField()

    class Meta:
        model = SenderProfile
        fields = ["id", "name", "provider", "from_address", "from_name", "reply_to", "settings", "secret_env_prefix", "is_default",
                  "active", "last_test_at", "last_test_status", "last_test_message", "secret_status", "delivers"]
        read_only_fields = ["last_test_at", "last_test_status", "last_test_message"]

    def get_secret_status(self, obj):
        # Only reports whether each environment variable is set - never the value.
        return providers.get_provider(obj).secret_status()

    def get_delivers(self, obj):
        return providers.get_provider(obj).delivers

    def validate_secret_env_prefix(self, value):
        import re
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}", value):
            raise serializers.ValidationError("Use letters, digits and underscores, e.g. SENDER_MAIN.")
        return value

    def validate(self, attrs):
        provider = attrs.get("provider", getattr(self.instance, "provider", None))
        cfg = attrs.get("settings", getattr(self.instance, "settings", {}) or {})
        if not isinstance(cfg, dict):
            raise serializers.ValidationError({"settings": "Invalid settings."})
        if provider == SenderProfile.PROVIDER_SMTP:
            host = str(cfg.get("host", "")).strip()
            if not host or len(host) > 255:
                raise serializers.ValidationError({"settings": "SMTP host is required."})
            try:
                port = int(cfg.get("port", 587))
            except (TypeError, ValueError):
                raise serializers.ValidationError({"settings": "SMTP port must be a number."})
            security = cfg.get("security", "starttls")
            if security not in SMTP_SECURITY or not 1 <= port <= 65535:
                raise serializers.ValidationError({"settings": "Invalid SMTP port or security mode."})
            attrs["settings"] = {"host": host, "port": port, "security": security, "username": str(cfg.get("username", "")).strip()[:255]}
        else:
            attrs["settings"] = {}
        for forbidden in ("password", "secret", "token"):
            if any(forbidden in str(k).lower() for k in cfg):
                raise serializers.ValidationError({"settings": "Secrets must be set as server environment variables, not here."})
        return attrs

    def save(self, **kwargs):
        obj = super().save(**kwargs)
        if obj.is_default:
            SenderProfile.objects.exclude(pk=obj.pk).update(is_default=False)
        return obj


class ReminderRuleSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = ReminderRule
        fields = ["id", "name", "category", "category_name", "target_date_field", "window_days", "include_statuses", "exclude_statuses",
                  "conditions", "units", "departments", "active", "updated_at"]

    def validate_window_days(self, value):
        if value > 365:
            raise serializers.ValidationError("Window must be 365 days or less.")
        return value

    def validate_conditions(self, value):
        errors = validate_conditions(value)
        if errors:
            raise serializers.ValidationError(errors)
        return value

    def validate(self, attrs):
        category = attrs.get("category", getattr(self.instance, "category", None))
        target = attrs.get("target_date_field", getattr(self.instance, "target_date_field", None))
        if category and target and not FieldDefinition.objects.filter(category=category, key=target, field_type=FieldDefinition.TYPE_DATE).exists():
            raise serializers.ValidationError({"target_date_field": f"Choose a date field of {category.name}."})
        if category:
            known = set(StatusDefinition.objects.filter(category=category).values_list("value", flat=True))
            for key in ("include_statuses", "exclude_statuses"):
                values = attrs.get(key, getattr(self.instance, key, []) or [])
                unknown = [v for v in values if known and v not in known]
                if unknown:
                    raise serializers.ValidationError({key: f"Unknown status(es): {', '.join(unknown)}"})
        return attrs


class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = ["id", "name", "is_active", "subject", "intro_text", "outro_text", "no_pending_subject", "no_pending_text",
                  "show_parent_context", "updated_at"]
        read_only_fields = ["is_active"]

    def validate(self, attrs):
        errors = {}
        for key in ("subject", "intro_text", "outro_text", "no_pending_subject", "no_pending_text"):
            if key in attrs:
                problems = validate_template_text(attrs[key])
                if problems:
                    errors[key] = problems
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class EmailTemplatePreviewSerializer(EmailTemplateSerializer):
    """Validates an unsaved/edited template for preview only (no uniqueness checks)."""

    name = serializers.CharField(required=False, allow_blank=True, max_length=128)


class JobScheduleSerializer(serializers.ModelSerializer):
    next_run_at = serializers.SerializerMethodField()

    class Meta:
        model = JobSchedule
        fields = ["id", "job", "enabled", "run_time", "days_of_week", "last_run_date", "last_started_at", "last_finished_at",
                  "last_status", "next_run_at"]
        read_only_fields = ["job", "last_run_date", "last_started_at", "last_finished_at", "last_status"]

    def get_next_run_at(self, obj):
        value = next_run_at(obj)
        return value.isoformat() if value else None

    def validate_days_of_week(self, value):
        if not isinstance(value, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in value):
            raise serializers.ValidationError("Days must be numbers 0 (Mon) to 6 (Sun).")
        return sorted(set(value))


class DailyRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyRun
        fields = ["id", "run_date", "kind", "status", "triggered_by", "started_at", "finished_at", "config_version", "summary"]


class DeliverySerializer(serializers.ModelSerializer):
    reconciled_by_name = serializers.CharField(source="reconciled_by.username", read_only=True, default=None)

    class Meta:
        model = Delivery
        fields = ["id", "idempotency_key", "run_date", "kind", "state", "unit", "department", "unit_name", "department_name",
                  "daily_run", "config_version", "subject", "recipients_to", "recipients_cc", "item_counts", "provider",
                  "sender_profile_name", "provider_message_id", "attempts", "last_attempt_at", "sent_at", "hold_reason", "error",
                  "reconciled_by_name", "reconcile_note", "created_at", "updated_at"]
