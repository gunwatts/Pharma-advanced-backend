from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.permissions import HasPermission, has_perm
from apps.catalog.models import RecipientGroup
from apps.core import audit
from apps.core.timeutils import business_today
from apps.core.viewsets import AuditedModelViewSet
from apps.reminders.models import DailyRun, Delivery, EmailTemplate, JobSchedule, ReminderRule, SenderProfile
from apps.reminders.serializers import (
    DailyRunSerializer,
    DeliverySerializer,
    EmailTemplateSerializer,
    JobScheduleSerializer,
    ReminderRuleSerializer,
    SenderProfileSerializer,
)
from apps.reminders.services import delivery as delivery_service
from apps.reminders.services import providers, workflow
from apps.reminders.services.rules import OPERATORS
from apps.reminders.services.templating import PLACEHOLDERS, TemplateError


def _require(request, perm):
    if not has_perm(request.user, perm):
        raise PermissionDenied()


class SenderProfileViewSet(AuditedModelViewSet):
    queryset = SenderProfile.objects.all()
    serializer_class = SenderProfileSerializer

    def get_throttles(self):
        if self.action in ("test_connection", "test_email"):
            self.throttle_scope = "sensitive"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def impacts(self, instance):
        return [f"Recipient group '{g}' uses this sender." for g in instance.recipient_groups.all()] + (
            ["This is the default sender for all reminders."] if instance.is_default else [])

    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection(self, request, pk=None):
        profile = self.get_object()
        result = providers.get_provider(profile).test_connection()
        profile.last_test_at, profile.last_test_status, profile.last_test_message = timezone.now(), result.status, result.message[:500]
        profile.save(update_fields=["last_test_at", "last_test_status", "last_test_message"])
        audit.record("sender_test_connection", obj=profile, changes={"status": result.status})
        return Response({"ok": result.ok, "status": result.status, "message": result.message})

    @action(detail=True, methods=["post"], url_path="test-email")
    def test_email(self, request, pk=None):
        _require(request, "runs.manage")
        profile = self.get_object()
        recipient = str(request.data.get("recipient", "")).strip()
        try:
            validate_email(recipient)
        except DjangoValidationError:
            raise ValidationError({"recipient": "Enter a valid test email address."})
        try:
            outcome, delivers = workflow.send_test_email(recipient, group_id=request.data.get("group_id") or None, profile=profile,
                                                         triggered_by=request.user.username)
        except (workflow.WorkflowError, TemplateError) as exc:
            raise ValidationError({"detail": str(exc)})
        audit.record("test_email", obj=profile, changes={"recipient": recipient, "result": outcome.action})
        entry = outcome.delivery
        if outcome.action == "sent":
            message = "Accepted by the email provider." if delivers else "Logged only: the console provider does NOT deliver email."
        elif outcome.action == "uncertain":
            message = "Delivery is uncertain (no confirmation from provider). Check the test mailbox before retrying."
        else:
            message = entry.error or "Sending failed."
        return Response({"result": outcome.action, "delivered_by_provider": delivers and outcome.action == "sent", "message": message,
                         "delivery": DeliverySerializer(entry).data})


class ReminderRuleViewSet(AuditedModelViewSet):
    queryset = ReminderRule.objects.select_related("category").prefetch_related("units", "departments")
    serializer_class = ReminderRuleSerializer

    @action(detail=False, methods=["get"])
    def operators(self, request):
        return Response([{"key": k, "label": v} for k, v in OPERATORS.items()])


class EmailTemplateViewSet(AuditedModelViewSet):
    queryset = EmailTemplate.objects.all()
    serializer_class = EmailTemplateSerializer

    def impacts(self, instance):
        return ["This is the active template used for daily reminders."] if instance.is_active else []

    def update_impacts(self, instance, data):
        return ["This template is active; changes apply to the next daily run."] if instance.is_active else []

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        template = self.get_object()
        with transaction.atomic():
            EmailTemplate.objects.exclude(pk=template.pk).update(is_active=False)
            EmailTemplate.objects.filter(pk=template.pk).update(is_active=True)
        audit.record("template_activate", obj=template)
        self._changed()
        return Response({"detail": f"'{template.name}' is now the active template."})

    @action(detail=False, methods=["get"])
    def placeholders(self, request):
        return Response([{"key": k, "label": v} for k, v in PLACEHOLDERS.items()])

    @action(detail=False, methods=["post"])
    def preview(self, request):
        """Render a (possibly unsaved) template with sample data or a real group's current data."""
        serializer = EmailTemplateSerializer(data={**request.data, "name": request.data.get("name") or "preview"})
        serializer.is_valid(raise_exception=True)
        template = EmailTemplate(**serializer.validated_data)
        group_id = request.data.get("group_id")
        try:
            if group_id:
                group = RecipientGroup.objects.select_related("unit", "department").get(pk=group_id)
                plan = workflow.build_plan(sync=False, template=template)  # renders this draft, not the saved template
                gp = next((g for g in plan.groups if g.group.pk == group.pk), None)
                if gp is None or gp.email is None:
                    return Response({"subject": None, "html": None, "to": group.to_emails, "cc": group.cc_emails,
                                     "note": f"No email would be sent for this group today ({gp.action if gp else 'inactive'}).",
                                     "hold_reasons": gp.hold_reasons if gp else []})
                return Response({"subject": gp.email.subject, "html": gp.email.html, "text": gp.email.text, "to": gp.email.to,
                                 "cc": gp.email.cc, "action": gp.action, "items": len(gp.items)})
            email = workflow.sample_email(template)
            no_pending = workflow.sample_no_pending(template)
            return Response({"subject": email.subject, "html": email.html, "text": email.text, "to": email.to, "cc": email.cc,
                             "no_pending": {"subject": no_pending.subject, "html": no_pending.html}, "sample": True})
        except RecipientGroup.DoesNotExist:
            raise ValidationError({"group_id": "Recipient group not found."})
        except (TemplateError, workflow.WorkflowError) as exc:
            raise ValidationError({"detail": str(exc)})


