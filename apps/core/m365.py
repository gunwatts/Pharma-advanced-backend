"""Microsoft 365 (Microsoft Graph) access, shared by OneDrive workbooks and the shared-mailbox sender.

Two modes, chosen in Setup:
* ``app``       - IT registers an Entra app with APPLICATION permissions (Files.Read.All or Sites.Selected,
                  Mail.Send) and admin consent. Uses the client-credentials flow.
* ``delegated`` - the same app registration with DELEGATED permissions; the admin signs in once with
                  "Sign in with Microsoft". Only an encrypted refresh token is stored.

App credentials always come from environment variables (GRAPH_TENANT_ID, GRAPH_CLIENT_ID,
GRAPH_CLIENT_SECRET); they are never stored in the database or shown in the UI.
"""

import base64
import hashlib
import logging
import os
import threading
import time
from urllib.parse import quote, urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone

from apps.core.models import M365Connection

logger = logging.getLogger("qa.m365")
HTTP_TIMEOUT = 30
GRAPH = "https://graph.microsoft.com/v1.0"
DELEGATED_SCOPES = "offline_access openid profile email User.Read Files.Read.All Mail.Send Mail.Send.Shared"
ENV_NAMES = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET")

_cache = {"token": None, "expires": 0.0, "mode": None}
_lock = threading.Lock()


class M365Error(Exception):
    """User-safe error message (never contains secrets)."""


def missing_env():
    return [name for name in ENV_NAMES if not os.environ.get(name)]


def configured():
    return not missing_env()


def _cfg():
    if not configured():
        raise M365Error("Microsoft 365 app details are not set on the server (" + ", ".join(missing_env()) + ").")
    return os.environ["GRAPH_TENANT_ID"], os.environ["GRAPH_CLIENT_ID"], os.environ["GRAPH_CLIENT_SECRET"]


def _fernet():
    key = os.environ.get("M365_TOKEN_KEY")
    if not key:  # derive a stable key from the Django secret key
        key = base64.urlsafe_b64encode(hashlib.sha256(("m365:" + settings.SECRET_KEY).encode()).digest()).decode()
    return Fernet(key.encode())


def _token_url():
    tenant, _, _ = _cfg()
    return f"https://login.microsoftonline.com/{quote(tenant)}/oauth2/v2.0/token"


def _post_token(data):
    try:
        resp = requests.post(_token_url(), data=data, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        raise M365Error("Could not reach Microsoft sign-in. Check the server's internet access.")
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200:
        code = body.get("error", "")
        logger.warning("Microsoft token request failed", extra={"status": resp.status_code, "error_code": code})
        if code == "invalid_grant":
            raise M365Error("The Microsoft sign-in has expired or was revoked. Sign in again in Setup.")
        if code in ("invalid_client", "unauthorized_client"):
            raise M365Error("Microsoft rejected the app details (check client ID/secret and that the secret has not expired).")
        raise M365Error("Microsoft sign-in failed. Check the app registration permissions and admin consent.")
    return body


def redirect_uri(request=None):
    configured_uri = os.environ.get("M365_REDIRECT_URI")
    if configured_uri:
        return configured_uri
    if request is None:
        raise M365Error("M365_REDIRECT_URI is not set.")
    return request.build_absolute_uri("/api/m365/callback/")


def authorize_url(state, redirect):
    tenant, client_id, _ = _cfg()
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect, "response_mode": "query",
              "scope": DELEGATED_SCOPES, "state": state, "prompt": "select_account"}
    return f"https://login.microsoftonline.com/{quote(tenant)}/oauth2/v2.0/authorize?{urlencode(params)}"


def complete_sign_in(code, redirect, user=None):
    _, client_id, secret = _cfg()
    body = _post_token({"client_id": client_id, "client_secret": secret, "grant_type": "authorization_code", "code": code,
                        "redirect_uri": redirect, "scope": DELEGATED_SCOPES})
    if not body.get("refresh_token"):
        raise M365Error("Microsoft did not return a refresh token (offline_access permission missing).")
    account = ""
    try:
        me = requests.get(f"{GRAPH}/me?$select=userPrincipalName,mail", headers={"Authorization": f"Bearer {body['access_token']}"},
                          timeout=HTTP_TIMEOUT)
        if me.status_code == 200:
            account = me.json().get("mail") or me.json().get("userPrincipalName") or ""
    except requests.RequestException:
        pass
    conn = M365Connection.load()
    conn.mode = M365Connection.MODE_DELEGATED
    conn.account = account[:255]
    conn.refresh_token_encrypted = _fernet().encrypt(body["refresh_token"].encode()).decode()
    conn.connected_at = timezone.now()
    conn.connected_by = user if getattr(user, "pk", None) else None
    conn.last_error = ""
    conn.save()
    with _lock:
        _cache.update(token=body["access_token"], expires=time.time() + int(body.get("expires_in", 3600)), mode="delegated")
    return conn


def disconnect():
    conn = M365Connection.load()
    conn.refresh_token_encrypted = ""
    conn.account = ""
    conn.connected_at = None
    conn.save()
    clear_cache()


def clear_cache():
    with _lock:
        _cache.update(token=None, expires=0.0, mode=None)


def access_token():
    """A valid Graph access token for the configured mode (cached in memory until shortly before expiry)."""
    conn = M365Connection.load()
    with _lock:
        if _cache["token"] and _cache["mode"] == conn.mode and _cache["expires"] > time.time() + 60:
            return _cache["token"]
    _, client_id, secret = _cfg()
    if conn.mode == M365Connection.MODE_DELEGATED:
        if not conn.refresh_token_encrypted:
            raise M365Error("Nobody has signed in with Microsoft yet. Use 'Sign in with Microsoft' in Setup.")
        try:
            refresh = _fernet().decrypt(conn.refresh_token_encrypted.encode()).decode()
        except InvalidToken:
            raise M365Error("The stored Microsoft sign-in cannot be read (server key changed). Sign in again.")
        try:
            body = _post_token({"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token",
                                "refresh_token": refresh, "scope": DELEGATED_SCOPES})
        except M365Error as exc:
            M365Connection.objects.filter(pk=1).update(last_error=str(exc)[:500])
            raise
        if body.get("refresh_token") and body["refresh_token"] != refresh:  # rotate
            M365Connection.objects.filter(pk=1).update(refresh_token_encrypted=_fernet().encrypt(body["refresh_token"].encode()).decode(),
                                                       last_error="")
    else:
        body = _post_token({"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials",
                            "scope": "https://graph.microsoft.com/.default"})
    with _lock:
        _cache.update(token=body["access_token"], expires=time.time() + int(body.get("expires_in", 3600)), mode=conn.mode)
    return body["access_token"]


def status():
    conn = M365Connection.load()
    return {
        "mode": conn.mode,
        "configured": configured(),
        "missing_env": missing_env(),
        "signed_in": bool(conn.refresh_token_encrypted),
        "account": conn.account,
        "connected_at": conn.connected_at,
        "last_error": conn.last_error,
    }


def share_id(url):
    """Graph 'shares' id for a OneDrive/SharePoint sharing link or file URL."""
    encoded = base64.urlsafe_b64encode(url.strip().encode()).decode().rstrip("=")
    return "u!" + encoded
