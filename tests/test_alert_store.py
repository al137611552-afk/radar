import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_store  # noqa: E402


class AlertStoreTests(unittest.TestCase):
    def _alerts(self):
        return pd.DataFrame([
            {
                "code": "au2608C880",
                "name": "黄金购880",
                "underlying": "au2608",
                "option_type": "CALL",
                "dte": 6,
                "confirmation_score": 6,
                "alert_type": "首次命中",
            },
            {
                "code": "rb2610P3000",
                "name": "螺纹钢沽3000",
                "underlying": "rb2610",
                "option_type": "PUT",
                "dte": 8,
                "confirmation_score": 4,
                "alert_type": "信号失效",
            },
        ])

    def test_option_alerts_are_idempotent_for_one_logical_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            slot = "2026-07-21T14:00:00+08:00"

            first = alert_store.enqueue_option_alerts(path, self._alerts(), slot)
            repeated = alert_store.enqueue_option_alerts(path, self._alerts(), slot)
            rows = alert_store.load_recent_alerts(path)

            self.assertEqual((first, repeated), (2, 0))
            self.assertEqual(rows["entity_code"].tolist(), [
                "rb2610P3000", "au2608C880",
            ])
            self.assertEqual(rows["delivery_status"].tolist(), ["pending", "pending"])
            self.assertEqual(rows["severity"].tolist(), ["warning", "info"])
            self.assertEqual(rows["logical_slot"].tolist(), [slot, slot])
            payload = json.loads(rows.loc[0, "payload_json"])
            self.assertEqual(payload["underlying"], "rb2610")
            self.assertNotIn("NaN", rows.loc[0, "payload_json"])

    def test_later_logical_slot_can_repeat_same_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            alerts = self._alerts().iloc[[0]]

            first = alert_store.enqueue_option_alerts(
                path, alerts, "2026-07-21T14:00:00+08:00"
            )
            later = alert_store.enqueue_option_alerts(
                path, alerts, "2026-07-21T15:00:00+08:00"
            )
            rows = alert_store.load_recent_alerts(path)

            self.assertEqual((first, later), (1, 1))
            self.assertEqual(rows["logical_slot"].tolist(), [
                "2026-07-21T15:00:00+08:00",
                "2026-07-21T14:00:00+08:00",
            ])

    def test_reader_reports_full_status_counts_beyond_recent_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            alerts = pd.DataFrame([
                {"code": f"C{index:03d}", "alert_type": "首次命中"}
                for index in range(103)
            ])
            alert_store.enqueue_option_alerts(
                path, alerts, "2026-07-21T14:00:00+08:00"
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE alert_events SET delivery_status = 'delivered' WHERE id = 1"
                )
                connection.execute(
                    "UPDATE alert_events SET delivery_status = 'failed' WHERE id = 2"
                )

            rows, counts = alert_store.load_alert_dashboard(path, limit=2)

            self.assertEqual(len(rows), 2)
            self.assertEqual(counts, {
                "total": 103, "pending": 101, "delivered": 1, "failed": 1,
            })

    def test_reader_handles_sqlite_uri_metacharacters_in_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts?#%.db"
            alert_store.enqueue_option_alerts(
                path,
                pd.DataFrame([{"code": "auC1", "alert_type": "首次命中"}]),
                "2026-07-21T14:00:00+08:00",
            )

            rows = alert_store.load_recent_alerts(path)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows.iloc[0]["entity_code"], "auC1")

    def test_reader_is_bounded_and_does_not_create_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"

            rows = alert_store.load_recent_alerts(path, limit=2)

            self.assertTrue(rows.empty)
            self.assertFalse(path.exists())
            with self.assertRaisesRegex(ValueError, "positive integer"):
                alert_store.load_recent_alerts(path, limit=0)

    def test_rejects_alert_rows_without_required_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            for column in ("code", "alert_type"):
                with self.subTest(column=column), self.assertRaisesRegex(
                    ValueError, "missing required column"
                ):
                    alert_store.enqueue_option_alerts(
                        path,
                        self._alerts().drop(columns=[column]),
                        "2026-07-21T14:00:00+08:00",
                    )


if __name__ == "__main__":
    unittest.main()
