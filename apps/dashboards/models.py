from django.conf import settings
from django.db import models


class Dashboard(models.Model):
    name = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True)
    is_published = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Widget(models.Model):
    TYPE_KPI = "kpi"
    TYPE_PIE = "pie"
    TYPE_DONUT = "donut"
    TYPE_BAR = "bar"
    TYPE_LINE = "line"
    TYPE_AREA = "area"
    TYPE_TABLE = "table"
    TYPE_UPCOMING = "upcoming"
    TYPE_OVERDUE = "overdue"
    TYPE_CHOICES = [(t, t.title()) for t in (
        TYPE_KPI, TYPE_PIE, TYPE_DONUT, TYPE_BAR, TYPE_LINE, TYPE_AREA, TYPE_TABLE, TYPE_UPCOMING, TYPE_OVERDUE
    )]

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
    title = models.CharField(max_length=128)
    widget_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    category = models.ForeignKey("catalog.Category", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    filters = models.JSONField(default=list, blank=True, help_text="[{field, op, value}] - no raw SQL.")
    group_by = models.CharField(max_length=63, blank=True)
    date_field = models.CharField(max_length=63, blank=True)
    date_bucket = models.CharField(max_length=8, default="month", choices=[("day", "Day"), ("week", "Week"), ("month", "Month")])
    window_days = models.PositiveSmallIntegerField(default=7)
    columns = models.JSONField(default=list, blank=True)
    sort_by = models.CharField(max_length=63, blank=True)
    sort_desc = models.BooleanField(default=False)
    limit = models.PositiveSmallIntegerField(default=10)
    width = models.PositiveSmallIntegerField(default=1, help_text="Grid columns spanned (1-3).")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["dashboard", "position", "id"]

    def __str__(self):
        return f"{self.dashboard.name}: {self.title}"


class DashboardAssignment(models.Model):
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="assignments")
    role = models.ForeignKey("accounts.Role", null=True, blank=True, on_delete=models.CASCADE, related_name="dashboard_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="dashboard_assignments")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(role__isnull=False, user__isnull=True) | models.Q(role__isnull=True, user__isnull=False)),
                name="assignment_role_xor_user",
            ),
        ]
