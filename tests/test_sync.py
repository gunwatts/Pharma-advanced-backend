from apps.core.models import AuditEvent
from apps.records.models import Record
from apps.reminders.models import Delivery
from apps.sources.models import SyncRun
from apps.sources.sync import sync_mapping, validate_mapping

from .helpers import HEADERS, QATestCase, workbook


class SyncTests(QATestCase):
    def setUp(self):
        super().setUp()
        self.m = self.mapping(self.cc, "CC")

    def sync(self, rows, headers=HEADERS):
        return sync_mapping(self.m, content=workbook({"CC": [headers, *rows]}))

    def test_imports_open_records_and_skips_closed(self):
        run = self.sync([self.row("CC-1"), self.row("CC-2", status="Closed"), self.row("CC-3", unit="P1")])
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        self.assertEqual(set(Record.objects.values_list("source_id", flat=True)), {"CC-1", "CC-3"})
        rec = Record.objects.get(source_id="CC-1")
        self.assertEqual(rec.unit, self.u1)
        self.assertEqual(rec.title, "Record CC-1")
        self.assertIn("target_date", rec.data)
        self.assertNotIn("Record No", rec.data)  # only mapped fields are stored

    def test_resync_is_idempotent(self):
        rows = [self.row("CC-1"), self.row("CC-2")]
        self.sync(rows)
        run = self.sync(rows)
        self.assertEqual(Record.objects.count(), 2)
        self.assertEqual((run.records_created, run.records_updated), (0, 2))

    def test_missing_required_column_fails_without_changes(self):
        self.sync([self.row("CC-1")])
        headers = [h for h in HEADERS if h != "Department"]
        run = sync_mapping(self.m, content=workbook({"CC": [headers, ["CC-9", "U1", "x", "Open", "01-01-2030", "o"]]}))
        self.assertEqual(run.status, SyncRun.STATUS_FAILED)
        self.assertIn("Department", run.message)
        self.assertEqual(list(Record.objects.values_list("source_id", flat=True)), ["CC-1"])

    def test_missing_worksheet_fails_without_changes(self):
        self.sync([self.row("CC-1")])
        run = sync_mapping(self.m, content=workbook({"Other": [HEADERS]}))
        self.assertEqual(run.status, SyncRun.STATUS_FAILED)
        self.assertEqual(Record.objects.count(), 1)

    def test_corrupt_file_fails_without_changes(self):
        self.sync([self.row("CC-1")])
        run = sync_mapping(self.m, content=b"not a workbook")
        self.assertEqual(run.status, SyncRun.STATUS_FAILED)
        self.assertEqual(Record.objects.count(), 1)

    def test_unreachable_source_fails_without_deleting(self):
        self.sync([self.row("CC-1")])
        run = sync_mapping(self.m)  # nothing uploaded -> source error
        self.assertEqual(run.status, SyncRun.STATUS_FAILED)
        self.assertEqual(Record.objects.count(), 1)
        self.m.refresh_from_db()
        self.assertEqual(self.m.last_status, "failed")
        self.assertIsNotNone(self.m.last_success_at)

    def test_duplicate_ids_rejected_and_sync_incomplete(self):
        self.sync([self.row("CC-1", offset=1)])
        run = self.sync([self.row("CC-1", offset=3), self.row("CC-1", offset=4), self.row("CC-2")])
        self.assertEqual(run.status, SyncRun.STATUS_INCOMPLETE)
        self.assertEqual(run.rows_rejected, 1)
        # Nothing changed: CC-2 not imported, CC-1 untouched.
        self.assertEqual(list(Record.objects.values_list("source_id", flat=True)), ["CC-1"])

    def test_rejections_within_threshold_apply_and_protect_rejected_ids(self):
        self.m.max_rejected_rows = 5
        self.m.save()
        self.sync([self.row("CC-1"), self.row("CC-2")])
        run = self.sync([self.row("CC-1", unit="Nowhere"), self.row("CC-3")])
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        # CC-1 was rejected (unknown unit) so it must not be removed; CC-2 disappeared and is removed.
        self.assertEqual(set(Record.objects.values_list("source_id", flat=True)), {"CC-1", "CC-3"})

    def test_rows_without_id_block_missing_record_cleanup(self):
        self.m.max_rejected_rows = 5
        self.m.save()
        self.sync([self.row("CC-1"), self.row("CC-2")])
        run = self.sync([self.row("CC-1"), self.row(None)])
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        self.assertEqual(run.records_removed_missing, 0)
        self.assertEqual(Record.objects.count(), 2)

    def test_unknown_unit_or_department_is_rejected(self):
        run = self.sync([self.row("CC-1", unit="Mars"), self.row("CC-2", dept="Kitchen")])
        self.assertEqual(run.status, SyncRun.STATUS_INCOMPLETE)
        self.assertEqual(run.rows_rejected, 2)

    def test_invalid_and_missing_dates_are_flagged_not_silently_accepted(self):
        bad = self.row("CC-1")
        bad[5] = "31-31-2026"
        run = self.sync([bad, self.row("CC-2", offset=None)])
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        codes = {r.source_id: {i["code"] for i in r.issues} for r in Record.objects.all()}
        self.assertIn("invalid_date", codes["CC-1"])
        self.assertIn("missing_required", codes["CC-2"])
        self.assertIsNone(Record.objects.get(source_id="CC-1").data["target_date"])

    def test_excel_native_dates_and_serials_parse(self):
        from datetime import datetime
        row1 = self.row("CC-1")
        row1[5] = datetime(2030, 1, 15)
        row2 = self.row("CC-2")
        row2[5] = 47000  # Excel serial
        self.sync([row1, row2])
        self.assertEqual(Record.objects.get(source_id="CC-1").data["target_date"], "2030-01-15")
        self.assertTrue(Record.objects.get(source_id="CC-2").data["target_date"].startswith("2028"))

    def test_unknown_status_flagged(self):
        self.sync([self.row("CC-1", status="Weird")])
        self.assertEqual(Record.objects.get().issues[0]["code"], "unknown_status")

    def test_closed_records_removed_only_after_successful_sync(self):
        self.sync([self.row("CC-1"), self.row("CC-2")])
        # Failed sync where CC-1 shows Closed must not remove it.
        headers = [h for h in HEADERS if h != "Plant"]
        sync_mapping(self.m, content=workbook({"CC": [headers, ["CC-1", "QA", "x", "Closed", "01-01-2030", "o"]]}))
        self.assertEqual(Record.objects.count(), 2)
        run = self.sync([self.row("CC-1", status="Closed"), self.row("CC-2")])
        self.assertEqual(run.records_removed_closed, 1)
        self.assertEqual(list(Record.objects.values_list("source_id", flat=True)), ["CC-2"])

    def test_cleanup_preserves_ledger_and_audit(self):
        self.sync([self.row("CC-1")])
        Delivery.objects.create(idempotency_key="k1", run_date="2026-01-01", kind="reminder", state="sent")
        AuditEvent.objects.create(action="x")
        self.sync([self.row("CC-1", status="Closed")])
        self.assertEqual(Record.objects.count(), 0)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)

    def test_validate_mapping_reports_problems_without_writing(self):
        content = workbook({"CC": [HEADERS + ["Extra"], self.row("CC-1") + ["x"], self.row("CC-1") + ["y"], self.row("CC-2", unit="Mars") + ["z"]]})
        report = validate_mapping(self.m, content)
        self.assertFalse(report["ok"])
        self.assertEqual(report["rows_rejected"], 2)
        self.assertIn("Extra", report["unmapped_columns"])
        self.assertEqual(Record.objects.count(), 0)

    def test_fixed_unit_mapping_needs_no_unit_column(self):
        self.m.unit_column, self.m.fixed_unit = "", self.u2
        self.m.save()
        headers = [h for h in HEADERS if h != "Plant"]
        run = sync_mapping(self.m, content=workbook({"CC": [headers, ["CC-1", "QA", "x", "Open", "01-01-2030", "o"]]}))
        self.assertEqual(run.status, SyncRun.STATUS_SUCCESS)
        self.assertEqual(Record.objects.get().unit, self.u2)

    def test_parent_links_use_explicit_relationship(self):
        mo = self.mapping(self.obs, "OBS", {"audit_ref": "Audit No"})
        ma = self.mapping(self.audit, "AUD")
        content = workbook({"AUD": [HEADERS, self.row("AUD-1")], "OBS": [HEADERS + ["Audit No"], self.row("OBS-1") + ["AUD-1"], self.row("OBS-2") + ["AUD-404"]]})
        sync_mapping(ma, content=content)
        sync_mapping(mo, content=content)
        self.assertEqual(Record.objects.get(source_id="OBS-1").parent.source_id, "AUD-1")
        orphan = Record.objects.get(source_id="OBS-2")
        self.assertIsNone(orphan.parent)
        self.assertIn("parent_not_found", {i["code"] for i in orphan.issues})
