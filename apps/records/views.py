import csv
from datetime import timedelta

from django.db.models import Q
from django.http import StreamingHttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.permissions import HasPermission, has_perm
from apps.catalog.models import FieldDefinition
from apps.core import audit
from apps.core.timeutils import business_today
from apps.dashboards.models import Widget
from apps.dashboards.services import query
from apps.dashboards.services.access import accessible_dashboards
from apps.records.serializers import RecordDetailSerializer, RecordSerializer
from apps.reminders.services.eligibility import parse_iso

MAX_EXPORT_ROWS = 20000


class _Echo:
    def write(self, value):
        return value


def _safe_csv(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class RecordViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [HasPermission.of("records.view")]

    def get_serializer_class(self):
        return RecordDetailSerializer if self.action == "retrieve" else RecordSerializer

    def get_queryset(self):
        return query.scoped_records(self.request.user).prefetch_related("children")

    def filtered_records(self):
        params = self.request.query_params
        widget_id = params.get("widget")
        if widget_id:
            widget = Widget.objects.filter(pk=widget_id, dashboard__in=accessible_dashboards(self.request.user)).first()
            if widget is None:
                raise NotFound()
            return query.drill_records(widget, self.request.user, params.get("group"))

        qs = query.scoped_records(self.request.user)
        for param in ("category", "unit", "department"):
            if params.get(param):
                try:
                    qs = qs.filter(**{f"{param}_id": int(params[param])})
                except ValueError:
                    raise ValidationError({param: "Invalid id."})
        if params.get("status"):
            qs = qs.filter(status__iexact=params["status"])
        if params.get("q"):
            qs = qs.filter(Q(source_id__icontains=params["q"]) | Q(title__icontains=params["q"]))
        if params.get("issues") == "true":
            qs = qs.exclude(issues=[])
        records = list(qs)

        due = params.get("due")
        if due:
            today = business_today()
            date_field = params.get("date_field")
            try:
                window = int(params.get("window", 7))
            except ValueError:
                window = 7

            first_date_field = {}
            for fd in FieldDefinition.objects.filter(field_type=FieldDefinition.TYPE_DATE).order_by("sort_order", "id"):
                first_date_field.setdefault(fd.category_id, fd.key)

            def target(r):
                key = date_field or first_date_field.get(r.category_id)
                return parse_iso((r.data or {}).get(key)) if key else None

            def keep(r):
                d = target(r)
                if due == "no_date":
                    return d is None
                if d is None:
                    return False
                return {"overdue": d < today, "today": d == today, "upcoming": today <= d <= today + timedelta(days=window)}.get(due, True)
            records = [r for r in records if keep(r)]
        return records

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.filtered_records())
        return self.get_paginated_response(RecordSerializer(page, many=True, context={"request": request}).data)

    @action(detail=False, methods=["get"])
    def export(self, request):
        if not has_perm(request.user, "records.export"):
            raise PermissionDenied("You do not have permission to export records.")
        records = self.filtered_records()[:MAX_EXPORT_ROWS]
        keys = []
        for r in records:
            for k in (r.data or {}):
                if k not in keys:
                    keys.append(k)
        audit.record("records_export", object_type="records.record", object_repr=f"{len(records)} rows",
                     changes={"filters": dict(request.query_params)})
        writer = csv.writer(_Echo())

        def rows():
            yield writer.writerow(["Category", "ID", "Unit", "Department", "Status", "Title", *keys, "Issues"])
            for r in records:
                yield writer.writerow([_safe_csv(v) for v in [r.category.name, r.source_id, r.unit.name, r.department.name, r.status, r.title,
                                                              *[(r.data or {}).get(k) for k in keys], "; ".join(i.get("message", "") for i in r.issues)]])

        response = StreamingHttpResponse(rows(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="records.csv"'
        return response
