"""Idempotent delivery through the persistent ledger.

Guarantees:
* At most one successful email per idempotency key (run date + unit + department).
* A key in "sent" or "uncertain" state is never re-sent automatically.
  Uncertain deliveries need an admin to reconcile them (mark sent / authorize retry).
* The ledger row is claimed with a conditional UPDATE before sending, so two
  processes cannot both send the same email.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.logging import redact
from apps.reminders.models import Delivery
from apps.reminders.services import providers

logger = logging.getLogger("qa.delivery")

STALE_IN_PROGRESS = timedelta(minutes=15)
SENDABLE_STATES = {Delivery.STATE_PENDING, Delivery.STATE_FAILED, Delivery.STATE_HELD, Delivery.STATE_RETRY_AUTHORIZED}


@dataclass
class DeliveryOutcome:
    delivery: Delivery
    action: str  # sent | failed | uncertain | skipped_sent | skipped_uncertain | skipped_in_progress | skipped_max_attempts | held
    detail: str = ""


def group_key(run_date, unit_id, department_id):
    return f"{run_date.isoformat()}:u{unit_id}:d{department_id}"


def _get_or_create(key, defaults):
    try:
        with transaction.atomic():
            return Delivery.objects.get_or_create(idempotency_key=key, defaults=defaults)[0]
    except IntegrityError:
        return Delivery.objects.get(idempotency_key=key)


def ledger_entry(key, run_date, kind, unit=None, department=None, daily_run=None, config_version=0):
    return _get_or_create(key, {
        "run_date": run_date, "kind": kind, "unit": unit, "department": department,
        "unit_name": unit.name if unit else "", "department_name": department.name if department else "",
        "daily_run": daily_run, "config_version": config_version,
    })


def mark_held(entry: Delivery, reason, daily_run=None):
    """Record that a group's email was held (e.g. source incomplete). Never overrides sent/uncertain."""
    updated = Delivery.objects.filter(pk=entry.pk, state__in=[Delivery.STATE_PENDING, Delivery.STATE_HELD, Delivery.STATE_FAILED]).update(
        state=Delivery.STATE_HELD, hold_reason=reason[:500], daily_run=daily_run, updated_at=timezone.now())
    entry.refresh_from_db()
    return DeliveryOutcome(entry, "held" if updated else f"skipped_{entry.state}", reason)


def deliver(entry: Delivery, email, profile, kind, item_counts=None, max_attempts=3, daily_run=None, config_version=0) -> DeliveryOutcome:
    entry.refresh_from_db()
    now = timezone.now()

    if entry.state == Delivery.STATE_SENT:
        return DeliveryOutcome(entry, "skipped_sent", "Already sent for this run date.")
    if entry.state == Delivery.STATE_UNCERTAIN:
        return DeliveryOutcome(entry, "skipped_uncertain", "Previous attempt has uncertain delivery; reconcile before retrying.")
    if entry.state == Delivery.STATE_IN_PROGRESS:
        if entry.last_attempt_at and now - entry.last_attempt_at > STALE_IN_PROGRESS:
            Delivery.objects.filter(pk=entry.pk, state=Delivery.STATE_IN_PROGRESS).update(
                state=Delivery.STATE_UNCERTAIN, error="Send attempt was interrupted; delivery is uncertain.")
            entry.refresh_from_db()
            return DeliveryOutcome(entry, "skipped_uncertain", "Interrupted attempt marked uncertain.")
        return DeliveryOutcome(entry, "skipped_in_progress", "Another process is sending this email.")
    if entry.state == Delivery.STATE_FAILED and entry.attempts >= max_attempts:
        return DeliveryOutcome(entry, "skipped_max_attempts", f"Maximum attempts ({max_attempts}) reached; use manual retry.")
    if entry.state not in SENDABLE_STATES:
        return DeliveryOutcome(entry, f"skipped_{entry.state}")

    provider = providers.get_provider(profile)
    claimed = Delivery.objects.filter(pk=entry.pk, state=entry.state).update(
        state=Delivery.STATE_IN_PROGRESS, kind=kind, attempts=F("attempts") + 1, last_attempt_at=now,
        subject=email.subject[:255], recipients_to=email.to, recipients_cc=email.cc, item_counts=item_counts or {},
        provider=profile.provider, sender_profile_name=profile.name, daily_run=daily_run, config_version=config_version,
        hold_reason="", error="", updated_at=now)
    if not claimed:
        entry.refresh_from_db()
        return DeliveryOutcome(entry, "skipped_in_progress", "Another process claimed this email.")

    try:
        result = provider.send(email, idempotency_key=entry.idempotency_key)
    except Exception:
        logger.exception("Provider raised unexpectedly", extra={"delivery_id": entry.pk})
        result = providers.SendResult(providers.UNCERTAIN, error="Unexpected error during send; delivery is uncertain.")

    state = {providers.ACCEPTED: Delivery.STATE_SENT, providers.FAILED: Delivery.STATE_FAILED}.get(result.status, Delivery.STATE_UNCERTAIN)
    Delivery.objects.filter(pk=entry.pk).update(
        state=state, provider_message_id=(result.message_id or "")[:255], error=redact(result.error or "")[:500],
        sent_at=timezone.now() if state == Delivery.STATE_SENT else None, updated_at=timezone.now())
    entry.refresh_from_db()
    logger.info("Delivery attempt", extra={"delivery_id": entry.pk, "key": entry.idempotency_key, "state": state,
                                           "provider": profile.provider, "attempt": entry.attempts})
    return DeliveryOutcome(entry, {Delivery.STATE_SENT: "sent", Delivery.STATE_FAILED: "failed"}.get(state, "uncertain"), entry.error)


def reconcile(entry: Delivery, action, user, note=""):
    """Admin resolution of uncertain/failed deliveries.

    * ``mark_sent``: admin confirmed the email arrived (e.g. in the Sent folder).
    * ``authorize_retry``: admin confirmed it did NOT arrive; next run may resend.
    """
    if action == "mark_sent":
        allowed = [Delivery.STATE_UNCERTAIN, Delivery.STATE_FAILED]
        values = {"state": Delivery.STATE_SENT, "sent_at": entry.sent_at or timezone.now()}
    elif action == "authorize_retry":
        allowed = [Delivery.STATE_UNCERTAIN, Delivery.STATE_FAILED, Delivery.STATE_HELD]
        values = {"state": Delivery.STATE_RETRY_AUTHORIZED, "attempts": 0}
    else:
        raise ValueError("Unknown reconcile action.")
    updated = Delivery.objects.filter(pk=entry.pk, state__in=allowed).update(
        reconciled_by=user, reconcile_note=note[:500], updated_at=timezone.now(), **values)
    entry.refresh_from_db()
    return bool(updated)
