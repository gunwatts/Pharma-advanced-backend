import os
from unittest import mock

from django.core.cache import cache
from rest_framework.test import APIClient

from apps.core import m365
from apps.core.models import M365Connection
from apps.sources import connectors

from .test_api import ApiTestCase

ENV = {"GRAPH_TENANT_ID": "tenant-1", "GRAPH_CLIENT_ID": "client-1", "GRAPH_CLIENT_SECRET": "very-secret-value", "M365_REDIRECT_URI": "https://qa.example/api/m365/callback/"}


class FakeResponse:
    def __init__(self, status, body, ctype="application/json"):
        self.status_code, self._body, self.headers = status, body, {"content-type": ctype}

    def json(self):
        return self._body


@mock.patch.dict(os.environ, ENV)
class M365Tests(ApiTestCase):
    def setUp(self):
        super().setUp()
        m365.clear_cache()

    def test_status_requires_admin_and_hides_secrets(self):
        self.assertEqual(self.client_for(self.viewer).get("/api/m365/status/").status_code, 403)
        resp = self.client_for(self.admin).get("/api/m365/status/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["configured"])
        self.assertNotIn("very-secret-value", resp.content.decode())

    def test_login_url_and_callback_store_encrypted_refresh_token(self):
        url = self.client_for(self.admin).post("/api/m365/login/").data["url"]
        self.assertIn("login.microsoftonline.com/tenant-1", url)
        state = url.split("state=")[1].split("&")[0]
        responses = [FakeResponse(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})]
        with mock.patch("apps.core.m365.requests.post", side_effect=responses), \
                mock.patch("apps.core.m365.requests.get", return_value=FakeResponse(200, {"mail": "cqa.head@example.com"})):
            resp = APIClient().get(f"/api/m365/callback/?code=abc&state={state}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("m365=connected", resp["Location"])
        conn = M365Connection.load()
        self.assertEqual((conn.mode, conn.account), ("delegated", "cqa.head@example.com"))
        self.assertNotIn("rt-1", conn.refresh_token_encrypted)  # never stored in plain text
        # The state is single-use.
        again = APIClient().get(f"/api/m365/callback/?code=abc&state={state}")
        self.assertIn("m365=error", again["Location"])

    def test_callback_with_unknown_state_is_rejected(self):
        with mock.patch("apps.core.m365.requests.post") as post:
            resp = APIClient().get("/api/m365/callback/?code=abc&state=forged")
        post.assert_not_called()
        self.assertIn("m365=error", resp["Location"])

    def test_delegated_refresh_rotates_token(self):
        conn = M365Connection.load()
        conn.mode, conn.refresh_token_encrypted = "delegated", m365._fernet().encrypt(b"rt-old").decode()
        conn.save()
        with mock.patch("apps.core.m365.requests.post", return_value=FakeResponse(200, {"access_token": "at-2", "refresh_token": "rt-new", "expires_in": 3600})) as post:
            self.assertEqual(m365.access_token(), "at-2")
            self.assertEqual(m365.access_token(), "at-2")  # cached
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        conn.refresh_from_db()
        self.assertEqual(m365._fernet().decrypt(conn.refresh_token_encrypted.encode()), b"rt-new")

    def test_expired_sign_in_gives_clear_message(self):
        conn = M365Connection.load()
        conn.mode, conn.refresh_token_encrypted = "delegated", m365._fernet().encrypt(b"rt").decode()
        conn.save()
        with mock.patch("apps.core.m365.requests.post", return_value=FakeResponse(400, {"error": "invalid_grant"})):
            with self.assertRaisesRegex(m365.M365Error, "Sign in again"):
                m365.access_token()

    def test_app_mode_uses_client_credentials(self):
        with mock.patch("apps.core.m365.requests.post", return_value=FakeResponse(200, {"access_token": "app-at", "expires_in": 3600})) as post:
            self.assertEqual(m365.access_token(), "app-at")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "client_credentials")

    def test_resolve_share_link(self):
        item = {"id": "item-9", "name": "Plant1_QMS.xlsx", "file": {}, "parentReference": {"driveId": "drive-3"}, "webUrl": "https://x"}
        with mock.patch("apps.sources.connectors.graph_token", return_value="t"), \
                mock.patch("apps.sources.connectors.requests.get", return_value=FakeResponse(200, item)) as get:
            cfg = connectors.resolve_share_link("https://contoso.sharepoint.com/:x:/g/personal/a/EaBc")
        self.assertEqual((cfg["drive_id"], cfg["item_id"], cfg["display_path"]), ("drive-3", "item-9", "Plant1_QMS.xlsx"))
        self.assertIn("/shares/u!", get.call_args.args[0])
        with self.assertRaises(connectors.SourceError):
            connectors.resolve_share_link("https://evil.example.com/file.xlsx")

    def tearDown(self):
        cache.clear()
        super().tearDown()
