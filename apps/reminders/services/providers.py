"""Email provider adapters.

Credentials are only ever read from server environment variables using the
sender profile's ``secret_env_prefix``; they are never stored in the database
or sent to the browser. Results distinguish accepted / failed / uncertain.
"""

import base64
import logging
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import quote

import requests

from apps.core import m365

logger = logging.getLogger("qa.email")
HTTP_TIMEOUT = 30

ACCEPTED = "accepted"
FAILED = "failed"
UNCERTAIN = "uncertain"


@dataclass
class SendResult:
    status: str
    message_id: str = ""
    error: str = ""


@dataclass
class ConnectionResult:
    ok: bool
    status: str  # connected | credentials_missing | failed
    message: str


class ProviderError(Exception):
    pass


def _env(profile, suffix):
    return os.environ.get(f"{profile.secret_env_prefix}_{suffix}".upper(), "")


class BaseProvider:
    required_secrets = ()
    delivers = True

    def __init__(self, profile):
        self.profile = profile

    def missing_secrets(self):
        return [f"{self.profile.secret_env_prefix}_{s}".upper() for s in self.required_secrets if not _env(self.profile, s)]

    def secret_status(self):
        return {f"{self.profile.secret_env_prefix}_{s}".upper(): bool(_env(self.profile, s)) for s in self.required_secrets}

    def test_connection(self) -> ConnectionResult:
        missing = self.missing_secrets()
        if missing:
            return ConnectionResult(False, "credentials_missing", "Missing server environment variables: " + ", ".join(missing))
        try:
            return self._test()
        except ProviderError as exc:
            return ConnectionResult(False, "failed", str(exc))

    def send(self, email, idempotency_key="") -> SendResult:
        missing = self.missing_secrets()
        if missing:
            return SendResult(FAILED, error="Missing server environment variables: " + ", ".join(missing))
        return self._send(email, idempotency_key)

    def _test(self):  # pragma: no cover - abstract
        raise NotImplementedError

    def _send(self, email, idempotency_key):  # pragma: no cover - abstract
        raise NotImplementedError


class ConsoleProvider(BaseProvider):
    """Testing only: logs metadata (not content). Nothing is delivered."""

    delivers = False

    def _test(self):
        return ConnectionResult(True, "connected", "Log-only provider: emails are recorded in the server log and NOT delivered.")

    def _send(self, email, idempotency_key):
        message_id = f"console-{uuid.uuid4().hex[:12]}"
        logger.info("Console provider - email NOT delivered", extra={
            "idempotency_key": idempotency_key, "subject_len": len(email.subject),
            "to_count": len(email.to), "cc_count": len(email.cc), "message_id": message_id})
        return SendResult(ACCEPTED, message_id=message_id)


