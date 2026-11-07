from rest_framework import serializers

from apps.catalog.models import (
    RESERVED_FIELD_KEYS,
    Category,
    Department,
    FieldDefinition,
    RecipientGroup,
    Relationship,
    StatusDefinition,
    Unit,
    validate_email_list,
)


def _string_list(value, name):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise serializers.ValidationError(f"{name} must be a list of text values.")
    return [v.strip() for v in value if v.strip()]


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ["id", "code", "name", "aliases", "active"]

    def validate_aliases(self, value):
        return _string_list(value, "Aliases")


class DepartmentSerializer(UnitSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "aliases", "active"]


class RecipientGroupSerializer(serializers.ModelSerializer):
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    warnings = serializers.SerializerMethodField()

    class Meta:
        model = RecipientGroup
        fields = ["id", "unit", "department", "unit_name", "department_name", "to_emails", "cc_emails", "sender_profile", "active", "warnings"]

    def _emails(self, value, field):
        value = [v.strip() for v in (value or []) if isinstance(v, str) and v.strip()]
        try:
            validate_email_list(value)
        except Exception as exc:
            raise serializers.ValidationError(getattr(exc, "messages", [str(exc)]))
        return value

    def validate_to_emails(self, value):
        value = self._emails(value, "to")
        if not value:
            raise serializers.ValidationError("At least one 'To' recipient is required.")
        return value

    def validate_cc_emails(self, value):
        return self._emails(value, "cc")

    def validate(self, attrs):
        to = {e.lower() for e in attrs.get("to_emails", getattr(self.instance, "to_emails", []) or [])}
        cc = {e.lower() for e in attrs.get("cc_emails", getattr(self.instance, "cc_emails", []) or [])}
        both = to & cc
        if both:
            raise serializers.ValidationError({"cc_emails": f"Already in 'To': {', '.join(sorted(both))}"})
        return attrs

    def get_warnings(self, obj):
        warnings = []
        if not obj.to_emails:
            warnings.append("No 'To' recipients.")
        domains = {e.split("@")[-1].lower() for e in obj.to_emails + obj.cc_emails}
        if len(domains) > 1:
            warnings.append(f"Recipients span multiple domains: {', '.join(sorted(domains))}.")
        return warnings


class FieldDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FieldDefinition
        fields = ["id", "category", "key", "label", "field_type", "required", "choices", "max_length", "pattern", "sort_order", "active"]

    def validate_key(self, value):
        if value in RESERVED_FIELD_KEYS:
            raise serializers.ValidationError(f"'{value}' is reserved.")
        return value

    def validate_choices(self, value):
        return _string_list(value, "Choices")

    def validate_pattern(self, value):
        import re
        if value:
            if len(value) > 200:
                raise serializers.ValidationError("Pattern is too long.")
            try:
                re.compile(value)
            except re.error:
                raise serializers.ValidationError("Invalid regular expression.")
        return value


class StatusDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatusDefinition
        fields = ["id", "category", "value", "label", "is_closed", "aliases"]

    def validate_aliases(self, value):
        return _string_list(value, "Aliases")


class CategorySerializer(serializers.ModelSerializer):
    fields_detail = FieldDefinitionSerializer(source="fields", many=True, read_only=True)
    statuses_detail = StatusDefinitionSerializer(source="statuses", many=True, read_only=True)
    record_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Category
        fields = ["id", "key", "name", "kind", "description", "title_field", "display_fields", "sort_order", "active",
                  "fields_detail", "statuses_detail", "record_count"]

    def validate_display_fields(self, value):
        return _string_list(value, "Display fields")

    def validate(self, attrs):
        instance = self.instance
        if instance is not None:
            keys = set(instance.fields.values_list("key", flat=True)) | {"source_id", "status", "unit", "department", "title"}
            unknown = [k for k in attrs.get("display_fields", []) if k not in keys]
            if unknown:
                raise serializers.ValidationError({"display_fields": f"Unknown field(s): {', '.join(unknown)}"})
            title = attrs.get("title_field")
            if title and title not in keys:
                raise serializers.ValidationError({"title_field": "Unknown field."})
        return attrs


class RelationshipSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent_category.name", read_only=True, default="Any event type")
    child_name = serializers.CharField(source="child_category.name", read_only=True)

    class Meta:
        model = Relationship
        fields = ["id", "parent_category", "child_category", "parent_name", "child_name", "link_field", "active"]

    def validate(self, attrs):
        parent = attrs.get("parent_category", getattr(self.instance, "parent_category", None))
        child = attrs.get("child_category", getattr(self.instance, "child_category", None))
        link = attrs.get("link_field", getattr(self.instance, "link_field", None))
        if parent and child and parent.pk == child.pk:
            raise serializers.ValidationError("A category cannot be its own parent.")
        if child and link and not child.fields.filter(key=link).exists():
            raise serializers.ValidationError({"link_field": f"'{link}' is not a field of {child.name}."})
        # Prevent cycles (A -> B -> A).
        seen, current = {child.pk} if child else set(), parent
        while current is not None:
            if current.pk in seen:
                raise serializers.ValidationError("This relationship would create a cycle.")
            seen.add(current.pk)
            rel = Relationship.objects.filter(child_category=current).exclude(pk=getattr(self.instance, "pk", None)).first()
            current = rel.parent_category if rel else None
        return attrs
