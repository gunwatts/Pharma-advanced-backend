from django.conf import settings
from rest_framework import serializers

from apps.catalog.models import FieldDefinition
from apps.sources import connectors
from apps.sources.models import DataSource, SourceMapping, SyncRun

GRAPH_KEYS = {"drive_id", "site_id", "user", "item_id", "item_path", "display_path"}


class DataSourceSerializer(serializers.ModelSerializer):
    mapping_count = serializers.IntegerField(source="mappings.count", read_only=True)

    class Meta:
        model = DataSource
        fields = ["id", "name", "provider", "config", "description", "active", "uploaded_file_name", "uploaded_at",
                  "last_checked_at", "last_check_ok", "last_check_message", "mapping_count", "created_at"]
        read_only_fields = ["uploaded_file_name", "uploaded_at", "last_checked_at", "last_check_ok", "last_check_message", "created_at"]

    def validate(self, attrs):
        provider = attrs.get("provider", getattr(self.instance, "provider", None))
        config = attrs.get("config", getattr(self.instance, "config", {}) or {})
        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "Invalid configuration."})
        if provider == DataSource.PROVIDER_UPLOAD:
            attrs["config"] = {}
        elif provider == DataSource.PROVIDER_SERVER_FOLDER:
            root, path = config.get("root", ""), str(config.get("path", ""))
            if root not in settings.SOURCE_ROOTS:
                raise serializers.ValidationError({"config": "Choose one of the server folder roots configured by IT (SOURCE_ROOTS)."})
            try:
                resolved = connectors.safe_server_path(root, path)
            except connectors.SourceError as exc:
                raise serializers.ValidationError({"config": str(exc)})
            if path and resolved.suffix.lower() not in connectors.ALLOWED_EXTENSIONS:
                raise serializers.ValidationError({"config": "Select an .xlsx or .xlsm workbook."})
            attrs["config"] = {"root": root, "path": path.replace("\\", "/").lstrip("/")}
        elif provider == DataSource.PROVIDER_GRAPH:
            clean = {k: str(v)[:512] for k, v in config.items() if k in GRAPH_KEYS and v}
            if not any(k in clean for k in ("drive_id", "site_id", "user")):
                raise serializers.ValidationError({"config": "Specify a SharePoint site, drive or OneDrive user."})
            attrs["config"] = clean
        return attrs


class SourceMappingSerializer(serializers.ModelSerializer):
    data_source_name = serializers.CharField(source="data_source.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    fixed_unit_name = serializers.CharField(source="fixed_unit.name", read_only=True, default=None)

    class Meta:
        model = SourceMapping
        fields = ["id", "name", "data_source", "data_source_name", "category", "category_name", "worksheet", "header_row",
                  "id_column", "unit_column", "fixed_unit", "fixed_unit_name", "department_column", "status_column", "column_map",
                  "date_formats", "max_rejected_rows", "active", "last_validated_at", "last_validation_ok", "last_attempt_at",
                  "last_success_at", "last_status", "last_rows_read", "last_rows_imported", "last_rows_rejected", "last_error"]
        read_only_fields = ["last_validated_at", "last_validation_ok", "last_attempt_at", "last_success_at", "last_status",
                            "last_rows_read", "last_rows_imported", "last_rows_rejected", "last_error"]

    def validate_column_map(self, value):
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise serializers.ValidationError("Column map must be {field_key: column header}.")
        return {k: v.strip() for k, v in value.items() if v and v.strip()}

    def validate_date_formats(self, value):
        if not isinstance(value, list) or not all(isinstance(v, str) and "%" in v and len(v) < 40 for v in value):
            raise serializers.ValidationError("Date formats must be strftime patterns such as %d-%m-%Y.")
        return value

    def validate_header_row(self, value):
        if not 1 <= value <= 100:
            raise serializers.ValidationError("Header row must be between 1 and 100.")
        return value

    def validate(self, attrs):
        get = lambda k: attrs.get(k, getattr(self.instance, k, None))  # noqa: E731
        if not get("fixed_unit") and not get("unit_column"):
            raise serializers.ValidationError({"unit_column": "Map a unit column or choose a fixed unit."})
        category = get("category")
        if category is not None:
            keys = set(FieldDefinition.objects.filter(category=category, active=True).values_list("key", flat=True))
            unknown = [k for k in (get("column_map") or {}) if k not in keys]
            if unknown:
                raise serializers.ValidationError({"column_map": f"Unknown field(s) for {category.name}: {', '.join(unknown)}"})
        return attrs


class SyncRunSerializer(serializers.ModelSerializer):
    mapping_name = serializers.CharField(source="mapping.__str__", read_only=True)

    class Meta:
        model = SyncRun
        fields = ["id", "mapping", "mapping_name", "daily_run", "triggered_by", "started_at", "finished_at", "status", "rows_read",
                  "rows_imported", "rows_rejected", "rows_flagged", "rows_closed", "records_created", "records_updated",
                  "records_removed_closed", "records_removed_missing", "message", "errors"]
