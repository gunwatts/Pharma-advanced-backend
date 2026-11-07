from django.db import models


class Record(models.Model):
    """Minimal current (open) business record synced from a source worksheet.

    Only mapped fields are stored. Closed/completed records are removed after a
    successful validated sync; historical rows are never retained.
    """

    category = models.ForeignKey("catalog.Category", on_delete=models.PROTECT, related_name="records")
    mapping = models.ForeignKey("sources.SourceMapping", on_delete=models.PROTECT, related_name="records")
    source_id = models.CharField(max_length=128)
    unit = models.ForeignKey("catalog.Unit", on_delete=models.PROTECT, related_name="records")
    department = models.ForeignKey("catalog.Department", on_delete=models.PROTECT, related_name="records")
    status = models.CharField(max_length=64, blank=True, db_index=True)
    title = models.CharField(max_length=500, blank=True)
    data = models.JSONField(default=dict, blank=True)
    issues = models.JSONField(default=list, blank=True, help_text="Data quality issues needing admin review.")
    parent_ref = models.CharField(max_length=128, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField()

    class Meta:
        ordering = ["category__sort_order", "source_id"]
        constraints = [models.UniqueConstraint(fields=["category", "unit", "source_id"], name="uniq_record_plant_source_id")]
        indexes = [
            models.Index(fields=["unit", "department"]),
            models.Index(fields=["category", "unit", "department"]),
        ]

    def __str__(self):
        return f"{self.category.key}:{self.source_id}"

    @property
    def has_issues(self):
        return bool(self.issues)
