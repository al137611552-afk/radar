import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import dashboard_cli  # noqa: E402


class DashboardDataTests(unittest.TestCase):
    def test_snapshot_combines_market_files_and_scheduler_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "intraday_latest.csv").write_text(
                "code,name,bar_time,rank_15m,turnover_15m_yi,price_change_15m_pct,side\n"
                "rb2610,螺纹钢2610,2026-07-15 14:55:00,1,12.5,0.8,多\n",
                encoding="utf-8",
            )
            (output / "options_latest.csv").write_text(
                "code,name,bar_time,option_type,dte,signal_score,recent_volume\n"
                "rb2610C3200,螺纹钢购3200,2026-07-15 14:00:00,CALL,12,4,2000\n",
                encoding="utf-8",
            )
            (output / "momentum_latest.csv").write_text(
                "code,name,as_of,momentum_score,return_5d,return_20d\n"
                "rb6666,螺纹钢指数,2026-07-14,88.5,3.2,7.1\n",
                encoding="utf-8",
            )
            scheduler_db = output / "scheduler" / "runs.db"
            scheduler_db.parent.mkdir()
            with sqlite3.connect(scheduler_db) as connection:
                connection.execute(
                    """CREATE TABLE task_runs (
                        id INTEGER PRIMARY KEY, task TEXT, slot TEXT,
                        attempt INTEGER, status TEXT, started_at TEXT,
                        finished_at TEXT, error TEXT
                    )"""
                )
                connection.execute(
                    "INSERT INTO task_runs VALUES (1, ?, ?, 1, ?, ?, ?, ?)",
                    (
                        "intraday",
                        "2026-07-15T14:55:00+08:00",
                        "success",
                        "2026-07-15T14:55:01+08:00",
                        "2026-07-15T14:55:03+08:00",
                        "request failed api_key=secret-123 Authorization: Bearer token-abc",
                    ),
                )

            payload = dashboard.build_dashboard_payload(
                root,
                now=datetime.fromisoformat("2026-07-15T15:00:00+08:00"),
            )

            self.assertEqual(payload["summary"]["intraday_count"], 1)
            self.assertEqual(payload["summary"]["option_count"], 1)
            self.assertEqual(payload["summary"]["momentum_count"], 1)
            self.assertEqual(payload["intraday"][0]["turnover_15m_yi"], 12.5)
            self.assertEqual(payload["options"][0]["dte"], 12)
            self.assertEqual(payload["momentum"][0]["momentum_score"], 88.5)
            self.assertEqual(payload["tasks"][0]["status"], "success")
            serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            self.assertNotIn("secret-123", serialized)
            self.assertNotIn("token-abc", serialized)
            self.assertIn("[REDACTED]", payload["tasks"][0]["error"])

    def test_redacts_common_credential_shapes(self):
        basic_value = "BASIC_" + "TEST_VALUE"
        cases = {
            "access_token=access-token-value": ("access-token-value",),
            "refresh_token:refresh-token-value": ("refresh-token-value",),
            "client_secret=client-secret-value": ("client-secret-value",),
            "AWS_SECRET_ACCESS_KEY=aws-secret-value": ("aws-secret-value",),
            f"Authorization: Basic {basic_value}": (basic_value,),
            "{'Authorization': 'Bearer bearer-map-value'}": ("bearer-map-value",),
            "api key=space-key-value": ("space-key-value",),
            "{'password': 'quoted password value'}": ("quoted password value",),
            "password=\"abc'def ghi\"": ("abc", "def ghi"),
            "password='abc\"def ghi'": ("abc", "def ghi"),
            "password=\"abc\ndef ghi\"": ("abc", "def ghi"),
            "password=\"abc def ghi": ("abc", "def ghi"),
            r'{"password":"abc\"def ghi"}': ("abc", "def ghi"),
            "https://alice:url-password@example.com/path": ("url-password",),
            "https://example.com/path?api_key=query-secret": ("query-secret",),
        }

        for error, fragments in cases.items():
            with self.subTest(error=error):
                for fragment in fragments:
                    self.assertIn(fragment, error)
                redacted = dashboard._redact_error(error)
                for fragment in fragments:
                    self.assertNotIn(fragment, redacted)
                self.assertIn("[REDACTED]", redacted)

        self.assertEqual(
            dashboard._redact_error("ordinary internal failure detail"),
            "任务失败详情已隐藏 [REDACTED]",
        )

    def test_corrupt_csv_does_not_hide_other_datasets(self):
        valid_rows = b"".join(
            f"rb{index},name{index}\n".encode() for index in range(200)
        )
        corrupt_cases = {
            "invalid encoding": b"code,name\nrb2610,\xff\n",
            "extra columns": b"code,name\nrb2610,name,unexpected\n",
            "extra columns after display limit": (
                b"code,name\n" + valid_rows + b"tail,name,unexpected\n"
            ),
            "unclosed quote after display limit": (
                b"code,name\n" + valid_rows + b'tail,"unterminated\n'
            ),
            "missing column after display limit": (
                b"code,name\n" + valid_rows + b"tail\n"
            ),
        }
        for label, content in corrupt_cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "output"
                output.mkdir()
                (output / "intraday_latest.csv").write_bytes(content)
                (output / "options_latest.csv").write_text(
                    "code,name,dte\nrbC,option,5\n", encoding="utf-8"
                )

                payload = dashboard.build_dashboard_payload(root)

                self.assertEqual(payload["intraday"], [])
                self.assertEqual(payload["summary"]["option_count"], 1)
                self.assertEqual(payload["summary"]["health"], "degraded")
                self.assertFalse(payload["files"]["intraday"]["available"])
                self.assertEqual(payload["files"]["options"]["available"], True)

    def test_file_status_tolerates_file_disappearing_before_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output/intraday_latest.csv"
            target.parent.mkdir()
            target.write_text("code\nrb2610\n", encoding="utf-8")
            original_stat = target.stat()
            with mock.patch.object(
                Path, "stat", side_effect=[original_stat, OSError("disappeared")]
            ):
                status = dashboard._file_status(
                    root,
                    Path("output/intraday_latest.csv"),
                    1,
                    datetime.fromisoformat("2026-07-15T15:00:00+08:00"),
                )

            self.assertFalse(status["available"])
            self.assertIsNotNone(status["error"])

    def test_snapshot_handles_missing_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = dashboard.build_dashboard_payload(Path(directory))

            self.assertEqual(payload["intraday"], [])
            self.assertEqual(payload["options"], [])
            self.assertEqual(payload["momentum"], [])
            self.assertEqual(payload["tasks"], [])
            self.assertEqual(payload["summary"]["health"], "waiting")


class DashboardAssetTests(unittest.TestCase):
    def test_project_dashboard_assets_include_core_panels(self):
        index = (ROOT / "web/index.html").read_text(encoding="utf-8")
        script = (ROOT / "web/assets/dashboard.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "web/assets/dashboard.css").read_text(encoding="utf-8")

        for panel in ("overview", "intraday", "options", "momentum", "tasks"):
            self.assertIn(f'data-panel="{panel}"', index)
        self.assertIn('id="global-search"', index)
        self.assertIn('id="refresh-button"', index)
        self.assertIn("/api/dashboard", script)
        self.assertIn("setInterval", script)
        self.assertIn("@media", stylesheet)


class DashboardHttpTests(unittest.TestCase):
    def test_server_exposes_dashboard_api_and_static_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text(
                "<!doctype html><title>Watchman</title>", encoding="utf-8"
            )
            server = dashboard_cli.create_server(
                "127.0.0.1", 0, project_root=root, web_root=web
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/api/dashboard") as response:
                    payload = json.load(response)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(payload["summary"]["health"], "waiting")
                with urllib.request.urlopen(base + "/") as response:
                    self.assertIn("Watchman", response.read().decode())
                    self.assertEqual(response.headers.get_content_type(), "text/html")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
