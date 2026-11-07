"""Safe placeholder templating for email subjects and text blocks.

Only ``{{ name }}`` placeholders from a fixed allow-list are supported. There
is no logic, no attribute access and no code execution. All values are
HTML-escaped when rendered into HTML.
"""

import re

from django.utils.html import escape

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
BRACES_RE = re.compile(r"\{\{|\}\}")

PLACEHOLDERS = {
    "unit_name": "Plant name",
    "unit_code": "Plant code",
    "department_name": "Department name",
    "department_code": "Department code",
    "run_date": "Reminder date (DD-Mon-YYYY)",
    "window_days": "Reminder window in days",
    "total_count": "Total items in the email",
    "action_required_count": "Items in the 'Action required' section",
    "periodic_count": "Items in the 'Periodic reminders' section",
    "qms_count": "Number of QMS events",
    "action_count": "Number of action plans",
    "due_today_count": "Number of items due today",
    "app_url": "Link to the application",
}


class TemplateError(ValueError):
    pass


def find_placeholders(text):
    return PLACEHOLDER_RE.findall(text or "")


def validate_template_text(text):
    """Return a list of human-readable problems (unknown placeholders, stray braces)."""
    problems = []
    unknown = sorted({name for name in find_placeholders(text) if name not in PLACEHOLDERS})
    if unknown:
        problems.append("Unknown placeholder(s): " + ", ".join(f"{{{{ {u} }}}}" for u in unknown))
    leftover = BRACES_RE.findall(PLACEHOLDER_RE.sub("", text or ""))
    if leftover:
        problems.append("Unmatched '{{' or '}}' found.")
    return problems


def render_text(text, context, missing="-"):
    """Render plain text. Raises TemplateError for unknown placeholders."""
    problems = validate_template_text(text)
    if problems:
        raise TemplateError("; ".join(problems))

    def sub(match):
        value = context.get(match.group(1))
        return missing if value is None or value == "" else str(value)

    return PLACEHOLDER_RE.sub(sub, text or "")


def render_html_block(text, context, missing="-"):
    """Render a text block into safe HTML paragraphs."""
    problems = validate_template_text(text)
    if problems:
        raise TemplateError("; ".join(problems))
    rendered = render_text(text, context, missing)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", rendered) if p.strip()]
    return "".join(f"<p style=\"margin:0 0 12px 0\">{escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)
