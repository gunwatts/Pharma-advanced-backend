"""Quick Start wizard: progress (save/resume), readiness checks and activation."""

from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import HasPermission
from apps.catalog.models import RecipientGroup
from apps.core import audit
from apps.core.models import SystemSettings
from apps.records.models import Record
from apps.reminders.models import DailyRun, Delivery, EmailTemplate, JobSchedule, ReminderRule, SenderProfile
from apps.reminders.serializers import DailyRunSerializer
from apps.reminders.services.scheduler import next_run_at
from apps.reminders.services.templating import validate_template_text
from apps.reminders.services.workflow import last_successful_run
from apps.sources.models import DataSource, SourceMapping

STEPS = ["sender", "source", "organization", "mappings", "rules", "template", "test", "activate"]


def _check(ok, message, warnings=None, blocking=True):
    return {"ok": bool(ok), "message": message, "warnings": warnings or [], "blocking": blocking}


def readiness():
    settings_obj = SystemSettings.load()
    checks = {}

    senders = SenderProfile.objects.filter(active=True)
    tested = senders.filter(last_test_status="connected")
    warnings = []
    if senders.filter(provider=SenderProfile.PROVIDER_CONSOLE).exists():
        warnings.append("A log-only (console) sender is active: emails will NOT be delivered.")
    if not senders.filter(is_default=True).exists() and senders.exists():
        warnings.append("No sender is marked as default; the first active sender will be used.")
    checks["sender"] = _check(tested.exists(), f"{tested.count()} of {senders.count()} active sender(s) passed the connection test.", warnings)

    sources = DataSource.objects.filter(active=True)
    checked = sources.filter(last_check_ok=True)
    checks["source"] = _check(checked.exists(), f"{checked.count()} of {sources.count()} active source(s) are reachable.")

    groups = RecipientGroup.objects.filter(active=True)
    warnings = [f"{g}: no 'To' recipients." for g in groups if not g.to_emails]
    routed = set(groups.values_list("unit_id", "department_id"))
    unrouted = (Record.objects.values("unit__name", "department__name", "unit_id", "department_id").annotate(n=Count("id")))
    for row in unrouted:
        if (row["unit_id"], row["department_id"]) not in routed:
            warnings.append(f"{row['n']} record(s) in {row['unit__name']} / {row['department__name']} have no recipient group.")
    checks["organization"] = _check(groups.exists(), f"{groups.count()} active unit-department recipient group(s).", warnings)

    mappings = SourceMapping.objects.filter(active=True)
    valid = mappings.filter(last_validation_ok=True)
    synced = mappings.filter(last_status="success")
    warnings = [f"Mapping '{m}' last sync: {m.last_status or 'never'}." for m in mappings if m.last_status != "success"]
    checks["mappings"] = _check(valid.exists() and synced.exists(),
                                f"{valid.count()} validated and {synced.count()} successfully synced of {mappings.count()} mapping(s).", warnings)

    rules = ReminderRule.objects.filter(active=True)
    mapped_categories = set(mappings.values_list("category_id", flat=True))
    warnings = [f"Rule '{r.name}' targets {r.category.name}, which has no active mapping." for r in rules.select_related("category")
                if r.category_id not in mapped_categories]
    checks["rules"] = _check(rules.exists(), f"{rules.count()} active reminder rule(s).", warnings)

    template = EmailTemplate.objects.filter(is_active=True).first()
    problems = []
    if template:
        for key in ("subject", "intro_text", "outro_text", "no_pending_subject", "no_pending_text"):
            problems += validate_template_text(getattr(template, key))
    checks["template"] = _check(template is not None and not problems,
                                f"Active template: {template.name}" if template else "No active template.", problems)

    dry = DailyRun.objects.filter(kind=DailyRun.KIND_DRY_RUN, status=DailyRun.STATUS_COMPLETED,
                                  config_version=settings_obj.config_version).first()
    test_sent = Delivery.objects.filter(kind=Delivery.KIND_TEST, state=Delivery.STATE_SENT).exists()
    warnings = [] if dry else ["Run a dry run after your latest configuration change."]
    if not test_sent:
        warnings.append("Send a test email to a test recipient.")
    done = dry is not None and test_sent
    checks["test"] = _check(done, "Dry run and test email completed." if done else "Dry run and test email pending.", warnings)

    active = settings_obj.reminders_active
    checks["activate"] = _check(active, "Reminders are active." if active else "Reminders are not active.", blocking=False)
    return checks


def schedule_info():
    schedule = JobSchedule.objects.filter(job=JobSchedule.JOB_DAILY).first()
    last = last_successful_run()
    nxt = next_run_at(schedule) if schedule else None
    return {
        "next_run_at": nxt.isoformat() if nxt else None,
        "schedule_enabled": bool(schedule and schedule.enabled),
        "last_successful_run": DailyRunSerializer(last).data if last else None,
        "last_run": DailyRunSerializer(DailyRun.objects.exclude(kind=DailyRun.KIND_DRY_RUN).first()).data
        if DailyRun.objects.exclude(kind=DailyRun.KIND_DRY_RUN).exists() else None,
    }


@api_view(["GET"])
@permission_classes([HasPermission.of("admin.configure")])
def setup_status(request):
    settings_obj = SystemSettings.load()
    checks = readiness()
    ready = all(c["ok"] for key, c in checks.items() if c["blocking"])
    return Response({"steps": STEPS, "checks": checks, "ready_to_activate": ready, "active": settings_obj.reminders_active,
                     "activated_at": settings_obj.activated_at, "wizard_state": settings_obj.wizard_state, **schedule_info()})


@api_view(["PATCH"])
@permission_classes([HasPermission.of("admin.configure")])
def setup_progress(request):
    step = request.data.get("current_step")
    if step not in STEPS:
        raise ValidationError({"current_step": "Unknown step."})
    settings_obj = SystemSettings.load()
    state = dict(settings_obj.wizard_state or {})
    state["current_step"] = step
    state["updated_at"] = timezone.now().isoformat()
    SystemSettings.objects.filter(pk=1).update(wizard_state=state)
    return Response(state)


@api_view(["POST"])
@permission_classes([HasPermission.of("admin.configure")])
def activate(request):
    if request.data.get("confirm") is not True:
        raise ValidationError({"confirm": "Explicit confirmation is required to activate production reminders."})
    checks = readiness()
    failing = [key for key, c in checks.items() if c["blocking"] and not c["ok"]]
    if failing:
        raise ValidationError({"detail": "Complete these steps before activating: " + ", ".join(failing), "failing": failing})
    SystemSettings.objects.filter(pk=1).update(reminders_active=True, activated_at=timezone.now(), activated_by=request.user)
    audit.record("activate_reminders", obj=SystemSettings.load())
    return Response({"active": True, **schedule_info()})


@api_view(["POST"])
@permission_classes([HasPermission.of("admin.configure")])
def deactivate(request):
    SystemSettings.objects.filter(pk=1).update(reminders_active=False)
    audit.record("deactivate_reminders", obj=SystemSettings.load())
    return Response({"active": False})
