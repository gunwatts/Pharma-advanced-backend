import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models

KEY_VALIDATOR_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
RESERVED_FIELD_KEYS = {"source_id", "unit", "department", "status", "category", "parent"}


def validate_key(value):
    if not KEY_VALIDATOR_RE.match(value or ""):
        raise ValidationError("Use lowercase letters, digits and underscores, starting with a letter.")


def validate_email_list(values):
    if not isinstance(values, list):
        raise ValidationError("Must be a list of email addresses.")
    seen = set()
    for value in values:
        try:
            validate_email(value)
        except ValidationError:
            raise ValidationError(f"Invalid email address: {value}")
        if value.lower() in seen:
            raise ValidationError(f"Duplicate email address: {value}")
        seen.add(value.lower())


def normalize_label(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


class Unit(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    aliases = models.JSONField(default=list, blank=True, help_text="Alternative spellings found in Excel files.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def labels(self):
        return {normalize_label(v) for v in [self.code, self.name, *self.aliases] if v}


class Department(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    aliases = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def labels(self):
        return {normalize_label(v) for v in [self.code, self.name, *self.aliases] if v}


class RecipientGroup(models.Model):
    """To/CC recipients for one unit-department combination."""

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="recipient_groups")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="recipient_groups")
    to_emails = models.JSONField(default=list, validators=[validate_email_list])
    cc_emails = models.JSONField(default=list, blank=True, validators=[validate_email_list])
    sender_profile = models.ForeignKey(
        "reminders.SenderProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="recipient_groups",
        help_text="Optional override; the default sender profile is used when empty.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["unit__name", "department__name"]
        constraints = [models.UniqueConstraint(fields=["unit", "department"], name="uniq_recipient_group")]

    def __str__(self):
        return f"{self.unit} / {self.department}"


class Category(models.Model):
    KIND_QMS = "qms"
    KIND_AUDIT = "audit"
    KIND_ACTION = "action_item"
    KIND_CHOICES = [(KIND_QMS, "QMS record"), (KIND_AUDIT, "Audit"), (KIND_ACTION, "Action item")]

    key = models.CharField(max_length=63, unique=True, validators=[validate_key])
    name = models.CharField(max_length=128)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_QMS)
    description = models.CharField(max_length=255, blank=True)
    title_field = models.CharField(max_length=63, blank=True, help_text="Field key used as the record title.")
    display_fields = models.JSONField(default=list, blank=True, help_text="Field keys shown in emails and tables.")
    sort_order = models.PositiveIntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class FieldDefinition(models.Model):
    TYPE_TEXT = "text"
    TYPE_DATE = "date"
    TYPE_NUMBER = "number"
    TYPE_CHOICE = "choice"
    TYPE_EMAIL = "email"
    TYPE_CHOICES = [(TYPE_TEXT, "Text"), (TYPE_DATE, "Date"), (TYPE_NUMBER, "Number"), (TYPE_CHOICE, "Choice"), (TYPE_EMAIL, "Email")]

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="fields")
    key = models.CharField(max_length=63, validators=[validate_key])
    label = models.CharField(max_length=128)
    field_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default=TYPE_TEXT)
    required = models.BooleanField(default=False)
    choices = models.JSONField(default=list, blank=True)
    max_length = models.PositiveIntegerField(null=True, blank=True)
    pattern = models.CharField(max_length=200, blank=True, help_text="Optional regular expression the value must match.")
    sort_order = models.PositiveIntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["category", "key"], name="uniq_field_key")]

    def __str__(self):
        return f"{self.category.key}.{self.key}"

    def clean(self):
        if self.key in RESERVED_FIELD_KEYS:
            raise ValidationError({"key": f"'{self.key}' is reserved."})
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error:
                raise ValidationError({"pattern": "Invalid regular expression."})


class StatusDefinition(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="statuses")
    value = models.CharField(max_length=64)
    label = models.CharField(max_length=128, blank=True)
    is_closed = models.BooleanField(default=False, help_text="Closed records are removed after a successful sync.")
    aliases = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["category", "value"]
        constraints = [models.UniqueConstraint(fields=["category", "value"], name="uniq_status_value")]

    def __str__(self):
        return f"{self.category.key}:{self.value}"

    def labels(self):
        return {normalize_label(v) for v in [self.value, self.label, *self.aliases] if v}


class Relationship(models.Model):
    """Explicit parent-child link: child.<link_field> holds the parent's source ID."""

    parent_category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="child_relationships")
    child_category = models.OneToOneField(Category, on_delete=models.PROTECT, related_name="parent_relationship")
    link_field = models.CharField(max_length=63, validators=[validate_key])
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.parent_category.key} -> {self.child_category.key} via {self.link_field}"

    def clean(self):
        if self.parent_category_id and self.parent_category_id == self.child_category_id:
            raise ValidationError("A category cannot be its own parent.")
