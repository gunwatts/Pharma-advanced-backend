from django.conf import settings
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import HasPermission, has_perm
from apps.core import audit, locks
from apps.core.exceptions import ConfirmationRequired
from apps.core.viewsets import AuditedModelViewSet, confirmed
from apps.sources import connectors
from apps.sources.excel import WorkbookError, list_sheets, read_sheet, to_text
from apps.sources.models import DataSource, SourceMapping, SyncRun
from apps.sources.serializers import DataSourceSerializer, SourceMappingSerializer, SyncRunSerializer
from apps.sources.sync import sync_mapping, validate_mapping


def _source_error(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class DataSourceViewSet(AuditedModelViewSet):
    queryset = DataSource.objects.all()
    serializer_class = DataSourceSerializer

    def impacts(self, instance):
        return [f"Excel mapping '{m}'." for m in instance.mappings.all()]

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        connectors.upload_path(instance).unlink(missing_ok=True)

    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        source = self.get_object()
        if source.provider != DataSource.PROVIDER_UPLOAD:
            raise ValidationError("This source does not accept uploads.")
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "Choose a workbook to upload."})
        try:
            name = connectors.save_upload(source, uploaded)
            sheets = list_sheets(connectors.fetch_workbook(source))
        except (connectors.SourceError, WorkbookError) as exc:
            connectors.upload_path(source).unlink(missing_ok=True)
            return _source_error(exc)
        source.uploaded_file_name, source.uploaded_at = name, timezone.now()
        source.last_checked_at, source.last_check_ok, source.last_check_message = timezone.now(), True, f"{len(sheets)} worksheet(s) found."
        source.save()
        audit.record("source_upload", obj=source, changes={"file": name})
        return Response({"file": name, "sheets": sheets})

    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        """Validate reachability, permissions and workbook format."""
        source = self.get_object()
        try:
            sheets = list_sheets(connectors.fetch_workbook(source))
            ok, message = True, f"Workbook reachable. {len(sheets)} worksheet(s): {', '.join(sheets[:10])}"
        except (connectors.SourceError, WorkbookError) as exc:
            sheets, ok, message = [], False, str(exc)
        source.last_checked_at, source.last_check_ok, source.last_check_message = timezone.now(), ok, message[:500]
        source.save(update_fields=["last_checked_at", "last_check_ok", "last_check_message"])
        return Response({"ok": ok, "message": message, "sheets": sheets})

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        source = self.get_object()
        worksheet = request.query_params.get("worksheet", "")
        try:
            header_row = int(request.query_params.get("header_row", 1))
            content = connectors.fetch_workbook(source)
            sheets = list_sheets(content)
            worksheet = worksheet or sheets[0]
            headers, rows = read_sheet(content, worksheet, header_row)
        except (connectors.SourceError, WorkbookError) as exc:
            return _source_error(exc)
        except ValueError:
            raise ValidationError({"header_row": "Must be a number."})
        sample = [{"row": n, "values": {h: to_text(v) for h, v in values.items()}} for n, values in rows[:10]]
        return Response({"sheets": sheets, "worksheet": worksheet, "headers": [h for h in headers if h], "rows": sample, "row_count": len(rows)})


@api_view(["GET"])
@permission_classes([HasPermission.of("admin.configure")])
def source_options(request):
    """What the server can reach. Root names only - absolute paths are never exposed."""
    return Response({"server_roots": sorted(settings.SOURCE_ROOTS), "graph_configured": connectors.graph_configured(),
                     "max_workbook_mb": settings.MAX_WORKBOOK_BYTES // (1024 * 1024)})


@api_view(["GET"])
@permission_classes([HasPermission.of("admin.configure")])
def browse_server(request):
    try:
        return Response({"entries": connectors.browse_server_folder(request.query_params.get("root", ""), request.query_params.get("path", ""))})
    except connectors.SourceError as exc:
        return _source_error(exc)


@api_view(["GET"])
@permission_classes([HasPermission.of("admin.configure")])
def browse_graph(request):
    params = request.query_params
    try:
        if params.get("search"):
            return Response({"sites": connectors.graph_search_sites(params["search"])})
        if params.get("list_drives"):
            return Response({"drives": connectors.graph_list_drives(site_id=params.get("site_id"), user=params.get("user"))})
        config = {k: params[k] for k in ("drive_id", "site_id", "user") if params.get(k)}
        return Response({"entries": connectors.graph_browse(config, params.get("item_id") or None)})
    except connectors.SourceError as exc:
        return _source_error(exc)


class SourceMappingViewSet(AuditedModelViewSet):
    queryset = SourceMapping.objects.select_related("data_source", "category", "fixed_unit")
    serializer_class = SourceMappingSerializer

    def impacts(self, instance):
        n = instance.records.count()
        return [f"{n} current record(s) imported by this mapping will be deleted."] if n else []

    def perform_destroy(self, instance):
        impacts = self.impacts(instance)
        if impacts and not confirmed(self.request):
            raise ConfirmationRequired("Deleting this mapping removes its current records.", impacts)
        instance.records.all().delete()  # current business records only; ledger/logs are preserved
        super().perform_destroy(instance)

    def update_impacts(self, instance, data):
        keys = ("id_column", "category", "data_source", "worksheet")
        if any(k in data and data[k] != getattr(instance, k) for k in keys) and instance.records.exists():
            return ["Changing the ID column, category, source or worksheet can cause current records to be replaced at the next sync."]
        return []

    def perform_update(self, serializer):
        super().perform_update(serializer)
        SourceMapping.objects.filter(pk=serializer.instance.pk).update(last_validation_ok=None)

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        mapping = self.get_object()
        report = validate_mapping(mapping)
        mapping.last_validated_at, mapping.last_validation_ok = timezone.now(), report["ok"]
        mapping.save(update_fields=["last_validated_at", "last_validation_ok"])
        return Response(report)

    @action(detail=False, methods=["post"], url_path="validate-draft")
    def validate_draft(self, request):
        """Validate an unsaved mapping (wizard) without persisting anything."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(validate_mapping(SourceMapping(**serializer.validated_data)))

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        if not has_perm(request.user, "runs.manage"):
            return Response({"detail": "You do not have permission to run a sync."}, status=403)
        mapping = self.get_object()
        try:
            with locks.run_lock("daily_reminders", ttl_minutes=30):
                run = sync_mapping(mapping, triggered_by=request.user.username)
        except locks.LockNotAcquired:
            return Response({"detail": "A reminder run is in progress. Try again shortly."}, status=409)
        audit.record("manual_sync", obj=mapping, changes={"status": run.status})
        return Response(SyncRunSerializer(run).data)


class SyncRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = SyncRunSerializer
    permission_classes = [HasPermission.of("runs.view")]

    def get_queryset(self):
        qs = SyncRun.objects.select_related("mapping__data_source", "mapping__category")
        for param in ("mapping", "daily_run", "status"):
            if self.request.query_params.get(param):
                qs = qs.filter(**{param: self.request.query_params[param]})
        return qs
