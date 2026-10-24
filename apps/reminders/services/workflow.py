"""Daily reminder workflow.

1. Acquire the run lock.            5. Evaluate reminder rules.
2. Sync configured sources.          6. Build one consolidated email per unit-department.
3. Decide which data is trusted.     7/8. Send reminders, or "no pending" only when trusted.
4. Group by unit, then department.   9/10. Hold affected groups, alert admins, track per group.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone
from django.utils.html import escape

from apps.catalog.models import Category, RecipientGroup, Relationship
from apps.core import locks
from apps.core.models import SystemSettings
from apps.core.timeutils import business_today
from apps.records.models import Record
from apps.reminders.models import DailyRun, Delivery, EmailTemplate, ReminderRule, SenderProfile
from apps.reminders.services import compose, delivery, providers
from apps.reminders.services.eligibility import REASON_LABELS
from apps.reminders.services.rules import Evaluation, RuleSet, evaluate_record
from apps.sources.models import SourceMapping, SyncRun
from apps.sources.sync import sync_all

logger = logging.getLogger("qa.workflow")
LOCK_NAME = "daily_reminders"
ALL_UNITS = "*"


class WorkflowError(Exception):
    pass


@dataclass
class GroupPlan:
    group: RecipientGroup
    items: list = field(default_factory=list)  # eligible Evaluations
    excluded: list = field(default_factory=list)
    flagged: list = field(default_factory=list)  # evaluations blocked by data issues
    action: str = "none"  # reminder | no_pending | held | none
    hold_reasons: list = field(default_factory=list)
    email: object = None
    outcome: object = None


@dataclass
class Plan:
    run_date: object
    settings: SystemSettings
    template: EmailTemplate
    groups: list
    untrusted: dict  # category_id -> [(unit_id | ALL_UNITS, reason)]
    untrusted_reasons: list
    unrouted: list
    sync_results: dict
    warnings: list


def active_template():
    template = EmailTemplate.objects.filter(is_active=True).first()
    if template is None:
        raise WorkflowError("No active email template is configured.")
    return template


def default_sender():
    profile = SenderProfile.objects.filter(is_default=True, active=True).first() or SenderProfile.objects.filter(active=True).first()
    if profile is None:
        raise WorkflowError("No active email sender profile is configured.")
    return profile


def _active_rules():
    return list(ReminderRule.objects.filter(active=True, category__active=True).select_related("category").prefetch_related("units", "departments"))


def _relevant_categories(ruleset):
    ids = set(ruleset.category_ids)
    for rel in Relationship.objects.filter(active=True, child_category_id__in=list(ids)):
        ids.add(rel.parent_category_id)  # parent context comes from the parent category's source
    return ids


def _trust(relevant_category_ids, rule_category_ids, sync_results, synced_now, warnings):
    """Return {category_id: [(unit_id | ALL_UNITS, reason)]} for data that is not complete and validated.

    A reminder category without any active mapping is untrusted. A parent-only (context) category
    without a mapping only produces a warning; a parent mapping that exists but fails is untrusted.
    """
    untrusted = {}
    mappings = SourceMapping.objects.filter(category_id__in=relevant_category_ids, active=True, data_source__active=True).select_related(
        "category", "fixed_unit", "data_source")
    covered = set()
    for mapping in mappings:
        covered.add(mapping.category_id)
        if synced_now:
            run = sync_results.get(mapping.pk)
            status, message = (run.status, run.message) if run else ("missing", "Not synced in this run.")
        else:
            status, message = mapping.last_status or "never synced", mapping.last_error or ""
        if status != SyncRun.STATUS_SUCCESS:
            scope = mapping.fixed_unit_id or ALL_UNITS
            where = f"unit {mapping.fixed_unit.name}" if mapping.fixed_unit_id else "all units"
            reason = f"{mapping.category.name} source '{mapping}' {status} ({where})" + (f": {message}" if message else "")
            untrusted.setdefault(mapping.category_id, []).append((scope, reason))
    for cat in Category.objects.filter(pk__in=relevant_category_ids - covered):
        if cat.pk in rule_category_ids:
            untrusted.setdefault(cat.pk, []).append((ALL_UNITS, f"{cat.name} has an active reminder rule but no active source mapping."))
        else:
            warnings.append(f"{cat.name} provides parent context but has no active source mapping; parent details will be missing.")
    return untrusted


def build_plan(run_date=None, sync=True, daily_run=None, triggered_by="", template=None) -> Plan:
    settings_obj = SystemSettings.load()
    run_date = run_date or business_today()
    template = template or active_template()
    ruleset = RuleSet(_active_rules())
    relevant = _relevant_categories(ruleset)
    warnings = []
    if not ruleset.category_ids:
        warnings.append("No active reminder rules are configured.")

    sync_results = sync_all(daily_run=daily_run, triggered_by=triggered_by, categories=list(relevant)) if sync else {}
    untrusted = _trust(relevant, ruleset.category_ids, sync_results, sync, warnings)
    untrusted_reasons = [reason for entries in untrusted.values() for _, reason in entries]

    records = Record.objects.filter(category_id__in=ruleset.category_ids).select_related(
        "category", "unit", "department", "parent", "parent__category")
    groups = list(RecipientGroup.objects.filter(active=True, unit__active=True, department__active=True).select_related(
        "unit", "department", "sender_profile").order_by("unit__name", "department__name"))
    plans = {(g.unit_id, g.department_id): GroupPlan(group=g) for g in groups}
    unrouted = []

    for record in records:
        ev = evaluate_record(record, ruleset, run_date)
        plan = plans.get((record.unit_id, record.department_id))
        if plan is None:
            if ev.eligible or ev.reason in ("data_issue", "invalid_date", "missing_date"):
                unrouted.append(ev)
            continue
        if ev.eligible:
            plan.items.append(ev)
        else:
            plan.excluded.append(ev)
            if ev.reason in ("data_issue", "invalid_date", "missing_date"):
                plan.flagged.append(ev)

    for plan in plans.values():
        unit_id = plan.group.unit_id
        for entries in untrusted.values():
            plan.hold_reasons.extend(reason for scope, reason in entries if scope == ALL_UNITS or scope == unit_id)
        plan.hold_reasons = list(dict.fromkeys(plan.hold_reasons))
        if plan.hold_reasons:
            plan.action = "held"
        elif plan.items:
            plan.action = "reminder"
        elif plan.flagged:
            plan.action = "held"
            plan.hold_reasons.append(f"{len(plan.flagged)} record(s) have missing/invalid data and need review; 'no pending' email suppressed.")
        elif settings_obj.send_no_pending_email:
            plan.action = "no_pending"
        else:
            plan.action = "none"
        window = max([ev.rule.window_days for ev in plan.items if ev.rule] or [settings_obj.default_window_days])
        if plan.action == "reminder":
            plan.email = compose.compose_reminder(plan.group, plan.items, template, run_date, window)
        elif plan.action == "no_pending":
            plan.email = compose.compose_no_pending(plan.group, template, run_date, window)

    return Plan(run_date, settings_obj, template, list(plans.values()), untrusted, untrusted_reasons, unrouted, sync_results, warnings)


def _ev_summary(ev):
    return {
        "record_id": ev.record.pk,
        "category": ev.record.category.name,
        "kind": ev.record.category.kind,
        "source_id": ev.record.source_id,
        "title": ev.record.title,
        "status": ev.record.status,
        "unit": ev.record.unit.name,
        "department": ev.record.department.name,
        "target_date": ev.target_date.isoformat() if ev.target_date else None,
        "reason": ev.reason,
        "reason_label": REASON_LABELS.get(ev.reason, ev.reason),
        "detail": ev.detail,
        "rule": ev.rule.name if ev.rule else None,
        "parent": f"{ev.record.parent.category.name} {ev.record.parent.source_id}" if ev.record.parent_id and ev.record.parent else None,
    }


def _sync_summary(sync_results):
    return [{"mapping_id": mid, "mapping": str(run.mapping), "status": run.status, "rows_read": run.rows_read,
             "rows_imported": run.rows_imported, "rows_rejected": run.rows_rejected, "rows_flagged": run.rows_flagged,
             "message": run.message} for mid, run in sync_results.items()]


def plan_to_preview(plan: Plan, include_html=True):
    groups = []
    for gp in plan.groups:
        groups.append({
            "group_id": gp.group.pk,
            "unit": gp.group.unit.name,
            "department": gp.group.department.name,
            "action": gp.action,
            "hold_reasons": gp.hold_reasons,
            "to": gp.group.to_emails,
            "cc": gp.group.cc_emails,
            "included": [_ev_summary(ev) for ev in gp.items],
            "excluded": [_ev_summary(ev) for ev in gp.excluded],
            "email": {"subject": gp.email.subject, "html": gp.email.html if include_html else None} if gp.email else None,
        })
    return {
        "run_date": plan.run_date.isoformat(),
        "sync": _sync_summary(plan.sync_results),
        "untrusted": plan.untrusted_reasons,
        "unrouted": [_ev_summary(ev) for ev in plan.unrouted],
        "warnings": plan.warnings,
        "groups": groups,
        "totals": {
            "groups": len(groups),
            "reminder": sum(1 for g in groups if g["action"] == "reminder"),
            "no_pending": sum(1 for g in groups if g["action"] == "no_pending"),
            "held": sum(1 for g in groups if g["action"] == "held"),
            "included_items": sum(len(g["included"]) for g in groups),
        },
    }


def dry_run(sync=False, triggered_by=""):
    """Evaluate everything and render emails without sending or touching the ledger."""
    daily_run = DailyRun.objects.create(run_date=business_today(), kind=DailyRun.KIND_DRY_RUN, triggered_by=triggered_by[:150],
                                        config_version=SystemSettings.load().config_version)
    try:
        plan = build_plan(sync=sync, daily_run=daily_run if sync else None, triggered_by=triggered_by)
    except WorkflowError as exc:
        daily_run.status, daily_run.finished_at, daily_run.summary = DailyRun.STATUS_FAILED, timezone.now(), {"error": str(exc)}
        daily_run.save()
        raise
    preview = plan_to_preview(plan)
    daily_run.status = DailyRun.STATUS_COMPLETED
    daily_run.finished_at = timezone.now()
    daily_run.summary = {"totals": preview["totals"], "untrusted": preview["untrusted"], "sync": preview["sync"], "warnings": preview["warnings"]}
    daily_run.save()
    preview["daily_run_id"] = daily_run.pk
    return preview


def run_daily(kind=DailyRun.KIND_SCHEDULED, triggered_by="scheduler", sync=True, require_active=True, only_group_ids=None):
    settings_obj = SystemSettings.load()
    run_date = business_today()
    daily_run = DailyRun.objects.create(run_date=run_date, kind=kind, triggered_by=triggered_by[:150], config_version=settings_obj.config_version)

    if require_active and not settings_obj.reminders_active:
        return _finish(daily_run, DailyRun.STATUS_SKIPPED, {"reason": "Reminders are not activated. Complete Quick Start and activate."})
    try:
        lock_holder = locks.acquire(LOCK_NAME, ttl_minutes=60)
    except locks.LockNotAcquired:
        return _finish(daily_run, DailyRun.STATUS_SKIPPED, {"reason": "Another reminder run is in progress."})

    try:
        plan = build_plan(run_date=run_date, sync=sync, daily_run=daily_run, triggered_by=triggered_by)
        default_profile = default_sender()
        outcomes = []
        for gp in plan.groups:
            if only_group_ids is not None and gp.group.pk not in only_group_ids:
                continue
            if gp.action == "none":
                continue
            key = delivery.group_key(run_date, gp.group.unit_id, gp.group.department_id)
            entry = delivery.ledger_entry(key, run_date, Delivery.KIND_REMINDER, gp.group.unit, gp.group.department,
                                          daily_run, settings_obj.config_version)
            if gp.action == "held":
                gp.outcome = delivery.mark_held(entry, " | ".join(gp.hold_reasons), daily_run)
            else:
                counts = {"total": len(gp.items)}
                for ev in gp.items:
                    counts[ev.record.category.kind] = counts.get(ev.record.category.kind, 0) + 1
                profile = gp.group.sender_profile if gp.group.sender_profile and gp.group.sender_profile.active else default_profile
                email_kind = Delivery.KIND_REMINDER if gp.action == "reminder" else Delivery.KIND_NO_PENDING
                gp.outcome = delivery.deliver(entry, gp.email, profile, email_kind, counts, settings_obj.max_send_attempts,
                                              daily_run, settings_obj.config_version)
            outcomes.append(gp)

        summary = _summarize(plan, outcomes)
        problems = summary["failed"] or summary["uncertain"] or summary["held"] or plan.untrusted_reasons or plan.unrouted
        status = DailyRun.STATUS_ATTENTION if problems else DailyRun.STATUS_COMPLETED
        _finish(daily_run, status, summary)
        if problems:
            send_admin_alert(daily_run, plan, summary)
        return daily_run
    except WorkflowError as exc:
        _finish(daily_run, DailyRun.STATUS_FAILED, {"error": str(exc)})
        send_admin_alert(daily_run, None, {"error": str(exc)})
        return daily_run
    except Exception:
        logger.exception("Daily run failed", extra={"daily_run_id": daily_run.pk})
        _finish(daily_run, DailyRun.STATUS_FAILED, {"error": "Unexpected error; see server logs."})
        send_admin_alert(daily_run, None, {"error": "Unexpected error during the daily run; see server logs."})
        return daily_run
    finally:
        locks.release(LOCK_NAME, lock_holder)


def _summarize(plan, outcomes):
    rows = []
    counters = {"sent": 0, "failed": 0, "uncertain": 0, "held": 0, "skipped": 0}
    for gp in outcomes:
        action = gp.outcome.action if gp.outcome else "none"
        bucket = action if action in counters else ("skipped" if action.startswith("skipped") else action)
        counters[bucket] = counters.get(bucket, 0) + 1
        rows.append({"group_id": gp.group.pk, "unit": gp.group.unit.name, "department": gp.group.department.name,
                     "planned": gp.action, "outcome": action, "items": len(gp.items), "flagged": len(gp.flagged),
                     "detail": (gp.outcome.detail if gp.outcome else "")[:300], "hold_reasons": gp.hold_reasons})
    return {**counters, "groups": rows, "sync": _sync_summary(plan.sync_results), "untrusted": plan.untrusted_reasons,
            "unrouted": len(plan.unrouted), "unrouted_items": [_ev_summary(ev) for ev in plan.unrouted[:50]], "warnings": plan.warnings}


def _finish(daily_run, status, summary):
    daily_run.status = status
    daily_run.summary = summary
    daily_run.finished_at = timezone.now()
    daily_run.save()
    logger.info("Daily run finished", extra={"daily_run_id": daily_run.pk, "status": status, "kind": daily_run.kind})
    return daily_run


def send_admin_alert(daily_run, plan, summary):
    """Best-effort operational alert to configured admin recipients."""
    settings_obj = SystemSettings.load()
    recipients = settings_obj.alert_recipients or []
    if not recipients:
        logger.warning("Run needs attention but no alert recipients are configured", extra={"daily_run_id": daily_run.pk})
        return None
    profile = settings_obj.alert_sender_profile or SenderProfile.objects.filter(is_default=True, active=True).first()
    if profile is None:
        return None
    lines = [f"Daily reminder run #{daily_run.pk} for {daily_run.run_date:%d-%b-%Y} finished with status: {daily_run.get_status_display()}."]
    if summary.get("error"):
        lines.append(f"Error: {summary['error']}")
    for reason in summary.get("untrusted", []):
        lines.append(f"Source problem: {reason}")
    for row in summary.get("groups", []):
        if row["outcome"] not in ("sent", "skipped_sent"):
            lines.append(f"{row['unit']} / {row['department']}: {row['outcome']} {row['detail'] or ''} {' | '.join(row['hold_reasons'])}".strip())
    if summary.get("unrouted"):
        lines.append(f"{summary['unrouted']} record(s) belong to a unit/department with no active recipients.")
    text = "\n".join(lines)
    html = "<p>" + "</p><p>".join(escape(line) for line in lines) + "</p>"
    email = compose.EmailContent(subject=f"[QA Reminders] Attention needed - run {daily_run.run_date:%d-%b-%Y}",
                                 html=html, text=text, to=recipients, cc=[])
    entry = delivery.ledger_entry(f"alert:{daily_run.run_date.isoformat()}:run{daily_run.pk}", daily_run.run_date, Delivery.KIND_ALERT,
                                  daily_run=daily_run)
    try:
        return delivery.deliver(entry, email, profile, Delivery.KIND_ALERT, max_attempts=1, daily_run=daily_run)
    except Exception:
        logger.exception("Failed to send admin alert")
        return None


def send_test_email(test_recipient, group_id=None, profile=None, triggered_by=""):
    """Send a preview to ONE test recipient only. Production recipients are never contacted."""
    profile = profile or default_sender()
    template = active_template()
    run_date = business_today()
    if group_id:
        plan = build_plan(sync=False)
        gp = next((g for g in plan.groups if g.group.pk == int(group_id)), None)
        if gp is None:
            raise WorkflowError("Recipient group not found or inactive.")
        if gp.action == "reminder":
            email = compose.compose_reminder(gp.group, gp.items, template, run_date, gp.items[0].rule.window_days, recipients=([test_recipient], []))
        else:
            email = compose.compose_no_pending(gp.group, template, run_date, plan.settings.default_window_days, recipients=([test_recipient], []))
        label = f"{gp.group.unit.name} / {gp.group.department.name}"
    else:
        email = sample_email(template, recipients=([test_recipient], []))
        label = "sample data"
    email.subject = f"[TEST] {email.subject}"[:255]
    entry = delivery.ledger_entry(f"test:{uuid.uuid4().hex}", run_date, Delivery.KIND_TEST)
    Delivery.objects.filter(pk=entry.pk).update(unit_name=label[:128])
    outcome = delivery.deliver(entry, email, profile, Delivery.KIND_TEST, max_attempts=1)
    provider = providers.get_provider(profile)
    return outcome, provider.delivers


def _sample_group():
    unit = SimpleNamespace(name="Sample Unit", code="UNIT1")
    dept = SimpleNamespace(name="Quality Assurance", code="QA")
    return SimpleNamespace(unit=unit, department=dept, to_emails=["qa-team@example.com"], cc_emails=["qa-head@example.com"])


def sample_no_pending(template):
    return compose.compose_no_pending(_sample_group(), template, business_today(), SystemSettings.load().default_window_days)


def sample_email(template, recipients=None):
    """Render the template with synthetic data (for preview and test emails)."""
    today = business_today()
    group = _sample_group()
    unit, dept = group.unit, group.department
    cats = {
        "qms": SimpleNamespace(pk=-1, name="Change Control", kind=Category.KIND_QMS, sort_order=1, display_fields=["title"], title_field="title"),
        "audit": SimpleNamespace(pk=-2, name="Audit Observation", kind=Category.KIND_AUDIT, sort_order=2, display_fields=["title"], title_field="title"),
        "action": SimpleNamespace(pk=-3, name="Audit Action Item", kind=Category.KIND_ACTION, sort_order=3, display_fields=["title"], title_field="title"),
    }
    parent = SimpleNamespace(source_id="AUD-2026-004", title="Supplier audit - API vendor", category=SimpleNamespace(name="Audit"))

    def ev(cat, sid, title, days, with_parent=False):
        rec = SimpleNamespace(pk=0, source_id=sid, title=title, status="Open", category=cats[cat], category_id=cats[cat].pk,
                              unit=unit, department=dept, data={"title": title}, parent=parent if with_parent else None,
                              parent_id=1 if with_parent else None)
        return Evaluation(rec, True, "due_today" if days == 0 else "within_window", None, today + timedelta(days=days))

    items = [ev("qms", "CC-2026-0142", "Change of HPLC column supplier", 0),
             ev("audit", "OBS-2026-017", "Inadequate line clearance records", 3, True),
             ev("action", "ACT-2026-051", "Retrain packing staff on line clearance", 5, True)]
    return compose.compose_reminder(group, items, template, today, 7, recipients=recipients or (group.to_emails, group.cc_emails))


def last_successful_run():
    return DailyRun.objects.filter(kind__in=[DailyRun.KIND_SCHEDULED, DailyRun.KIND_MANUAL, DailyRun.KIND_RETRY],
                                   status=DailyRun.STATUS_COMPLETED).first()
