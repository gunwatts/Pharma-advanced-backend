import os
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AuthToken, Role, UserScope
from apps.core.models import AuditEvent
from apps.dashboards.models import Dashboard, DashboardAssignment, Widget
from apps.records.models import Record
from apps.sources.sync import sync_mapping

from .helpers import HEADERS, QATestCase, workbook

User = get_user_model()
PASSWORD = "Str0ng-Passphrase!"


class ApiTestCase(QATestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.make_user("admin1", "admin", all_scope=True)
        self.viewer = self.make_user("viewer1", "viewer", scopes=[(self.u1, self.qa)])
        self.manager = self.make_user("mgr1", "qa_manager", scopes=[(self.u1, None)])
        self.m = self.mapping(self.cc, "CC")
        self.mo = self.mapping(self.obs, "OBS", {"audit_ref": "Audit No"})
        self.ma = self.mapping(self.act, "ACT", {"observation_ref": "Obs No"})
        content = workbook({
            "CC": [HEADERS, self.row("CC-QA1"), self.row("CC-QC1", dept="QC"), self.row("CC-U2", unit="U2")],
            "OBS": [HEADERS + ["Audit No"], self.row("OBS-1") + ["A-1"]],
            "ACT": [HEADERS + ["Obs No"], self.row("ACT-QA") + ["OBS-1"], self.row("ACT-QC", dept="QC") + ["OBS-1"]],
        })
        for m in (self.m, self.mo, self.ma):
            sync_mapping(m, content=content)

    def make_user(self, username, role_key, all_scope=False, scopes=()):
        user = User.objects.create_user(username, f"{username}@example.com", PASSWORD)
        user.profile.role = Role.objects.get(key=role_key)
        user.profile.all_data_scope = all_scope
        user.profile.save()
        for unit, dept in scopes:
            UserScope.objects.create(user=user, unit=unit, department=dept)
        return user

    def client_for(self, user):
        client = APIClient()
        raw, _ = AuthToken.issue(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client


class AuthTests(ApiTestCase):
    def test_login_returns_token_and_profile(self):
        resp = APIClient().post("/api/auth/login/", {"username": "viewer1", "password": PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.data)
        self.assertIn("records.view", resp.data["user"]["permissions"])
        self.assertNotIn("admin.configure", resp.data["user"]["permissions"])
        self.assertFalse(AuthToken.objects.filter(key_hash=resp.data["token"]).exists())  # only a hash is stored

    def test_bad_login_generic_error_and_lockout(self):
        client = APIClient()
        for _ in range(5):
            resp = client.post("/api/auth/login/", {"username": "viewer1", "password": "wrong"})
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(resp.data["detail"], "Invalid username or password.")
        resp = client.post("/api/auth/login/", {"username": "viewer1", "password": PASSWORD})
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(AuditEvent.objects.filter(action="login_failed").exists())

    def test_unauthenticated_requests_rejected(self):
        for url in ("/api/records/", "/api/auth/me/", "/api/senders/", "/api/dashboards/", "/api/setup/status/"):
            self.assertEqual(APIClient().get(url).status_code, 401, url)

    def test_expired_token_rejected(self):
        raw, token = AuthToken.issue(self.viewer)
        AuthToken.objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)

    def test_logout_revokes_token(self):
        client = self.client_for(self.viewer)
        self.assertEqual(client.post("/api/auth/logout/").status_code, 204)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)

    def test_health_is_public_and_not_sensitive(self):
        resp = APIClient().get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.data), {"status", "database", "time"})


