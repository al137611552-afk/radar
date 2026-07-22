import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_health_cli  # noqa: E402


class AlertHealthCliTests(unittest.TestCase):
    def test_main_prints_shared_health_snapshot(self):
        fixed_now = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
        health = {
            "total": 4,
            "ready": 1,
            "retry_waiting": 1,
            "active_leases": 1,
            "stale_leases": 0,
            "failed": 1,
            "delivered": 0,
            "oldest_undelivered_at": "2026-07-22T03:00:00+00:00",
            "oldest_undelivered_age_seconds": 3600,
            "last_delivered_at": None,
        }
        with patch.object(
            alert_health_cli, "load_alert_delivery_health", return_value=health
        ) as loader, patch.object(
            alert_health_cli, "_utc_now", return_value=fixed_now
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = alert_health_cli.main(["--db", "/tmp/alerts.db"])

        self.assertEqual(code, 0)
        loader.assert_called_once_with(Path("/tmp/alerts.db"), now=fixed_now)
        self.assertEqual(json.loads(stdout.getvalue()), health)

    def test_default_database_is_runtime_alert_outbox(self):
        args = alert_health_cli.parse_args([])

        self.assertEqual(args.db, Path("output/alerts/alerts.db"))

    def test_runtime_failure_is_sanitized(self):
        with patch.object(
            alert_health_cli,
            "load_alert_delivery_health",
            side_effect=FileNotFoundError("/private/operator/alerts.db"),
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout, patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            code = alert_health_cli.main([
                "--db", "/private/operator/alerts.db"
            ])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {
            "error": "ALERT_HEALTH_UNAVAILABLE"
        })
        self.assertNotIn("/private", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
