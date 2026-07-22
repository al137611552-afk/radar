import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_store  # noqa: E402


class AlertStoreTests(unittest.TestCase):
    def _enqueue_one(self, path):
        alert_store.enqueue_option_alerts(
            path,
            pd.DataFrame([{"code": "auC1", "alert_type": "首次命中"}]),
            "2026-07-21T14:00:00+08:00",
        )

    def test_claim_migrates_a_pre_delivery_outbox_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """CREATE TABLE alert_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL, logical_slot TEXT NOT NULL,
                        entity_code TEXT NOT NULL, alert_type TEXT NOT NULL,
                        severity TEXT NOT NULL, title TEXT NOT NULL,
                        message TEXT NOT NULL, payload_json TEXT NOT NULL,
                        delivery_status TEXT NOT NULL, attempts INTEGER NOT NULL,
                        last_error TEXT, created_at TEXT NOT NULL,
                        UNIQUE(source, logical_slot, entity_code, alert_type)
                    )"""
                )

            self.assertEqual(alert_store.claim_alerts(
                path, now=datetime(2026, 7, 22, tzinfo=timezone.utc)
            ), [])
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(alert_events)"
                    )
                }
            self.assertTrue({
                "next_attempt_at", "lease_token", "lease_until", "delivered_at"
            }.issubset(columns))

    def test_concurrent_workers_claim_one_event_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue_one(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _item: alert_store.claim_alerts(path, now=now), range(2)
                ))

            self.assertEqual(sum(len(result) for result in results), 1)

    def test_claims_are_exclusive_until_their_lease_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue_one(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)

            first = alert_store.claim_alerts(path, now=now, lease_seconds=60)
            blocked = alert_store.claim_alerts(
                path, now=now + timedelta(seconds=59), lease_seconds=60
            )
            reclaimed = alert_store.claim_alerts(
                path, now=now + timedelta(seconds=60), lease_seconds=60
            )

            self.assertEqual(len(first), 1)
            self.assertEqual(blocked, [])
            self.assertEqual(len(reclaimed), 1)
            self.assertNotEqual(first[0]["lease_token"], reclaimed[0]["lease_token"])
            self.assertEqual(reclaimed[0]["attempts"], 2)

    def test_expired_lease_cannot_complete_or_fail_without_reclaiming(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue_one(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
            event = alert_store.claim_alerts(
                path, now=now, lease_seconds=60
            )[0]
            expired_at = now + timedelta(seconds=60)

            self.assertFalse(alert_store.mark_alert_delivered(
                path, event["id"], event["lease_token"],
                delivered_at=expired_at,
            ))
            self.assertFalse(alert_store.mark_alert_failed(
                path, event["id"], event["lease_token"], failed_at=expired_at,
                error_code="HTTP_503",
            ))
            reclaimed = alert_store.claim_alerts(path, now=expired_at)
            self.assertEqual(len(reclaimed), 1)
            self.assertEqual(reclaimed[0]["attempts"], 2)

    def test_delivery_completion_rejects_stale_lease_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue_one(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
            event = alert_store.claim_alerts(path, now=now)[0]

            stale = alert_store.mark_alert_delivered(
                path, event["id"], "stale-token", delivered_at=now
            )
            completed = alert_store.mark_alert_delivered(
                path, event["id"], event["lease_token"], delivered_at=now
            )

            row = alert_store.load_recent_alerts(path).iloc[0]
            self.assertFalse(stale)
            self.assertTrue(completed)
            self.assertEqual(row["delivery_status"], "delivered")

    def test_failed_delivery_uses_backoff_then_dead_letters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue_one(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)

            first = alert_store.claim_alerts(path, now=now)[0]
            self.assertTrue(alert_store.mark_alert_failed(
                path, first["id"], first["lease_token"], failed_at=now,
                error_code="HTTP_503", max_attempts=3, base_delay_seconds=30,
            ))
            self.assertEqual(alert_store.claim_alerts(
                path, now=now + timedelta(seconds=29)
            ), [])

            second = alert_store.claim_alerts(
                path, now=now + timedelta(seconds=30)
            )[0]
            self.assertEqual(second["attempts"], 2)
            self.assertTrue(alert_store.mark_alert_failed(
                path, second["id"], second["lease_token"],
                failed_at=now + timedelta(seconds=30), error_code="HTTP_503",
                max_attempts=3, base_delay_seconds=30,
            ))
            self.assertEqual(alert_store.claim_alerts(
                path, now=now + timedelta(seconds=89)
            ), [])

            third = alert_store.claim_alerts(
                path, now=now + timedelta(seconds=90)
            )[0]
            self.assertEqual(third["attempts"], 3)
            self.assertTrue(alert_store.mark_alert_failed(
                path, third["id"], third["lease_token"],
                failed_at=now + timedelta(seconds=90), error_code="HTTP_503",
                max_attempts=3, base_delay_seconds=30,
            ))

            row = alert_store.load_recent_alerts(path).iloc[0]
            self.assertEqual(row["delivery_status"], "failed")
            self.assertEqual(row["last_error"], "HTTP_503")
            self.assertEqual(alert_store.claim_alerts(
                path, now=now + timedelta(days=1)
            ), [])

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
