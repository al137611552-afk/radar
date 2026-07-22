import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_requeue_cli  # noqa: E402


class AlertRequeueCliTests(unittest.TestCase):
    def test_requires_confirmation_and_exactly_one_selector(self):
        invalid_argv = (
            ["--confirm"],
            ["--all"],
            ["--id", "1"],
            ["--all", "--id", "1", "--confirm"],
        )
        for argv in invalid_argv:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                alert_requeue_cli.parse_args(argv)

    def test_requeues_explicit_ids_and_prints_bounded_json_summary(self):
        fixed_now = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
        with patch.object(
            alert_requeue_cli, "requeue_failed_alerts", return_value=[7]
        ) as replay, patch.object(
            alert_requeue_cli, "_utc_now", return_value=fixed_now
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = alert_requeue_cli.main([
                "--db", "/tmp/alerts.db",
                "--id", "7",
                "--id", "8",
                "--limit", "10",
                "--confirm",
            ])

        self.assertEqual(code, 0)
        replay.assert_called_once_with(
            Path("/tmp/alerts.db"), now=fixed_now, event_ids=[7, 8], limit=10
        )
        self.assertEqual(json.loads(stdout.getvalue()), {
            "event_ids": [7],
            "requeued": 1,
            "selector": "ids",
        })

    def test_all_selector_is_bounded_and_passes_no_ids(self):
        fixed_now = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)
        with patch.object(
            alert_requeue_cli, "requeue_failed_alerts", return_value=[]
        ) as replay, patch.object(
            alert_requeue_cli, "_utc_now", return_value=fixed_now
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = alert_requeue_cli.main([
                "--db", "/tmp/alerts.db",
                "--all",
                "--limit", "25",
                "--confirm",
            ])

        self.assertEqual(code, 0)
        replay.assert_called_once_with(
            Path("/tmp/alerts.db"), now=fixed_now, event_ids=None, limit=25
        )
        self.assertEqual(json.loads(stdout.getvalue())["selector"], "all")

    def test_rejects_nonpositive_or_excessive_limit_before_store_call(self):
        for value in ("0", "1001"):
            with self.subTest(limit=value), self.assertRaises(SystemExit):
                alert_requeue_cli.parse_args([
                    "--all", "--limit", value, "--confirm"
                ])

    def test_rejects_event_id_above_sqlite_integer_range(self):
        with self.assertRaises(SystemExit):
            alert_requeue_cli.parse_args([
                "--id", str(2**63), "--confirm"
            ])

    def test_runtime_failure_returns_sanitized_nonzero_error(self):
        with patch.object(
            alert_requeue_cli,
            "requeue_failed_alerts",
            side_effect=FileNotFoundError("/private/operator/alerts.db"),
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout, patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            code = alert_requeue_cli.main([
                "--db", "/private/operator/alerts.db",
                "--all",
                "--confirm",
            ])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {
            "error": "REQUEUE_FAILED"
        })
        self.assertNotIn("/private", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
