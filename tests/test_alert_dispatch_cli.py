import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_dispatch_cli  # noqa: E402


class AlertDispatchCliTests(unittest.TestCase):
    def test_main_uses_environment_endpoint_and_prints_machine_summary(self):
        output = StringIO()
        stats = {"claimed": 2, "delivered": 1, "retrying": 1, "dead_lettered": 0}
        environment = {
            "WATCHMAN_ALERT_WEBHOOK_URL": "https://example.invalid/watchman",
            "WATCHMAN_ALERT_WEBHOOK_TOKEN": "local-test-token",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            alert_dispatch_cli, "dispatch_once", return_value=stats
        ) as dispatch, redirect_stdout(output):
            code = alert_dispatch_cli.main(["--db", "/tmp/alerts.db"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), stats)
        self.assertEqual(
            dispatch.call_args.args[:2],
            (Path("/tmp/alerts.db"), environment["WATCHMAN_ALERT_WEBHOOK_URL"]),
        )
        self.assertEqual(
            dispatch.call_args.kwargs["authorization"],
            environment["WATCHMAN_ALERT_WEBHOOK_TOKEN"],
        )

    def test_main_treats_dead_letter_transition_as_a_handled_delivery_result(self):
        stats = {"claimed": 1, "delivered": 0, "retrying": 0, "dead_lettered": 1}
        with patch.dict(
            os.environ,
            {"WATCHMAN_ALERT_WEBHOOK_URL": "https://example.invalid/watchman"},
            clear=True,
        ), patch.object(alert_dispatch_cli, "dispatch_once", return_value=stats), \
             redirect_stdout(StringIO()):
            code = alert_dispatch_cli.main([])

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
