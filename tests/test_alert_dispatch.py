import json
import math
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_dispatch  # noqa: E402
import alert_store  # noqa: E402


class _WebhookHandler(BaseHTTPRequestHandler):
    status = 204
    location = ""
    requests = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body),
        })
        self.send_response(type(self).status)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.end_headers()

    def log_message(self, *_args):
        return


class AlertDispatchTests(unittest.TestCase):
    def test_webhook_opener_explicitly_disables_environment_proxies(self):
        self.assertEqual(alert_dispatch._WEBHOOK_PROXY_HANDLER.proxies, {})

    def setUp(self):
        _WebhookHandler.status = 204
        _WebhookHandler.location = ""
        _WebhookHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _WebhookHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/watchman"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _enqueue(self, path):
        alert_store.enqueue_option_alerts(
            path,
            pd.DataFrame([{
                "code": "auC1", "name": "黄金购", "underlying": "au2608",
                "alert_type": "首次命中",
            }]),
            "2026-07-21T14:00:00+08:00",
        )

    def test_idempotency_key_uses_business_identity_not_database_row_id(self):
        event = {
            "id": 1, "source": "option",
            "logical_slot": "2026-07-22T11:00:00+08:00",
            "entity_code": "auC1", "alert_type": "首次命中",
        }
        same_event = dict(event, id=999)
        later_event = dict(event, logical_slot="2026-07-22T12:00:00+08:00")

        self.assertEqual(
            alert_dispatch.idempotency_key(event),
            alert_dispatch.idempotency_key(same_event),
        )
        self.assertNotEqual(
            alert_dispatch.idempotency_key(event),
            alert_dispatch.idempotency_key(later_event),
        )

    def test_dispatch_once_posts_public_payload_and_marks_delivered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)

            stats = alert_dispatch.dispatch_once(
                path, self.url, authorization="test-bearer",
                now=datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(stats, {
                "claimed": 1, "delivered": 1, "retrying": 0, "dead_lettered": 0,
                "lease_lost": 0,
            })
            row = alert_store.load_recent_alerts(path).iloc[0]
            self.assertEqual(row["delivery_status"], "delivered")
            request = _WebhookHandler.requests[0]
            self.assertEqual(request["path"], "/watchman")
            self.assertEqual(request["headers"]["Authorization"], "Bearer test-bearer")
            key = request["headers"]["Idempotency-Key"]
            self.assertTrue(key.startswith("watchman-alert-"))
            self.assertEqual(len(key), len("watchman-alert-") + 64)
            self.assertEqual(request["body"]["entity_code"], "auC1")
            self.assertNotIn("lease_token", request["body"])
            self.assertNotIn("payload_json", request["body"])
            self.assertNotIn("last_error", request["body"])
            self.assertNotIn("attempts", request["body"])

    def test_retry_reuses_identical_idempotency_key_and_request_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
            _WebhookHandler.status = 503
            alert_dispatch.dispatch_once(
                path, self.url, now=now, base_delay_seconds=30,
            )
            _WebhookHandler.status = 204
            alert_dispatch.dispatch_once(
                path, self.url,
                now=datetime(2026, 7, 22, 2, 0, 30, tzinfo=timezone.utc),
                base_delay_seconds=30,
            )

            first, second = _WebhookHandler.requests
            self.assertEqual(
                first["headers"]["Idempotency-Key"],
                second["headers"]["Idempotency-Key"],
            )
            self.assertEqual(first["body"], second["body"])

    def test_dead_letter_replay_reuses_idempotency_key_and_request_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
            _WebhookHandler.status = 503
            alert_dispatch.dispatch_once(
                path, self.url, now=now, max_attempts=1,
            )
            event_id = int(alert_store.load_recent_alerts(path).iloc[0]["id"])
            alert_store.requeue_failed_alerts(
                path, now=now + timedelta(seconds=1), event_ids=[event_id]
            )
            _WebhookHandler.status = 204
            alert_dispatch.dispatch_once(
                path, self.url, now=now + timedelta(seconds=2),
            )

            first, second = _WebhookHandler.requests
            self.assertEqual(
                first["headers"]["Idempotency-Key"],
                second["headers"]["Idempotency-Key"],
            )
            self.assertEqual(first["body"], second["body"])

    def test_non_finite_timeout_fails_before_claiming_events(self):
        for timeout in (math.nan, math.inf, -math.inf):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "alerts.db"
                self._enqueue(path)
                with self.assertRaisesRegex(ValueError, "timeout"):
                    alert_dispatch.dispatch_once(path, self.url, timeout=timeout)
                row = alert_store.load_recent_alerts(path).iloc[0]
                self.assertEqual(row["attempts"], 0)

    def test_lost_failure_lease_is_reported_without_false_retry_or_dead_letter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)
            error = HTTPError(self.url, 503, "fixture", {}, None)
            with patch.object(
                alert_dispatch, "post_webhook", side_effect=error
            ), patch.object(
                alert_dispatch, "mark_alert_failed", return_value=False
            ):
                stats = alert_dispatch.dispatch_once(
                    path, self.url,
                    now=datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
                )

            self.assertEqual(stats["retrying"], 0)
            self.assertEqual(stats["dead_lettered"], 0)
            self.assertEqual(stats["lease_lost"], 1)

    def test_http_failure_is_requeued_with_backoff_without_leaking_url(self):
        _WebhookHandler.status = 503
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)
            now = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)

            first = alert_dispatch.dispatch_once(
                path, self.url, now=now, max_attempts=3, base_delay_seconds=30,
            )
            immediate = alert_dispatch.dispatch_once(
                path, self.url, now=now, max_attempts=3, base_delay_seconds=30,
            )

            self.assertEqual(first["retrying"], 1)
            self.assertEqual(immediate["claimed"], 0)
            row = alert_store.load_recent_alerts(path).iloc[0]
            self.assertEqual(row["delivery_status"], "pending")
            self.assertEqual(row["attempts"], 1)
            self.assertEqual(row["last_error"], "HTTP_503")
            self.assertNotIn(self.url, str(row["last_error"]))

    def test_invalid_authorization_fails_before_claiming_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)

            for value in (
                "invalid\nvalue", "invalid\x00value", "invalid\x7fvalue",
                "invalid\tvalue", "无法编码",
            ):
                with self.subTest(value=repr(value)), self.assertRaisesRegex(
                    ValueError, "authorization"
                ):
                    alert_dispatch.dispatch_once(
                        path, self.url, authorization=value,
                        now=datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
                    )

            row = alert_store.load_recent_alerts(path).iloc[0]
            self.assertEqual(row["attempts"], 0)
            self.assertEqual(row["delivery_status"], "pending")

    def test_plain_http_requires_a_literal_loopback_address(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            alert_dispatch.validate_webhook_url("http://localhost/hook")
        with self.assertRaisesRegex(ValueError, "loopback"):
            alert_dispatch.validate_webhook_url("http://example.com/hook")
        self.assertEqual(
            alert_dispatch.validate_webhook_url("http://127.0.0.2/hook"),
            "http://127.0.0.2/hook",
        )

    def test_redirect_is_not_followed_or_given_the_authorization_header(self):
        _WebhookHandler.status = 302
        _WebhookHandler.location = self.url + "/redirected"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.db"
            self._enqueue(path)

            stats = alert_dispatch.dispatch_once(
                path, self.url, authorization="local-test-token",
                now=datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(stats["retrying"], 1)
            self.assertEqual(len(_WebhookHandler.requests), 1)
            row = alert_store.load_recent_alerts(path).iloc[0]
            self.assertEqual(row["last_error"], "HTTP_302")

    def test_rejects_webhook_urls_with_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            alert_dispatch.validate_webhook_url(
                "https://" + "user:fixture@" + "example.com/hook"
            )


if __name__ == "__main__":
    unittest.main()
