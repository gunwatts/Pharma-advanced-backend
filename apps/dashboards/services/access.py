from django.db.models import Q

from apps.accounts.permissions import has_perm
from apps.dashboards.models import Dashboard


def accessible_dashboards(user):
    """Dashboards the user may open. Managers see all; others only published + assigned."""
    if has_perm(user, "dashboards.manage"):
        return Dashboard.objects.all()
    if not has_perm(user, "dashboards.view"):
        return Dashboard.objects.none()
    role_id = getattr(getattr(user, "profile", None), "role_id", None)
    q = Q(assignments__user=user)
    if role_id:
        q |= Q(assignments__role_id=role_id)
    return Dashboard.objects.filter(is_published=True).filter(q).distinct()


def default_dashboard_id(user):
    allowed = accessible_dashboards(user)
    profile = getattr(user, "profile", None)
    for candidate in (getattr(profile, "default_dashboard_id", None), getattr(getattr(profile, "role", None), "default_dashboard_id", None)):
        if candidate and allowed.filter(pk=candidate).exists():
            return candidate
    first = allowed.filter(is_published=True).order_by("name").first() or allowed.order_by("name").first()
    return first.pk if first else None
