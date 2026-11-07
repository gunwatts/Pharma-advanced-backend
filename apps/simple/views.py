"""Simple V1 setup API used by the admin's Setup screens and the plant QA heads' Today view."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.models import AuthToken, Role, UserProfile, UserScope
from apps.accounts.permissions import HasPermission, has_full_scope, has_perm
from apps.catalog.models import Category, Department, RecipientGroup, Unit
from apps.core import audit
from apps.core.models import SystemSettings
from apps.reminders.models import ReminderRule
from apps.reminders.services import workflow
from apps.reminders.services.templating import TemplateError
from apps.reminders.setup_views import schedule_info
from apps.simple import layout
from apps.sources import connectors
from apps.sources.excel import WorkbookError, list_sheets
from apps.sources.models import DataSource
from apps.sources.sync import validate_mapping

ADMIN = HasPermission.of("admin.configure")
User = get_user_model()


def _bad(msg, field="detail"):
    raise ValidationError({field: msg})


# --- Workbook layout & event types ------------------------------------------------------------

class RuleSerializer(serializers.Serializer):
    window_days = serializers.IntegerField(min_value=0, max_value=365)
    repeat_every_days = serializers.IntegerField(min_value=1, max_value=365, allow_null=True, required=False)
    repeat_anchor = serializers.ChoiceField(choices=[c for c, _ in ReminderRule.ANCHOR_CHOICES])
    start_days_before = serializers.IntegerField(min_value=0, max_value=3650, allow_null=True, required=False)


class EventTypeSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    kind = serializers.ChoiceField(choices=["event", "action"], required=False, default="event")
    sheet_name = serializers.CharField(max_length=31)
    active = serializers.BooleanField(required=False, default=True)
    columns = serializers.DictField(child=serializers.CharField(max_length=128, allow_blank=True))
    open_statuses = serializers.ListField(child=serializers.CharField(max_length=64), allow_empty=False)
    closed_statuses = serializers.ListField(child=serializers.CharField(max_length=64), required=False, default=list)
    rule = RuleSerializer()

    def validate(self, attrs):
        roles = layout.ACTION_ROLES if attrs.get("kind") == "action" else layout.EVENT_ROLES
        missing = [layout.ROLE_LABELS[r] for r in roles if not (attrs["columns"].get(r) or "").strip()]
        if missing:
            raise serializers.ValidationError({"columns": f"Enter the column header for: {', '.join(missing)}."})
        overlap = {s.lower() for s in attrs["open_statuses"]} & {s.lower() for s in attrs.get("closed_statuses", [])}
        if overlap:
            raise serializers.ValidationError({"closed_statuses": f"A status cannot be both open and closed: {', '.join(sorted(overlap))}"})
        return attrs


@api_view(["GET", "PATCH"])
@permission_classes([ADMIN])
def workbook_layout(request):
    if request.method == "PATCH":
        data = layout.layout_settings()
        if "header_row" in request.data:
            try:
                data["header_row"] = int(request.data["header_row"])
            except (TypeError, ValueError):
                _bad("Header row must be a number.", "header_row")
            if not 1 <= data["header_row"] <= 50:
                _bad("Header row must be between 1 and 50.", "header_row")
        if "date_formats" in request.data:
            formats = request.data["date_formats"]
            if not isinstance(formats, list) or not all(isinstance(f, str) and "%" in f for f in formats):
                _bad("Date formats look like %d-%m-%Y.", "date_formats")
            data["date_formats"] = formats
        if "max_rejected_rows" in request.data:
            data["max_rejected_rows"] = max(0, int(request.data["max_rejected_rows"] or 0))
        SystemSettings.objects.filter(pk=1).update(workbook_layout=data)
        SystemSettings.bump_config_version()
        layout.rebuild_mappings()
        audit.record("update", object_type="core.workbook_layout", object_repr="Workbook layout", changes=data)
    types = [layout.event_type_payload(c) for c in Category.objects.exclude(sheet_name="").order_by("sort_order", "name")]
    return Response({"settings": layout.layout_settings(), "event_types": types, "role_labels": layout.ROLE_LABELS,
                     "anchors": [{"value": v, "label": label} for v, label in ReminderRule.ANCHOR_CHOICES]})


@api_view(["POST"])
@permission_classes([ADMIN])
def event_type_create(request):
    ser = EventTypeSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    if Category.objects.exclude(sheet_name="").filter(sheet_name__iexact=ser.validated_data["sheet_name"]).exists():
        _bad("Another event type already uses this sheet name.", "sheet_name")
    cat = layout.save_event_type(ser.validated_data)
    audit.record("create", obj=cat, changes={"event_type": ser.validated_data})
    return Response(layout.event_type_payload(cat), status=201)


@api_view(["PUT", "DELETE"])
@permission_classes([ADMIN])
def event_type_detail(request, pk):
    cat = Category.objects.filter(pk=pk).exclude(sheet_name="").first()
    if cat is None:
        raise NotFound()
    if request.method == "DELETE":  # deactivate: history and ledger are kept
        cat = layout.save_event_type({"active": False}, cat)
        audit.record("deactivate", obj=cat)
        return Response(layout.event_type_payload(cat))
    ser = EventTypeSerializer(data={**request.data, "kind": "action" if cat.kind == Category.KIND_ACTION else "event"})
    ser.is_valid(raise_exception=True)
    if Category.objects.exclude(sheet_name="").exclude(pk=cat.pk).filter(sheet_name__iexact=ser.validated_data["sheet_name"]).exists():
        _bad("Another event type already uses this sheet name.", "sheet_name")
    before = layout.event_type_payload(cat)
    cat = layout.save_event_type(ser.validated_data, cat)
    audit.record("update", obj=cat, changes={"before": before, "after": layout.event_type_payload(cat)})
    return Response(layout.event_type_payload(cat))


@api_view(["GET"])
@permission_classes([ADMIN])
def template_download(request):
    response = HttpResponse(layout.template_workbook(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="QMS_Plant_Workbook_Template.xlsx"'
    return response


# --- Plants, workbooks, departments & recipients ---------------------------------------------------

def _plant(pk):
    plant = Unit.objects.filter(pk=pk).first()
    if plant is None:
        raise NotFound()
    return plant


def _clean_emails(values, label):
    out, seen = [], set()
    for v in values or []:
        v = str(v).strip()
        if not v:
            continue
        try:
            validate_email(v)
        except DjangoValidationError:
            _bad(f"{label}: '{v}' is not a valid email address.", "departments")
        if v.lower() in seen:
            continue
        seen.add(v.lower())
        out.append(v)
    return out


@api_view(["GET", "POST"])
@permission_classes([ADMIN])
def plants(request):
    if request.method == "POST":
        name = str(request.data.get("name", "")).strip()
        code = str(request.data.get("code", "")).strip().upper()
        if not name or not code:
            _bad("Enter the plant name and a short code (e.g. P1).")
        if Unit.objects.filter(code__iexact=code).exists():
            _bad("A plant with this code already exists.", "code")
        with transaction.atomic():
            plant = Unit.objects.create(code=code[:32], name=name[:128], aliases=[a for a in request.data.get("aliases", []) if a])
            layout.plant_workbook(plant)
        layout.rebuild_mappings()
        SystemSettings.bump_config_version()
        audit.record("create", obj=plant)
        return Response(layout.plant_payload(plant), status=201)
    return Response([layout.plant_payload(p) for p in layout.plants()])


@api_view(["PATCH"])
@permission_classes([ADMIN])
def plant_detail(request, pk):
    plant = _plant(pk)
    before = audit.snapshot(plant)
    for field in ("name", "code", "active"):
        if field in request.data:
            setattr(plant, field, request.data[field] if field == "active" else str(request.data[field]).strip()[:128])
    if "aliases" in request.data:
        plant.aliases = [str(a).strip() for a in request.data["aliases"] if str(a).strip()]
    if Unit.objects.exclude(pk=plant.pk).filter(code__iexact=plant.code).exists():
        _bad("A plant with this code already exists.", "code")
    plant.save()
    source = layout.plant_workbook(plant)
    if source.active != plant.active:
        source.active = plant.active
        source.save(update_fields=["active"])
    layout.rebuild_mappings()
    SystemSettings.bump_config_version()
    audit.record("update", obj=plant, changes=audit.diff(before, audit.snapshot(plant)))
    return Response(layout.plant_payload(plant))


def _check_workbook(source):
    """Reachability + every expected sheet/column, without importing anything."""
    try:
        content = connectors.fetch_workbook(source)
        sheets = list_sheets(content)
    except (connectors.SourceError, WorkbookError) as exc:
        source.last_checked_at, source.last_check_ok, source.last_check_message = timezone.now(), False, str(exc)[:500]
        source.save(update_fields=["last_checked_at", "last_check_ok", "last_check_message"])
        return {"ok": False, "message": str(exc), "sheets": []}
    results = []
    for mapping in source.mappings.filter(active=True).select_related("category"):
        report = validate_mapping(mapping, content, sample_size=0)
        mapping.last_validated_at, mapping.last_validation_ok = timezone.now(), report["ok"]
        mapping.save(update_fields=["last_validated_at", "last_validation_ok"])
        problems = report["structure_errors"] + [f"Row {r['row']}: {r['message']}" for r in report.get("rejected", [])[:5]]
        results.append({"sheet": mapping.worksheet, "event_type": mapping.category.name, "ok": report["ok"],
                        "rows": report.get("rows_read", 0), "open": report.get("rows_open", 0), "rejected": report.get("rows_rejected", 0),
                        "flagged": report.get("rows_flagged", 0), "problems": problems[:8], "warnings": report.get("warnings", [])[:5]})
    ok = all(r["ok"] for r in results)
    message = f"Workbook reachable ({len(sheets)} sheets). " + ("All sheets match the layout." if ok else "Some sheets need attention.")
    source.last_checked_at, source.last_check_ok, source.last_check_message = timezone.now(), ok, message[:500]
    source.save(update_fields=["last_checked_at", "last_check_ok", "last_check_message"])
    return {"ok": ok, "message": message, "sheets": results}


@api_view(["POST"])
@permission_classes([ADMIN])
def plant_workbook_link(request, pk):
    plant = _plant(pk)
    source = layout.plant_workbook(plant)
    try:
        config = connectors.resolve_share_link(str(request.data.get("url", "")))
    except connectors.SourceError as exc:
        _bad(str(exc), "url")
    source.provider, source.config = DataSource.PROVIDER_GRAPH, config
    source.uploaded_file_name = ""
    source.save()
    connectors.upload_path(source).unlink(missing_ok=True)
    audit.record("plant_workbook_link", obj=plant, changes={"file": config.get("display_path")})
    return Response({"plant": layout.plant_payload(plant), "check": _check_workbook(source)})


@api_view(["POST"])
@permission_classes([ADMIN])
def plant_workbook_upload(request, pk):
    plant = _plant(pk)
    source = layout.plant_workbook(plant)
    uploaded = request.FILES.get("file")
    if uploaded is None:
        _bad("Choose an Excel file.", "file")
    source.provider, source.config = DataSource.PROVIDER_UPLOAD, {}
    try:
        source.uploaded_file_name = connectors.save_upload(source, uploaded)
    except connectors.SourceError as exc:
        _bad(str(exc), "file")
    source.uploaded_at = timezone.now()
    source.save()
    audit.record("plant_workbook_upload", obj=plant, changes={"file": source.uploaded_file_name})
    return Response({"plant": layout.plant_payload(plant), "check": _check_workbook(source)})


@api_view(["POST"])
@permission_classes([ADMIN])
def plant_check(request, pk):
    plant = _plant(pk)
    return Response({"plant": layout.plant_payload(plant), "check": _check_workbook(layout.plant_workbook(plant))})


@api_view(["PUT"])
@permission_classes([ADMIN])
def plant_departments(request, pk):
    """Replace the plant's department list with To/CC recipients. Departments left out are deactivated."""
    plant = _plant(pk)
    rows = request.data.get("departments")
    if not isinstance(rows, list):
        _bad("Send a list of departments.", "departments")
    seen = set()
    with transaction.atomic():
        for row in rows:
            dept = Department.objects.filter(pk=row.get("department")).first()
            if dept is None:
                _bad("Unknown department.", "departments")
            if dept.pk in seen:
                _bad(f"{dept.name} is listed twice.", "departments")
            seen.add(dept.pk)
            to = _clean_emails(row.get("to"), f"{dept.name} To")
            cc = [e for e in _clean_emails(row.get("cc"), f"{dept.name} CC") if e.lower() not in {t.lower() for t in to}]
            active = bool(row.get("active", True))
            if active and not to:
                _bad(f"{dept.name}: add at least one 'To' address (or switch the department off).", "departments")
            RecipientGroup.objects.update_or_create(unit=plant, department=dept, defaults={"to_emails": to, "cc_emails": cc, "active": active})
        RecipientGroup.objects.filter(unit=plant).exclude(department_id__in=seen).update(active=False)
    SystemSettings.bump_config_version()
    audit.record("plant_departments", obj=plant, changes={"departments": rows})
    return Response(layout.plant_payload(plant))


