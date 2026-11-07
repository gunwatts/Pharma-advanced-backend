from django.conf import settings
from django.db import models


class SenderProfile(models.Model):
    PROVIDER_SMTP = "smtp"
    PROVIDER_GRAPH = "microsoft_graph"
    PROVIDER_GMAIL = "gmail_api"
    PROVIDER_CONSOLE = "console"
    PROVIDER_CHOICES = [
        (PROVIDER_GRAPH, "Microsoft 365 (Microsoft Graph sendMail)"),
        (PROVIDER_GMAIL, "Google Gmail API"),
        (PROVIDER_SMTP, "SMTP (only where the provider/tenant permits it)"),
        (PROVIDER_CONSOLE, "Log only (testing - nothing is delivered)"),
    ]

    name = models.CharField(max_length=128, unique=True)
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    from_address = models.EmailField()
    from_name = models.CharField(max_length=128, blank=True)
    reply_to = models.EmailField(blank=True)
    settings = models.JSONField(default=dict, blank=True, help_text="Non-secret provider settings (e.g. SMTP host/port).")
    secret_env_prefix = models.CharField(
        max_length=64, help_text="Credentials are read from server environment variables starting with this prefix."
    )
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(max_length=32, blank=True)
    last_test_message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ReminderRule(models.Model):
    name = models.CharField(max_length=128)
    category = models.ForeignKey("catalog.Category", on_delete=models.PROTECT, related_name="reminder_rules")
    ANCHOR_DUE = "due_date"
    ANCHOR_FIRST_SEEN = "first_seen"
    ANCHOR_CHOICES = [(ANCHOR_DUE, "Counted back from the target date"), (ANCHOR_FIRST_SEEN, "Counted from when the item first appeared")]

    target_date_field = models.CharField(max_length=63)
    window_days = models.PositiveSmallIntegerField(default=7, help_text="'Action required' daily when due within this many days.")
    repeat_every_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="'Periodic reminder' every N days before the daily window. Empty = no periodic reminders.")
    repeat_anchor = models.CharField(max_length=16, choices=ANCHOR_CHOICES, default=ANCHOR_DUE)
    start_days_before = models.PositiveIntegerField(
        null=True, blank=True, help_text="Ignore items due further away than this. Empty = no limit.")
    include_statuses = models.JSONField(default=list, blank=True, help_text="Empty = every open status.")
    exclude_statuses = models.JSONField(default=list, blank=True)
    conditions = models.JSONField(default=list, blank=True, help_text="Exclusion conditions: [{field, op, value}].")
    units = models.ManyToManyField("catalog.Unit", blank=True, related_name="+")
    departments = models.ManyToManyField("catalog.Department", blank=True, related_name="+")
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__sort_order", "name"]

    def __str__(self):
        return self.name


class EmailTemplate(models.Model):
    name = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=False, help_text="The active template is used for daily reminders.")
    subject = models.CharField(max_length=255)
    intro_text = models.TextField(blank=True)
    outro_text = models.TextField(blank=True)
    no_pending_subject = models.CharField(max_length=255)
    no_pending_text = models.TextField(blank=True)
    show_parent_context = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class JobSchedule(models.Model):
    JOB_DAILY = "daily_reminders"
    JOB_SYNC = "sync_sources"
    JOB_CHOICES = [(JOB_DAILY, "Daily reminder run (sync + email)"), (JOB_SYNC, "Sync sources only")]

    job = models.CharField(max_length=32, choices=JOB_CHOICES, unique=True)
    enabled = models.BooleanField(default=True)
    run_time = models.TimeField(help_text="Local time in the business timezone.")
    days_of_week = models.JSONField(default=list, blank=True, help_text="0=Monday..6=Sunday. Empty = every day.")
    last_run_date = models.DateField(null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=32, blank=True)

    def __str__(self):
        return f"{self.job} @ {self.run_time}"


class DailyRun(models.Model):
    KIND_SCHEDULED = "scheduled"
    KIND_MANUAL = "manual"
    KIND_RETRY = "retry"
    KIND_DRY_RUN = "dry_run"
    KIND_SYNC = "sync"
    KIND_CHOICES = [(k, k.replace("_", " ").title()) for k in (KIND_SCHEDULED, KIND_MANUAL, KIND_RETRY, KIND_DRY_RUN, KIND_SYNC)]

    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_ATTENTION = "completed_with_issues"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [(s, s.replace("_", " ").title()) for s in (STATUS_RUNNING, STATUS_COMPLETED, STATUS_ATTENTION, STATUS_FAILED, STATUS_SKIPPED)]

    run_date = models.DateField(db_index=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    triggered_by = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    config_version = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"{self.kind} {self.run_date} ({self.status})"


class Delivery(models.Model):
    """Persistent delivery ledger. Never deleted by business-record cleanup."""

    KIND_REMINDER = "reminder"
    KIND_NO_PENDING = "no_pending"
    KIND_TEST = "test"
    KIND_ALERT = "alert"
    KIND_CHOICES = [(k, k.replace("_", " ").title()) for k in (KIND_REMINDER, KIND_NO_PENDING, KIND_TEST, KIND_ALERT)]

    STATE_PENDING = "pending"
    STATE_IN_PROGRESS = "in_progress"
    STATE_SENT = "sent"
    STATE_FAILED = "failed"
    STATE_UNCERTAIN = "uncertain"
    STATE_HELD = "held"
    STATE_RETRY_AUTHORIZED = "retry_authorized"
    STATE_CHOICES = [(s, s.replace("_", " ").title()) for s in (
        STATE_PENDING, STATE_IN_PROGRESS, STATE_SENT, STATE_FAILED, STATE_UNCERTAIN, STATE_HELD, STATE_RETRY_AUTHORIZED
    )]

    idempotency_key = models.CharField(max_length=128, unique=True)
    run_date = models.DateField(db_index=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    state = models.CharField(max_length=24, choices=STATE_CHOICES, default=STATE_PENDING, db_index=True)
    unit = models.ForeignKey("catalog.Unit", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    department = models.ForeignKey("catalog.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    unit_name = models.CharField(max_length=128, blank=True)
    department_name = models.CharField(max_length=128, blank=True)
    daily_run = models.ForeignKey(DailyRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="deliveries")
    config_version = models.PositiveIntegerField(default=0)
    subject = models.CharField(max_length=255, blank=True)
    recipients_to = models.JSONField(default=list, blank=True)
    recipients_cc = models.JSONField(default=list, blank=True)
    item_counts = models.JSONField(default=dict, blank=True)
    provider = models.CharField(max_length=32, blank=True)
    sender_profile_name = models.CharField(max_length=128, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    hold_reason = models.CharField(max_length=500, blank=True)
    error = models.CharField(max_length=500, blank=True)
    reconciled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reconcile_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-run_date", "unit_name", "department_name"]
        verbose_name_plural = "deliveries"

    def __str__(self):
        return f"{self.idempotency_key} [{self.state}]"
