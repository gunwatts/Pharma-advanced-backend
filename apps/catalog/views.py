from django.db.models import Count

from apps.catalog import impacts
from apps.catalog.models import Category, Department, FieldDefinition, RecipientGroup, Relationship, StatusDefinition, Unit
from apps.catalog.serializers import (
    CategorySerializer,
    DepartmentSerializer,
    FieldDefinitionSerializer,
    RecipientGroupSerializer,
    RelationshipSerializer,
    StatusDefinitionSerializer,
    UnitSerializer,
)
from apps.core.viewsets import AuditedModelViewSet
from apps.sources.sync import relink_parents


class UnitViewSet(AuditedModelViewSet):
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    read_permission = None  # any signed-in user (needed for filters); writes need admin.configure

    def impacts(self, instance):
        return impacts.org_usage(instance, "unit")


class DepartmentViewSet(AuditedModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    read_permission = None

    def impacts(self, instance):
        return impacts.org_usage(instance, "department")


class RecipientGroupViewSet(AuditedModelViewSet):
    queryset = RecipientGroup.objects.select_related("unit", "department")
    serializer_class = RecipientGroupSerializer


class CategoryViewSet(AuditedModelViewSet):
    serializer_class = CategorySerializer
    read_permission = None

    def get_queryset(self):
        qs = Category.objects.prefetch_related("fields", "statuses").annotate(record_count=Count("records"))
        if self.request.query_params.get("active") == "true":
            qs = qs.filter(active=True)
        return qs

    def impacts(self, instance):
        return impacts.category_usage(instance)

    def update_impacts(self, instance, data):
        if data.get("active") is False and instance.active:
            return impacts.category_usage(instance)
        return []


class FieldDefinitionViewSet(AuditedModelViewSet):
    serializer_class = FieldDefinitionSerializer
    read_permission = None

    def get_queryset(self):
        qs = FieldDefinition.objects.select_related("category")
        category = self.request.query_params.get("category")
        return qs.filter(category_id=category) if category else qs

    def impacts(self, instance):
        return impacts.field_usage(instance.category_id, instance.key)

    def update_impacts(self, instance, data):
        if ("key" in data and data["key"] != instance.key) or ("field_type" in data and data["field_type"] != instance.field_type) \
                or (data.get("active") is False and instance.active):
            return impacts.field_usage(instance.category_id, instance.key)
        return []


class StatusDefinitionViewSet(AuditedModelViewSet):
    serializer_class = StatusDefinitionSerializer
    read_permission = None

    def get_queryset(self):
        qs = StatusDefinition.objects.all()
        category = self.request.query_params.get("category")
        return qs.filter(category_id=category) if category else qs

    def impacts(self, instance):
        return impacts.status_usage(instance)

    def update_impacts(self, instance, data):
        if ("value" in data and data["value"] != instance.value) or ("is_closed" in data and data["is_closed"] != instance.is_closed):
            usage = impacts.status_usage(instance)
            if data.get("is_closed") and not instance.is_closed:
                usage.append("Records with this status will be removed from current records at the next successful sync.")
            return usage
        return []


class RelationshipViewSet(AuditedModelViewSet):
    queryset = Relationship.objects.select_related("parent_category", "child_category")
    serializer_class = RelationshipSerializer

    def impacts(self, instance):
        n = instance.child_category.records.exclude(parent=None).count()
        return [f"{n} child record(s) will lose their parent context in emails and dashboards."] if n else []

    def update_impacts(self, instance, data):
        return self.impacts(instance) if any(k in data and data[k] != getattr(instance, k) for k in ("parent_category", "child_category", "link_field")) else []

    def _changed(self):
        super()._changed()
        relink_parents()  # parent links depend on relationship configuration
