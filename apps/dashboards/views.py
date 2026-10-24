from django.db import transaction
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import has_perm
from apps.core import audit
from apps.core.viewsets import AuditedModelViewSet
from apps.dashboards.models import Dashboard, DashboardAssignment, Widget
from apps.dashboards.serializers import AssignmentSerializer, DashboardSerializer, WidgetSerializer
from apps.dashboards.services.access import accessible_dashboards
from apps.dashboards.services.query import QueryError, widget_data


def _widget_payload(widget, user):
    try:
        return {"id": widget.pk, "ok": True, "data": widget_data(widget, user)}
    except QueryError as exc:
        return {"id": widget.pk, "ok": False, "error": str(exc)}


class DashboardViewSet(AuditedModelViewSet):
    serializer_class = DashboardSerializer
    read_permission = "dashboards.view"
    write_permission = "dashboards.manage"
    bumps_config_version = False

    def get_queryset(self):
        # Unauthorized dashboards are simply not found - direct URLs are denied.
        return accessible_dashboards(self.request.user).prefetch_related("widgets", "assignments__role", "assignments__user")

    def perform_create(self, serializer):
        obj = serializer.save(created_by=self.request.user)
        audit.record("create", obj=obj)

    @action(detail=True, methods=["get"])
    def data(self, request, pk=None):
        dashboard = self.get_object()
        if not dashboard.is_published and not has_perm(request.user, "dashboards.manage"):
            raise PermissionDenied()
        return Response({"dashboard": dashboard.pk, "widgets": [_widget_payload(w, request.user) for w in dashboard.widgets.all()]})

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        dashboard = self.get_object()
        dashboard.is_published = bool(request.data.get("published", True))
        dashboard.save(update_fields=["is_published", "updated_at"])
        audit.record("dashboard_publish" if dashboard.is_published else "dashboard_unpublish", obj=dashboard)
        return Response(DashboardSerializer(dashboard).data)

    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        source = self.get_object()
        with transaction.atomic():
            copy = Dashboard.objects.create(name=f"{source.name} (copy)"[:128], description=source.description, created_by=request.user)
            for w in source.widgets.all():
                w.pk = None
                w.dashboard = copy
                w.save()
        audit.record("dashboard_clone", obj=copy, changes={"source": source.pk})
        return Response(DashboardSerializer(copy).data, status=201)


class WidgetViewSet(AuditedModelViewSet):
    queryset = Widget.objects.select_related("dashboard")
    serializer_class = WidgetSerializer
    read_permission = "dashboards.manage"
    write_permission = "dashboards.manage"
    bumps_config_version = False

    @action(detail=False, methods=["post"])
    def preview(self, request):
        serializer = WidgetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        widget = Widget(**serializer.validated_data)
        try:
            return Response({"ok": True, "data": widget_data(widget, request.user)})
        except QueryError as exc:
            raise ValidationError({"detail": str(exc)})


class AssignmentViewSet(AuditedModelViewSet):
    queryset = DashboardAssignment.objects.select_related("role", "user", "dashboard")
    serializer_class = AssignmentSerializer
    read_permission = "dashboards.manage"
    write_permission = "dashboards.manage"
    bumps_config_version = False
    http_method_names = ["get", "post", "delete", "options", "head"]
