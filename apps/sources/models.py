from django.db import models


class DataSource(models.Model):
    PROVIDER_UPLOAD = "upload"
    PROVIDER_SERVER_FOLDER = "server_folder"
    PROVIDER_GRAPH = "graph_drive"
    PROVIDER_CHOICES = [
        (PROVIDER_UPLOAD, "Uploaded workbook (manual refresh)"),
        (PROVIDER_SERVER_FOLDER, "Server / network folder"),
        (PROVIDER_GRAPH, "OneDrive for Business / SharePoint (Microsoft Graph)"),
    ]

    name = models.CharField(max_length=128, unique=True)
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    config = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    uploaded_file_name = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_check_ok = models.BooleanField(null=True)
    last_check_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SourceMapping(models.Model):
    """Maps one worksheet of a data source to one record category."""

    data_source = models.ForeignKey(DataSource, on_delete=models.PROTECT, related_name="mappings")
    category = models.ForeignKey("catalog.Category", on_delete=models.PROTECT, related_name="mappings")
    name = models.CharField(max_length=128, blank=True)
    worksheet = models.CharField(max_length=128)
    header_row = models.PositiveIntegerField(default=1)
    id_column = models.CharField(max_length=128)
    unit_column = models.CharField(max_length=128, blank=True)
    fixed_unit = models.ForeignKey(
        "catalog.Unit", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Set when the worksheet only contains one unit's records. A failed sync then affects only that unit.",
    )
    department_column = models.CharField(max_length=128)
    status_column = models.CharField(max_length=128, blank=True)
    column_map = models.JSONField(default=dict, blank=True, help_text="{field_key: column header}")
    date_formats = models.JSONField(default=list, blank=True, help_text="strftime formats, tried in order.")
    max_rejected_rows = models.PositiveIntegerField(
        default=0, help_text="Sync is marked incomplete (and nothing is changed) if more rows than this are rejected."
    )
    active = models.BooleanField(default=True)

    last_validated_at = models.DateTimeField(null=True, blank=True)
    last_validation_ok = models.BooleanField(null=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=16, blank=True)
    last_rows_read = models.PositiveIntegerField(default=0)
    last_rows_imported = models.PositiveIntegerField(default=0)
    last_rows_rejected = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["category__sort_order", "id"]

    def __str__(self):
        return self.name or f"{self.data_source.name} / {self.worksheet} -> {self.category.name}"


class SyncRun(models.Model):
    STATUS_SUCCESS = "success"
    STATUS_INCOMPLETE = "incomplete"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(STATUS_SUCCESS, "Success"), (STATUS_INCOMPLETE, "Incomplete"), (STATUS_FAILED, "Failed")]

    mapping = models.ForeignKey(SourceMapping, on_delete=models.CASCADE, related_name="sync_runs")
    daily_run = models.ForeignKey("reminders.DailyRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="sync_runs")
    triggered_by = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    rows_read = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_rejected = models.PositiveIntegerField(default=0)
    rows_flagged = models.PositiveIntegerField(default=0)
    rows_closed = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    records_removed_closed = models.PositiveIntegerField(default=0)
    records_removed_missing = models.PositiveIntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    @property
    def trusted(self):
        return self.status == self.STATUS_SUCCESS