class SmtpProvider(BaseProvider):
    required_secrets = ("SMTP_PASSWORD",)

    def _settings(self):
        s = self.profile.settings or {}
        return s.get("host", ""), int(s.get("port", 587)), s.get("security", "starttls"), s.get("username") or self.profile.from_address

    def _connect(self):
        host, port, security, username = self._settings()
        if not host:
            raise ProviderError("SMTP host is not configured.")
        context = ssl.create_default_context()
        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=HTTP_TIMEOUT, context=context)
            else:
                server = smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT)
                server.ehlo()
                if security == "starttls":
                    server.starttls(context=context)
                    server.ehlo()
            server.login(username, _env(self.profile, "SMTP_PASSWORD"))
            return server
        except smtplib.SMTPAuthenticationError:
            raise ProviderError("SMTP authentication failed. The tenant may have SMTP AUTH disabled, or an app password is required.")
        except TimeoutError:
            raise ProviderError("Timed out connecting to the SMTP server.")
        except (OSError, smtplib.SMTPException) as exc:
            raise ProviderError(f"Could not connect to the SMTP server ({exc.__class__.__name__}).")

    def _test(self):
        server = self._connect()
        try:
            server.noop()
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return ConnectionResult(True, "connected", "Connected and authenticated to the SMTP server.")

    def _send(self, email, idempotency_key):
        msg = EmailMessage()
        msg["Subject"] = email.subject
        msg["From"] = formataddr((self.profile.from_name, self.profile.from_address)) if self.profile.from_name else self.profile.from_address
        msg["To"] = ", ".join(email.to)
        if email.cc:
            msg["Cc"] = ", ".join(email.cc)
        if self.profile.reply_to:
            msg["Reply-To"] = self.profile.reply_to
        message_id = make_msgid(domain=self.profile.from_address.split("@")[-1])
        msg["Message-ID"] = message_id
        if idempotency_key:
            msg["X-QA-Idempotency-Key"] = idempotency_key
        msg.set_content(email.text or " ")
        msg.add_alternative(email.html, subtype="html")
        try:
            server = self._connect()
        except ProviderError as exc:
            return SendResult(FAILED, error=str(exc))
        try:
            refused = server.send_message(msg)
        except smtplib.SMTPResponseException as exc:
            # A definite SMTP reply means the server did not accept the message.
            return SendResult(FAILED, error=f"SMTP server rejected the message (code {exc.smtp_code}).")
        except smtplib.SMTPRecipientsRefused:
            return SendResult(FAILED, error="All recipients were refused by the SMTP server.")
        except (TimeoutError, OSError, smtplib.SMTPServerDisconnected):
            # The server may or may not have accepted the message.
            return SendResult(UNCERTAIN, message_id=message_id, error="Connection lost while sending; delivery is uncertain.")
        finally:
            try:
                server.quit()
            except Exception:
                pass
        if refused:
            return SendResult(ACCEPTED, message_id=message_id, error=f"{len(refused)} recipient(s) refused.")
        return SendResult(ACCEPTED, message_id=message_id)


