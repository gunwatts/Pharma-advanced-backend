"""Setup endpoints for the Microsoft 365 connection (status, mode, sign in, callback, disconnect)."""

import secrets
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.accounts.permissions import HasPermission
from apps.core import audit, m365
from apps.core.models import M365Connection

STATE_TTL = 600


def _frontend(path):
    base = settings.FRONTEND_URL or "/"
    return f"{base.rstrip('/')}/#{path}"


@api_view(["GET"])
@permission_classes([HasPermission.of("admin.configure")])
def m365_status(request):
    data = m365.status()
    try:
        data["redirect_uri"] = m365.redirect_uri(request)
    except m365.M365Error:
        data["redirect_uri"] = ""
    return Response(data)


@api_view(["POST"])
@permission_classes([HasPermission.of("admin.configure")])
def m365_mode(request):
    mode = request.data.get("mode")
    if mode not in (M365Connection.MODE_APP, M365Connection.MODE_DELEGATED):
        raise ValidationError({"mode": "Choose 'app' or 'delegated'."})
    conn = M365Connection.load()
    conn.mode = mode
    conn.save()
    m365.clear_cache()
    audit.record("m365_mode", obj=conn, changes={"mode": mode})
    return Response(m365.status())


@api_view(["POST"])
@permission_classes([HasPermission.of("admin.configure")])
def m365_login(request):
    try:
        redirect = m365.redirect_uri(request)
        state = secrets.token_urlsafe(24)
        cache.set(f"m365-state:{state}", {"user": request.user.pk, "redirect": redirect}, STATE_TTL)
        return Response({"url": m365.authorize_url(state, redirect)})
    except m365.M365Error as exc:
        raise ValidationError({"detail": str(exc)})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def m365_callback(request):
    """Microsoft redirects the admin's browser here after sign-in. Protected by a one-time state value."""
    state = request.query_params.get("state", "")
    pending = cache.get(f"m365-state:{state}") if state else None
    if not pending:
        return HttpResponseRedirect(_frontend("/setup?m365=error&reason=" + quote("Sign-in link expired. Try again.")))
    cache.delete(f"m365-state:{state}")
    if request.query_params.get("error"):
        reason = request.query_params.get("error_description", "Sign-in was cancelled or not allowed.")[:200]
        return HttpResponseRedirect(_frontend("/setup?m365=error&reason=" + quote(reason)))
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=pending["user"]).first()
    try:
        conn = m365.complete_sign_in(request.query_params.get("code", ""), pending["redirect"], user)
    except m365.M365Error as exc:
        return HttpResponseRedirect(_frontend("/setup?m365=error&reason=" + quote(str(exc))))
    audit.record("m365_sign_in", actor=user, obj=conn, changes={"account": conn.account})
    return HttpResponseRedirect(_frontend("/setup?m365=connected"))


@api_view(["POST"])
@permission_classes([HasPermission.of("admin.configure")])
def m365_disconnect(request):
    m365.disconnect()
    audit.record("m365_disconnect", obj=M365Connection.load())
    return Response(m365.status())


@api_view(["POST"])
@permission_classes([HasPermission.of("admin.configure")])
def m365_test(request):
    try:
        m365.clear_cache()
        m365.access_token()
    except m365.M365Error as exc:
        return Response({"ok": False, "message": str(exc)})
    return Response({"ok": True, "message": "Connected to Microsoft 365."})
