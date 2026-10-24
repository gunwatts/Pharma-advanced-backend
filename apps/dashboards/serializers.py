from rest_framework import serializers

from apps.dashboards.models import Dashboard, DashboardAssignment, Widget
from apps.dashboards.services.query import QueryError, validate_filters


class WidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "dashboard", "title", "widget_type", "category", "filters", "group_by", "date_field", "date_bucket",
                  "window_days", "columns", "sort_by", "sort_desc", "limit", "width", "position"]

    def validate_filters(self, value):
        try:
            validate_filters(value)
        except QueryError as exc:
            raise serializers.ValidationError(str(exc))
        return value

    def validate_columns(self, value):
        if not isinstance(value, list) or not all(isinstance(v, str) and len(v) < 64 for v in value):
            raise serializers.ValidationError("Columns must be a list of field keys.")
        return value[:20]

    def validate(self, attrs):
        wtype = attrs.get("widget_type", getattr(self.instance, "widget_type", None))
        if wtype in ("line", "area", "upcoming", "overdue") and not attrs.get("date_field", getattr(self.instance, "date_field", "")):
            raise serializers.ValidationError({"date_field": "Choose a date field for this widget type."})
        if not 1 <= attrs.get("width", getattr(self.instance, "width", 1)) <= 3:
            raise serializers.ValidationError({"width": "Width must be 1-3."})
        if not 1 <= attrs.get("limit", getattr(self.instance, "limit", 10)) <= 500:
            raise serializers.ValidationError({"limit": "Limit must be 1-500."})
        return attrs


class AssignmentSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    username = serializers.CharField(source="user.username", read_only=True, default=None)

    class Meta:
        model = DashboardAssignment
        fields = ["id", "dashboard", "role", "user", "role_name", "username"]

    def validate(self, attrs):
        if bool(attrs.get("role")) == bool(attrs.get("user")):
            raise serializers.ValidationError("Assign to exactly one role or one user.")
        return attrs


class DashboardSerializer(serializers.ModelSerializer):
    widgets = WidgetSerializer(many=True, read_only=True)
    assignments = AssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = Dashboard
        fields = ["id", "name", "description", "is_published", "widgets", "assignments", "created_at", "updated_at"]
        read_only_fields = ["is_published"]