class PermissionTests(ApiTestCase):
    ADMIN_ONLY = ["/api/senders/", "/api/sources/", "/api/mappings/", "/api/rules/", "/api/templates/", "/api/recipient-groups/",
                  "/api/settings/", "/api/setup/status/", "/api/users/", "/api/roles/", "/api/widgets/"]

    def test_viewer_cannot_reach_admin_endpoints(self):
        client = self.client_for(self.viewer)
        for url in self.ADMIN_ONLY:
            self.assertEqual(client.get(url).status_code, 403, url)
        self.assertEqual(client.post("/api/units/", {"code": "X", "name": "X"}).status_code, 403)
        self.assertEqual(client.post("/api/runs/run-now/", {"confirm": True}, format="json").status_code, 403)
        self.assertEqual(client.get("/api/audit/").status_code, 403)

    def test_admin_can_configure_and_changes_are_audited(self):
        client = self.client_for(self.admin)
        resp = client.post("/api/units/", {"code": "U9", "name": "Plant 9", "aliases": [], "active": True}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(AuditEvent.objects.filter(action="create", object_type="catalog.unit").exists())

    def test_destructive_delete_requires_confirmation(self):
        client = self.client_for(self.admin)
        field = self.cc.fields.get(key="owner")
        resp = client.delete(f"/api/fields/{field.pk}/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["code"], "confirmation_required")
        self.assertTrue(resp.data["impacts"])
        self.assertEqual(client.delete(f"/api/fields/{field.pk}/?confirm=true").status_code, 204)

    def test_in_use_items_cannot_be_deleted(self):
        client = self.client_for(self.admin)
        resp = client.delete(f"/api/units/{self.u1.pk}/?confirm=true")
        self.assertEqual(resp.status_code, 409)

    def test_recipient_email_validation(self):
        client = self.client_for(self.admin)
        from apps.catalog.models import Department
        dept = Department.objects.create(code="ENG", name="Engineering")
        bad = client.post("/api/recipient-groups/", {"unit": self.u1.pk, "department": dept.pk, "to_emails": ["not-an-email"]}, format="json")
        self.assertEqual(bad.status_code, 400)
        dup = client.post("/api/recipient-groups/", {"unit": self.u1.pk, "department": dept.pk, "to_emails": ["a@x.com", "A@x.com"]}, format="json")
        self.assertEqual(dup.status_code, 400)
        overlap = client.post("/api/recipient-groups/", {"unit": self.u1.pk, "department": dept.pk, "to_emails": ["a@x.com"], "cc_emails": ["a@x.com"]}, format="json")
        self.assertEqual(overlap.status_code, 400)


class ScopeTests(ApiTestCase):
    def ids(self, resp):
        return {r["source_id"] for r in resp.data["results"]}

    def test_records_list_is_scoped_server_side(self):
        resp = self.client_for(self.viewer).get("/api/records/")
        self.assertEqual(self.ids(resp), {"CC-QA1", "OBS-1", "ACT-QA"})
        # Filtering by another department returns nothing rather than leaking.
        resp = self.client_for(self.viewer).get(f"/api/records/?department={self.qc.pk}")
        self.assertEqual(self.ids(resp), set())
        resp = self.client_for(self.manager).get("/api/records/")
        self.assertEqual(self.ids(resp), {"CC-QA1", "CC-QC1", "OBS-1", "ACT-QA", "ACT-QC"})

    def test_direct_detail_outside_scope_is_not_found(self):
        other = Record.objects.get(source_id="CC-QC1")
        self.assertEqual(self.client_for(self.viewer).get(f"/api/records/{other.pk}/").status_code, 404)

    def test_children_of_parent_filtered_by_scope(self):
        parent = Record.objects.get(source_id="OBS-1")
        resp = self.client_for(self.viewer).get(f"/api/records/{parent.pk}/")
        self.assertEqual([c["source_id"] for c in resp.data["children"]], ["ACT-QA"])

    def test_search_does_not_leak(self):
        resp = self.client_for(self.viewer).get("/api/records/?q=QC1")
        self.assertEqual(self.ids(resp), set())

    def test_user_without_scope_sees_nothing(self):
        lonely = self.make_user("lonely", "viewer")
        self.assertEqual(self.client_for(lonely).get("/api/records/").data["count"], 0)

    def test_export_requires_permission_and_is_scoped(self):
        self.assertEqual(self.client_for(self.viewer).get("/api/records/export/").status_code, 403)
        resp = self.client_for(self.manager).get("/api/records/export/")
        body = b"".join(resp.streaming_content).decode()
        self.assertIn("CC-QC1", body)
        self.assertNotIn("CC-U2", body)
        self.assertTrue(AuditEvent.objects.filter(action="records_export").exists())


class DashboardAccessTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.dash = Dashboard.objects.create(name="Viewer dash", is_published=True)
        self.widget = Widget.objects.create(dashboard=self.dash, title="By dept", widget_type="bar", group_by="department")
        Widget.objects.create(dashboard=self.dash, title="Count", widget_type="kpi")
        DashboardAssignment.objects.create(dashboard=self.dash, role=Role.objects.get(key="viewer"))
        self.secret = Dashboard.objects.create(name="Managers only", is_published=True)
        DashboardAssignment.objects.create(dashboard=self.secret, role=Role.objects.get(key="qa_manager"))

    def test_assigned_dashboard_default_and_data_scoped(self):
        client = self.client_for(self.viewer)
        self.assertEqual(client.get("/api/auth/me/").data["default_dashboard_id"], self.dash.pk)
        data = client.get(f"/api/dashboards/{self.dash.pk}/data/").data
        kpi = next(w for w in data["widgets"] if w["data"].get("value") is not None)
        self.assertEqual(kpi["data"]["value"], 3)
        bar = next(w for w in data["widgets"] if "series" in w["data"])
        self.assertEqual({s["key"] for s in bar["data"]["series"]}, {"Quality Assurance"})

    def test_unassigned_dashboard_denied_by_direct_url(self):
        client = self.client_for(self.viewer)
        self.assertEqual(client.get(f"/api/dashboards/{self.secret.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/dashboards/{self.secret.pk}/data/").status_code, 404)
        self.assertNotIn(self.secret.pk, [d["id"] for d in client.get("/api/dashboards/").data["results"]])

    def test_unpublished_dashboard_hidden_from_viewers(self):
        self.dash.is_published = False
        self.dash.save()
        self.assertEqual(self.client_for(self.viewer).get(f"/api/dashboards/{self.dash.pk}/data/").status_code, 404)

    def test_drill_down_is_scoped_and_requires_access(self):
        client = self.client_for(self.viewer)
        resp = client.get(f"/api/records/?widget={self.widget.pk}&group=Quality Control")
        self.assertEqual(resp.data["count"], 0)
        secret_widget = Widget.objects.create(dashboard=self.secret, title="x", widget_type="kpi")
        self.assertEqual(client.get(f"/api/records/?widget={secret_widget.pk}").status_code, 404)

    def test_admin_builds_publishes_and_assigns_dashboard(self):
        client = self.client_for(self.admin)
        dash = client.post("/api/dashboards/", {"name": "New", "description": ""}, format="json").data
        w = client.post("/api/widgets/", {"dashboard": dash["id"], "title": "Upcoming", "widget_type": "upcoming", "date_field": "target_date"}, format="json")
        self.assertEqual(w.status_code, 201, w.data)
        bad = client.post("/api/widgets/", {"dashboard": dash["id"], "title": "x", "widget_type": "table", "filters": [{"field": "status", "op": "DROP TABLE"}]}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(client.post(f"/api/dashboards/{dash['id']}/publish/", {"published": True}, format="json").status_code, 200)
        a = client.post("/api/dashboard-assignments/", {"dashboard": dash["id"], "user": self.viewer.pk}, format="json")
        self.assertEqual(a.status_code, 201, a.data)
        ids = [d["id"] for d in self.client_for(self.viewer).get("/api/dashboards/").data["results"]]
        self.assertIn(dash["id"], ids)
        clone = client.post(f"/api/dashboards/{dash['id']}/clone/")
        self.assertEqual(clone.status_code, 201)
        self.assertEqual(len(clone.data["widgets"]), 1)


class SecretHandlingTests(ApiTestCase):
    def test_sender_api_never_returns_secret_values(self):
        client = self.client_for(self.admin)
        with mock.patch.dict(os.environ, {"SENDER_SMTPX_SMTP_PASSWORD": "hunter2-very-secret"}):
            resp = client.post("/api/senders/", {"name": "SMTP", "provider": "smtp", "from_address": "a@example.com",
                                                 "secret_env_prefix": "sender_smtpx", "settings": {"host": "smtp.example.com", "port": 587}}, format="json")
            self.assertEqual(resp.status_code, 201, resp.data)
            listing = client.get("/api/senders/").content.decode()
        self.assertNotIn("hunter2", listing)
        self.assertIn('"SENDER_SMTPX_SMTP_PASSWORD":true', listing.replace(" ", ""))

    def test_secrets_rejected_in_sender_settings(self):
        resp = self.client_for(self.admin).post("/api/senders/", {"name": "x", "provider": "smtp", "from_address": "a@example.com", "secret_env_prefix": "S_X",
                                                                  "settings": {"host": "h", "password": "p"}}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_missing_credentials_reported_without_values(self):
        client = self.client_for(self.admin)
        sid = client.post("/api/senders/", {"name": "Graph", "provider": "microsoft_graph", "from_address": "a@example.com",
                                            "secret_env_prefix": "SENDER_NOPE"}, format="json").data["id"]
        resp = client.post(f"/api/senders/{sid}/test-connection/")
        self.assertEqual(resp.data["status"], "credentials_missing")
        self.assertIn("GRAPH_CLIENT_SECRET", resp.data["message"])

    @override_settings(SOURCE_ROOTS={"share": "/tmp"})
    def test_server_folder_path_traversal_rejected(self):
        client = self.client_for(self.admin)
        resp = client.get("/api/browse/server/?root=share&path=../etc")
        self.assertEqual(resp.status_code, 400)
        resp = client.post("/api/sources/", {"name": "bad", "provider": "server_folder", "config": {"root": "share", "path": "../../etc/passwd.xlsx"}}, format="json")
        self.assertEqual(resp.status_code, 400)
        resp = client.post("/api/sources/", {"name": "bad2", "provider": "server_folder", "config": {"root": "unknown", "path": "a.xlsx"}}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("/tmp", client.get("/api/source-options/").content.decode())

    def test_unhandled_errors_do_not_leak_details(self):
        client = self.client_for(self.admin)
        with mock.patch("apps.core.views.SystemSettings.load", side_effect=RuntimeError("db password=abc")):
            resp = client.get("/api/settings/")
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn("password", resp.content.decode())
        self.assertIn("error_id", resp.data)


class SetupFlowTests(ApiTestCase):
    def test_activation_requires_confirmation_and_readiness(self):
        from apps.core.models import SystemSettings
        SystemSettings.objects.filter(pk=1).update(reminders_active=False)
        client = self.client_for(self.admin)
        self.assertEqual(client.post("/api/setup/activate/", {}, format="json").status_code, 400)
        resp = client.post("/api/setup/activate/", {"confirm": True}, format="json")
        self.assertEqual(resp.status_code, 400)  # no rule / dry run / test email yet
        self.assertIn("failing", resp.data)
        status = client.get("/api/setup/status/").data
        self.assertFalse(status["ready_to_activate"])
        self.assertIn("sender", status["checks"])

    def test_dry_run_endpoint_and_template_preview(self):
        self.rule(self.cc)
        self.upload(workbook({"CC": [HEADERS, self.row("CC-QA1")], "OBS": [HEADERS + ["Audit No"]], "ACT": [HEADERS + ["Obs No"]]}))
        client = self.client_for(self.admin)
        resp = client.post("/api/runs/dry-run/", {"sync": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("groups", resp.data)
        preview = client.post("/api/templates/preview/", {"subject": "Hi {{ unit_name }}", "intro_text": "x", "outro_text": "",
                                                           "no_pending_subject": "None", "no_pending_text": ""}, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["subject"], "Hi Plant 1")
        existing = client.post("/api/templates/preview/", {"name": "Default daily reminder", "subject": "S", "no_pending_subject": "N"}, format="json")
        self.assertEqual(existing.status_code, 200, existing.data)  # previewing an existing template must not fail uniqueness
        bad = client.post("/api/templates/preview/", {"subject": "{{ nope }}", "no_pending_subject": "x"}, format="json")
        self.assertEqual(bad.status_code, 400)