class GraphProvider(BaseProvider):
    """Microsoft 365 via Graph ``/users/{mailbox}/sendMail`` using the shared Microsoft 365 connection.

    App-only mode needs the Mail.Send application permission (restrict it to the reminder mailbox with an
    Exchange application access policy). Signed-in mode needs Mail.Send.Shared and "Send As" on the shared mailbox.
    """

    def missing_secrets(self):
        return m365.missing_env()

    def secret_status(self):
        return {name: name not in m365.missing_env() for name in m365.ENV_NAMES}

    def _token(self):
        try:
            return m365.access_token()
        except m365.M365Error as exc:
            raise ProviderError(str(exc))

    def _test(self):
        token = self._token()
        try:
            resp = requests.get(f"https://graph.microsoft.com/v1.0/users/{quote(self.profile.from_address)}?$select=id,mail",
                                headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            raise ProviderError("Could not reach Microsoft Graph.")
        if resp.status_code == 200:
            return ConnectionResult(True, "connected", "Connected to Microsoft 365 and the mailbox was found. Send a test email to confirm send permission.")
        if resp.status_code in (401, 403):
            # Reading the mailbox profile is not required to send; the connection itself works.
            return ConnectionResult(True, "connected", "Connected to Microsoft 365. Send a test email to confirm send permission for this mailbox.")
        if resp.status_code == 404:
            raise ProviderError("The sender mailbox was not found in the tenant.")
        raise ProviderError(f"Microsoft Graph returned HTTP {resp.status_code}.")

    def _send(self, email, idempotency_key):
        try:
            token = self._token()
        except ProviderError as exc:
            return SendResult(FAILED, error=str(exc))
        message = {
            "subject": email.subject,
            "body": {"contentType": "HTML", "content": email.html},
            "toRecipients": [{"emailAddress": {"address": a}} for a in email.to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in email.cc],
        }
        if self.profile.reply_to:
            message["replyTo"] = [{"emailAddress": {"address": self.profile.reply_to}}]
        if idempotency_key:
            message["internetMessageHeaders"] = [{"name": "X-QA-Idempotency-Key", "value": idempotency_key}]
        url = f"https://graph.microsoft.com/v1.0/users/{quote(self.profile.from_address)}/sendMail"
        try:
            resp = requests.post(url, json={"message": message, "saveToSentItems": True},
                                 headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT)
        except requests.ConnectTimeout:
            return SendResult(FAILED, error="Timed out connecting to Microsoft Graph; the message was not sent.")
        except (requests.Timeout, requests.ConnectionError):
            return SendResult(UNCERTAIN, error="No response from Microsoft Graph; delivery is uncertain.")
        request_id = resp.headers.get("request-id", "")
        if resp.status_code == 202:
            return SendResult(ACCEPTED, message_id=f"graph-request:{request_id}" if request_id else "")
        if resp.status_code >= 500:
            return SendResult(UNCERTAIN, message_id=request_id, error=f"Microsoft Graph returned HTTP {resp.status_code}; delivery is uncertain.")
        hint = " (Mail.Send application permission or mailbox access policy missing)" if resp.status_code in (401, 403) else ""
        return SendResult(FAILED, error=f"Microsoft Graph rejected the message (HTTP {resp.status_code}){hint}.")


class GmailProvider(BaseProvider):
    """Google Gmail API with an OAuth refresh token for the sender account (scope gmail.send)."""

    required_secrets = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")

    def _token(self):
        try:
            resp = requests.post("https://oauth2.googleapis.com/token", data={
                "client_id": _env(self.profile, "GMAIL_CLIENT_ID"), "client_secret": _env(self.profile, "GMAIL_CLIENT_SECRET"),
                "refresh_token": _env(self.profile, "GMAIL_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            raise ProviderError("Could not reach Google sign-in (check outbound network access).")
        if resp.status_code != 200:
            raise ProviderError("Google rejected the OAuth credentials (refresh token revoked/expired or client mismatch).")
        return resp.json()["access_token"]

    def _test(self):
        token = self._token()
        try:
            resp = requests.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                                headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            raise ProviderError("Could not reach the Gmail API.")
        if resp.status_code == 200:
            return ConnectionResult(True, "connected", "Connected to the Gmail API.")
        if resp.status_code == 403:
            return ConnectionResult(True, "connected", "Token acquired (profile read not permitted with gmail.send scope) - send a test email to confirm.")
        raise ProviderError(f"Gmail API returned HTTP {resp.status_code}.")

    def _send(self, email, idempotency_key):
        try:
            token = self._token()
        except ProviderError as exc:
            return SendResult(FAILED, error=str(exc))
        msg = EmailMessage()
        msg["Subject"] = email.subject
        msg["From"] = formataddr((self.profile.from_name, self.profile.from_address)) if self.profile.from_name else self.profile.from_address
        msg["To"] = ", ".join(email.to)
        if email.cc:
            msg["Cc"] = ", ".join(email.cc)
        if self.profile.reply_to:
            msg["Reply-To"] = self.profile.reply_to
        if idempotency_key:
            msg["X-QA-Idempotency-Key"] = idempotency_key
        msg.set_content(email.text or " ")
        msg.add_alternative(email.html, subtype="html")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        try:
            resp = requests.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json={"raw": raw},
                                 headers={"Authorization": f"Bearer {token}"}, timeout=HTTP_TIMEOUT)
        except requests.ConnectTimeout:
            return SendResult(FAILED, error="Timed out connecting to the Gmail API; the message was not sent.")
        except (requests.Timeout, requests.ConnectionError):
            return SendResult(UNCERTAIN, error="No response from the Gmail API; delivery is uncertain.")
        if resp.status_code == 200:
            return SendResult(ACCEPTED, message_id=resp.json().get("id", ""))
        if resp.status_code >= 500:
            return SendResult(UNCERTAIN, error=f"Gmail API returned HTTP {resp.status_code}; delivery is uncertain.")
        return SendResult(FAILED, error=f"Gmail API rejected the message (HTTP {resp.status_code}).")


PROVIDERS = {
    "console": ConsoleProvider,
    "smtp": SmtpProvider,
    "microsoft_graph": GraphProvider,
    "gmail_api": GmailProvider,
}


def get_provider(profile) -> BaseProvider:
    try:
        return PROVIDERS[profile.provider](profile)
    except KeyError:
        raise ProviderError(f"Unsupported provider '{profile.provider}'.")