# --- Users (admin + plant QA heads) -------------------------------------------------------------

def _user_payload(user):
    profile = getattr(user, "profile", None)
    role = profile.role.key if profile and profile.role else ("admin" if user.is_superuser else "")
    return {"id": user.pk, "username": user.username, "first_name": user.first_name, "last_name": user.last_name, "email": user.email,
            "is_active": user.is_active, "role": "admin" if role == "admin" else ("plant_qa" if role == "plant_qa" else role),
            "plants": sorted({s.unit_id for s in user.data_scopes.all() if s.unit_id}), "last_login": user.last_login}


def _save_user(user, data, creating):
    role_key = data.get("role", "plant_qa")
    if role_key not in ("admin", "plant_qa"):
        _bad("Role must be admin or plant_qa.", "role")
    plants_ids = [int(p) for p in data.get("plants", [])]
    if role_key == "plant_qa" and not plants_ids:
        _bad("Choose the plant(s) this QA head is responsible for.", "plants")
    for field in ("username", "first_name", "last_name", "email"):
        if field in data:
            setattr(user, field, str(data[field]).strip()[:150])
    if not user.username:
        _bad("Enter a username.", "username")
    if User.objects.exclude(pk=user.pk).filter(username__iexact=user.username).exists():
        _bad("This username is taken.", "username")
    if "is_active" in data:
        user.is_active = bool(data["is_active"])
    password = data.get("password") or ""
    if creating and not password:
        _bad("Set an initial password.", "password")
    if password:
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            raise ValidationError({"password": exc.messages})
        user.set_password(password)
    with transaction.atomic():
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = Role.objects.get(key=role_key)
        profile.all_data_scope = role_key == "admin"
        profile.save()
        user.data_scopes.all().delete()
        for pid in plants_ids if role_key == "plant_qa" else []:
            UserScope.objects.create(user=user, unit=_plant(pid))
    if password or not user.is_active:
        AuthToken.objects.filter(user=user).delete()
    return user