class JobScheduleViewSet(AuditedModelViewSet):
    queryset = JobSchedule.objects.all()
    serializer_class = JobScheduleSerializer
    http_method_names = ["get", "patch", "put", "options", "head"]  # schedules are seeded; no create/delete


class DailyRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = DailyRunSerializer
    permission_classes = [HasPermission.of("runs.view")]

    def get_queryset(self):
        qs = DailyRun.objects.all()
        if self.request.query_params.get("kind"):
            qs = qs.filter(kind=self.request.query_params["kind"])
        return qs

    @action(detail=False, methods=["post"], url_path="dry-run")
    def dry_run(self, request):
        _require(request, "runs.manage")
        try:
            preview = workflow.dry_run(sync=bool(request.data.get("sync")), triggered_by=request.user.username)
        except (workflow.WorkflowError, TemplateError) as exc:
            raise ValidationError({"detail": str(exc)})
        audit.record("dry_run", object_type="reminders.dailyrun", object_id=preview.get("daily_run_id"), changes={"totals": preview["totals"]})
        return Response(preview)

    @action(detail=False, methods=["post"], url_path="run-now")
    def run_now(self, request):
        """Manual production run. Requires activation; idempotent per unit-department per day."""
        _require(request, "runs.manage")
        if request.data.get("confirm") is not True:
            raise ValidationError({"confirm": "Confirm that production recipients will be emailed."})
        kind = DailyRun.KIND_RETRY if request.data.get("retry") else DailyRun.KIND_MANUAL
        run = workflow.run_daily(kind=kind, triggered_by=request.user.username, sync=request.data.get("sync", True) is not False)
        audit.record("manual_run" if kind == DailyRun.KIND_MANUAL else "manual_retry", obj=run, changes={"status": run.status})
        return Response(DailyRunSerializer(run).data, status=status.HTTP_201_CREATED)


class DeliveryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = DeliverySerializer
    permission_classes = [HasPermission.of("runs.view")]

    def get_queryset(self):
        qs = Delivery.objects.select_related("reconciled_by")
        params = self.request.query_params
        for param in ("state", "kind", "run_date", "daily_run", "unit", "department"):
            if params.get(param):
                qs = qs.filter(**{param: params[param]})
        return qs

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        _require(request, "runs.manage")
        entry = self.get_object()
        action_name = request.data.get("action")
        if action_name not in ("mark_sent", "authorize_retry"):
            raise ValidationError({"action": "Choose mark_sent or authorize_retry."})
        note = str(request.data.get("note", "")).strip()
        if not note:
            raise ValidationError({"note": "Explain how delivery was verified."})
        if not delivery_service.reconcile(entry, action_name, request.user, note):
            raise ValidationError({"detail": f"A delivery in state '{entry.state}' cannot be changed this way."})
        audit.record("delivery_reconcile", obj=entry, changes={"action": action_name, "note": note})
        return Response(DeliverySerializer(entry).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Retry only this unit-department for today. Already-sent groups are never resent."""
        _require(request, "runs.manage")
        entry = self.get_object()
        if entry.kind not in (Delivery.KIND_REMINDER, Delivery.KIND_NO_PENDING) or entry.run_date != business_today():
            raise ValidationError({"detail": "Only today's reminder deliveries can be retried."})
        if entry.state in (Delivery.STATE_SENT, Delivery.STATE_UNCERTAIN, Delivery.STATE_IN_PROGRESS):
            raise ValidationError({"detail": f"Cannot retry a delivery that is '{entry.state}'. Reconcile uncertain deliveries first."})
        group = RecipientGroup.objects.filter(unit_id=entry.unit_id, department_id=entry.department_id).first()
        if group is None:
            raise ValidationError({"detail": "The recipient group no longer exists."})
        run = workflow.run_daily(kind=DailyRun.KIND_RETRY, triggered_by=request.user.username,
                                 sync=bool(request.data.get("sync", True)), only_group_ids={group.pk})
        audit.record("manual_retry", obj=entry, changes={"daily_run": run.pk, "status": run.status})
        entry.refresh_from_db()
        return Response({"run": DailyRunSerializer(run).data, "delivery": DeliverySerializer(entry).data})
