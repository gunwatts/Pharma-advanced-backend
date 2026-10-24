"""Audit trail helpers. Never store secrets in audit records."""

from django.forms.models import model_to_dict

from apps.core.middleware import client_ip, current_request
from apps.core.models import AuditEvent

SENSITIVE_KEYS = {"password", "secret", "token", "client_secret", "refresh_token", "key_hash"}


def _clean(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in k.lower() for s in SENSITIVE_KEYS) else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def snapshot(instance):
    if instance is None:
        return None
    data = model_to_dict(instance)
    for key, value in list(data.items()):
        if hasattr(value, "__iter__") and not isinstance(value, (str, dict, list)):
            data[key] = [getattr(v, "pk", v) for v in value]
        elif hasattr(value, "pk"):
            data[key] = value.pk
    return _clean(data)


def diff(before, after):
    if before is None or after is None:
        return {"before": before, "after": after}
    changed = {k: {"from": before.get(k), "to": after.get(k)} for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return {"changed": changed}


def record(action, actor=None, obj=None, changes=None, object_type=None, object_id=None, object_repr=None):
    request = current_request()
    if actor is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        actor = request.user
    return AuditEvent.objects.create(
        actor=actor if actor is not None and getattr(actor, "pk", None) else None,
        actor_username=getattr(actor, "username", "") or "system",
        action=action,
        object_type=object_type or (obj._meta.label_lower if obj is not None else ""),
        object_id=str(object_id if object_id is not None else getattr(obj, "pk", "") or ""),
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:255],
        changes=_clean(changes or {}),
        ip_address=client_ip(request),
    )