@api_view(["GET", "POST"])
@permission_classes([HasPermission.of("admin.users")])
def users(request):
    if request.method == "POST":
        user = _save_user(User(), request.data, creating=True)
        audit.record("create", obj=user, changes={"role": request.data.get("role"), "plants": request.data.get("plants")})
        return Response(_user_payload(user), status=201)
    qs = User.objects.select_related("profile__role").prefetch_related("data_scopes").order_by("username")
    return Response([_user_payload(u) for u in qs])


@api_view(["PATCH"])
@permission_classes([HasPermission.of("admin.users")])
def user_detail(request, pk):
    user = User.objects.filter(pk=pk).first()
    if user is None:
        raise NotFound()
    if user == request.user and (request.data.get("role") not in (None, "admin") or request.data.get("is_active") is False):
        _bad("You cannot remove your own admin access.")
    current = _user_payload(user)
    user = _save_user(user, {"role": current["role"] or "plant_qa", "plants": current["plants"], **request.data}, creating=False)
    audit.record("permission_change", obj=user, changes={"role": request.data.get("role"), "plants": request.data.get("plants")})
    return Response(_user_payload(user))


# --- Today's preview (scoped) -------------------------------------------------------------------

@api_view(["GET"])
def today_preview(request):
    """What each department will receive today. Admins can sync first; plant QA heads see their plants only."""
    user = request.user
    if not (has_perm(user, "runs.view") or has_perm(user, "admin.configure")):
        raise PermissionDenied()
    sync = request.query_params.get("sync") == "1"
    try:
        if sync:
            if not has_perm(user, "runs.manage"):
                raise PermissionDenied("Only the admin can read the workbooks now.")
            preview = workflow.dry_run(sync=True, triggered_by=user.username)
        else:
            preview = workflow.plan_to_preview(workflow.build_plan(sync=False))
    except (workflow.WorkflowError, TemplateError) as exc:
        _bad(str(exc))
    if not has_full_scope(user):
        scopes = list(user.data_scopes.select_related("unit"))
        names = ({u.name for u in Unit.objects.all()} if any(s.unit_id is None for s in scopes) else {s.unit.name for s in scopes})
        preview["groups"] = [g for g in preview["groups"] if g["unit"] in names]
        preview["unrouted"] = [i for i in preview["unrouted"] if i["unit"] in names]
        preview["untrusted"] = [u for u in preview["untrusted"] if any(n in u for n in names) or "all units" in u]
        preview.pop("sync", None)
    preview["totals"] = {
        "groups": len(preview["groups"]),
        "reminder": sum(1 for g in preview["groups"] if g["action"] == "reminder"),
        "no_pending": sum(1 for g in preview["groups"] if g["action"] == "no_pending"),
        "held": sum(1 for g in preview["groups"] if g["action"] == "held"),
        "included_items": sum(len(g["included"]) for g in preview["groups"]),
    }
    info = schedule_info()
    preview["schedule"] = {"next_run_at": info["next_run_at"], "schedule_enabled": info["schedule_enabled"],
                           "active": SystemSettings.load().reminders_active}
    return Response(preview)
