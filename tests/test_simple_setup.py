import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook

from apps.catalog.models import Department, RecipientGroup
from apps.reminders.models import Delivery, ReminderRule
from apps.sources.models import SourceMapping

from .helpers import d, workbook
from .test_api import PASSWORD, ApiTestCase

EVENT = ["QMS Number", "Department", "Description", "Target Date", "Status"]
ACTION = ["Action ID", "QMS Number", "Department", "Description", "Target Date", "Status"]


class SimpleSetupTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        SourceMapping.objects.update(active=False)  # legacy fixture mappings (no file) would otherwise hold every plant
        self.api = self.client_for(self.admin)
        self.api.get("/api/simple/layout/")  # recreates the default rule for every event type
        self.prod = Department.objects.create(code="PROD", name="Production", aliases=["Prodn"])

    def create_plant(self, code, name):
        resp = self.api.post("/api/simple/plants/", {"code": code, "name": name}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data

    def set_departments(self, plant_id, rows):
        return self.api.put(f"/api/simple/plants/{plant_id}/departments/", {"departments": rows}, format="json")

    def upload(self, plant_id, sheets):
        f = SimpleUploadedFile("plant.xlsx", workbook(sheets))
        return self.api.post(f"/api/simple/plants/{plant_id}/workbook-upload/", {"file": f}, format="multipart")

    def full_sheets(self, cc_rows=(), actions=()):
        sheets = {name: [EVENT] for name in ("Change Control", "Deviation", "Incident", "CAPA", "OOS", "OOT")}
        sheets["Change Control"] = [EVENT, *cc_rows]
        sheets["Action Plans"] = [ACTION, *actions]
        return sheets

    def test_layout_lists_seeded_event_types_and_template_matches(self):
        data = self.api.get("/api/simple/layout/").data
        names = {t["sheet_name"]: t for t in data["event_types"] if t["active"]}
        self.assertEqual(set(names), {"Change Control", "Deviation", "Incident", "CAPA", "OOS", "OOT", "Action Plans"})
        self.assertEqual(names["Action Plans"]["kind"], "action")
        self.assertIn("Open", names["CAPA"]["open_statuses"])
        resp = self.api.get("/api/simple/template.xlsx")
        wb = load_workbook(io.BytesIO(resp.content))
        self.assertIn("Action Plans", wb.sheetnames)
        self.assertEqual([c.value for c in wb["Action Plans"][1]], ACTION)

    def test_full_flow_plant_departments_workbook_preview(self):
        p9 = self.create_plant("P9", "Plant 9")
        resp = self.set_departments(p9["id"], [
            {"department": self.qa.pk, "to": ["qa.head.p9@example.com"], "cc": ["qa.team.p9@example.com"]},
            {"department": self.qc.pk, "to": ["qc.head.p9@example.com"], "cc": []},
            {"department": self.prod.pk, "to": ["prod.p9@example.com"]}])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data["departments"]), 3)
        sheets = self.full_sheets(
            cc_rows=[["CC-26-001", "Prodn", "New product ABC 5 mg", d(120), "Open"]],
            actions=[["A-1", "CC-26-001", "QC", "Prepare specification", d(2), "Open"],
                     ["A-2", "CC-26-001", "QA", "Approve artwork", d(30), "Open"],
                     ["A-3", "CC-26-001", "QA", "Done already", d(1), "Closed"]])
        resp = self.upload(p9["id"], sheets)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["check"]["ok"], resp.data["check"])
        today = self.api.get("/api/simple/today/?sync=1").data
        groups = {(g["unit"], g["department"]): g for g in today["groups"]}
        qc = groups[("Plant 9", "Quality Control")]
        self.assertEqual([(i["source_id"], i["section"]) for i in qc["included"]], [("A-1", "action_required")])
        self.assertIsNotNone(qc["email"], (qc["action"], qc["hold_reasons"], qc["excluded"]))
        self.assertIn("New product ABC 5 mg", qc["email"]["html"])
        qa = groups[("Plant 9", "Quality Assurance")]
        self.assertEqual([(i["source_id"], i["section"]) for i in qa["included"]], [("A-2", "periodic")])
        self.assertEqual(groups[("Plant 9", "Production")]["action"], "no_pending")  # CC itself is 120 days out (beyond horizon)

    def test_missing_sheet_or_column_is_reported_and_holds_plant(self):
        p9 = self.create_plant("P9", "Plant 9")
        self.set_departments(p9["id"], [{"department": self.qa.pk, "to": ["qa@example.com"]}])
        sheets = self.full_sheets()
        sheets["Action Plans"] = [["Action ID", "Department", "Description", "Target Date", "Status"]]  # QMS Number column missing
        check = self.upload(p9["id"], sheets).data["check"]
        self.assertFalse(check["ok"])
        bad = next(s for s in check["sheets"] if s["sheet"] == "Action Plans")
        self.assertTrue(any("QMS Number" in p for p in bad["problems"]))
        today = self.api.get("/api/simple/today/?sync=1").data
        self.assertEqual(next(g for g in today["groups"] if g["unit"] == "Plant 9")["action"], "held")

    def test_add_new_event_type_and_change_rule(self):
        resp = self.api.post("/api/simple/event-types/", {
            "name": "Market Complaint", "sheet_name": "Complaints",
            "columns": {"id": "Complaint No", "department": "Dept", "description": "Details", "target_date": "Due", "status": "Status"},
            "open_statuses": ["Open", "Under Investigation"], "closed_statuses": ["Closed"],
            "rule": {"window_days": 3, "repeat_every_days": 30, "repeat_anchor": "first_seen", "start_days_before": None}}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        rule = ReminderRule.objects.get(category_id=resp.data["id"])
        self.assertEqual((rule.window_days, rule.repeat_every_days, rule.repeat_anchor, rule.include_statuses),
                         (3, 30, "first_seen", ["Open", "Under Investigation"]))
        bad = self.api.post("/api/simple/event-types/", {"name": "X", "sheet_name": "Complaints", "columns": {}, "open_statuses": ["Open"],
                                                          "rule": {"window_days": 1, "repeat_anchor": "due_date"}}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.api.delete(f"/api/simple/event-types/{resp.data['id']}/").data["active"], False)

    def test_department_recipient_validation(self):
        p9 = self.create_plant("P9", "Plant 9")
        self.assertEqual(self.set_departments(p9["id"], [{"department": self.qa.pk, "to": ["not-an-email"]}]).status_code, 400)
        self.assertEqual(self.set_departments(p9["id"], [{"department": self.qa.pk, "to": []}]).status_code, 400)
        self.set_departments(p9["id"], [{"department": self.qa.pk, "to": ["a@example.com"]}, {"department": self.qc.pk, "to": ["b@example.com"]}])
        self.set_departments(p9["id"], [{"department": self.qa.pk, "to": ["a@example.com"]}])
        self.assertFalse(RecipientGroup.objects.get(unit__code="P9", department=self.qc).active)

    def test_viewer_cannot_use_setup_api(self):
        client = self.client_for(self.viewer)
        for url in ("/api/simple/layout/", "/api/simple/plants/", "/api/simple/users/", "/api/simple/template.xlsx"):
            self.assertEqual(client.get(url).status_code, 403, url)


class PlantQAHeadTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        api = self.client_for(self.admin)
        resp = api.post("/api/simple/users/", {"username": "qa.p1", "first_name": "QA", "last_name": "Head P1", "email": "qa.p1@example.com",
                                               "role": "plant_qa", "plants": [self.u1.pk], "password": PASSWORD}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        from django.contrib.auth import get_user_model
        self.qa_head = get_user_model().objects.get(username="qa.p1")
        self.rule(self.cc)

    def test_plant_qa_head_requires_plant(self):
        resp = self.client_for(self.admin).post("/api/simple/users/", {"username": "x", "role": "plant_qa", "plants": [], "password": PASSWORD},
                                                format="json")
        self.assertEqual(resp.status_code, 400)

    def test_sees_only_own_plant_in_today_and_history(self):
        client = self.client_for(self.qa_head)
        today = client.get("/api/simple/today/").data
        self.assertTrue(today["groups"])
        self.assertEqual({g["unit"] for g in today["groups"]}, {"Plant 1"})
        self.assertEqual(client.get("/api/simple/today/?sync=1").status_code, 403)  # cannot trigger a sync
        Delivery.objects.create(idempotency_key="k-u1", run_date="2026-01-01", kind="reminder", unit=self.u1, department=self.qa, state="sent")
        Delivery.objects.create(idempotency_key="k-u2", run_date="2026-01-01", kind="reminder", unit=self.u2, department=self.qa, state="sent")
        Delivery.objects.create(idempotency_key="k-alert", run_date="2026-01-01", kind="alert", state="sent")
        keys = {d["idempotency_key"] for d in client.get("/api/deliveries/").data["results"]}
        self.assertEqual(keys, {"k-u1"})
        self.assertEqual(client.get("/api/runs/").status_code, 403)
        self.assertEqual(client.get("/api/simple/plants/").status_code, 403)

    def test_admin_sees_all_plants(self):
        units = {g["unit"] for g in self.client_for(self.admin).get("/api/simple/today/").data["groups"]}
        self.assertEqual(units, {"Plant 1", "Plant 2"})
