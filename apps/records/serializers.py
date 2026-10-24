from rest_framework import serializers

from apps.accounts.permissions import in_scope
from apps.records.models import Record


class RecordSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_kind = serializers.CharField(source="category.kind", read_only=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    parent_summary = serializers.SerializerMethodField()

    class Meta:
        model = Record
        fields = ["id", "category", "category_name", "category_kind", "source_id", "unit", "unit_name", "department",
                  "department_name", "status", "title", "data", "issues", "parent_ref", "parent_summary", "last_synced_at"]

    def get_parent_summary(self, obj):
        # Parent context is shown (as in emails) but only minimal fields, never the parent's other children.
        p = obj.parent
        if p is None:
            return None
        return {"id": p.pk if in_scope(self.context["request"].user, p.unit_id, p.department_id) else None,
                "category": p.category.name, "source_id": p.source_id, "title": p.title, "status": p.status}


class RecordDetailSerializer(RecordSerializer):
    children = serializers.SerializerMethodField()

    class Meta(RecordSerializer.Meta):
        fields = RecordSerializer.Meta.fields + ["children"]

    def get_children(self, obj):
        user = self.context["request"].user
        visible = [c for c in obj.children.select_related("category", "unit", "department") if in_scope(user, c.unit_id, c.department_id)]
        return [{"id": c.pk, "category": c.category.name, "source_id": c.source_id, "title": c.title, "status": c.status,
                 "unit": c.unit.name, "department": c.department.name, "data": c.data} for c in visible]
