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

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import dashboard_cli  # noqa: E402
from alert_store import enqueue_option_alerts  # noqa: E402
from momentum_history_store import save_momentum_snapshot  # noqa: E402
from option_history_store import save_option_snapshot  # noqa: E402


class DashboardDataTests(unittest.TestCase):
    def test_dataframe_records_normalizes_all_non_finite_floats(self):
        frame = pd.DataFrame({
            "value": [float("nan"), float("inf"), float("-inf"), 1.5]
        })

        records = dashboard._dataframe_records(frame)

        self.assertEqual(records, [
            {"value": None}, {"value": None}, {"value": None}, {"value": 1.5}
        ])
        json.dumps(records, allow_nan=False)

    def test_snapshot_exposes_recent_durable_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alerts = pd.DataFrame([{
                "code": "au2608C880", "name": "黄金购880",
                "underlying": "au2608", "option_type": "CALL", "dte": 6,
                "confirmation_score": 6, "alert_type": "首次命中",
            }])
            enqueue_option_alerts(
                root / "output/alerts/alerts.db",
                alerts,
                "2026-07-21T14:00:00+08:00",
            )

            payload = dashboard.build_dashboard_payload(root)

            self.assertEqual(payload["summary"]["alert_count"], 1)
            self.assertEqual(payload["summary"]["pending_alert_count"], 1)
            self.assertEqual(payload["alerts"][0]["entity_code"], "au2608C880")
            self.assertEqual(payload["alerts"][0]["severity"], "info")
            self.assertTrue(payload["files"]["alerts"]["available"])
            json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_alert_summary_counts_full_outbox_beyond_recent_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output/alerts/alerts.db"
            alerts = pd.DataFrame([
                {"code": f"C{index:03d}", "alert_type": "首次命中"}
                for index in range(103)
            ])
            enqueue_option_alerts(
                path, alerts, "2026-07-21T14:00:00+08:00"
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE alert_events SET delivery_status = 'delivered' WHERE id = 1"
                )
                connection.execute(
                    "UPDATE alert_events SET delivery_status = 'failed' WHERE id = 2"
                )

            payload = dashboard.build_dashboard_payload(root)

            self.assertEqual(len(payload["alerts"]), 100)
            self.assertEqual(payload["summary"]["alert_count"], 103)
            self.assertEqual(payload["summary"]["pending_alert_count"], 101)
            self.assertEqual(payload["summary"]["delivered_alert_count"], 1)
            self.assertEqual(payload["summary"]["failed_alert_count"], 1)

    def test_dashboard_omits_internal_alert_error_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alert_path = root / "output/alerts/alerts.db"
            enqueue_option_alerts(
                alert_path,
                pd.DataFrame([{"code": "auC1", "alert_type": "首次命中"}]),
                "2026-07-21T14:00:00+08:00",
            )
            with sqlite3.connect(alert_path) as connection:
                connection.execute(
                    "UPDATE alert_events SET last_error = ? WHERE id = 1",
                    ("private diagnostic marker",),
                )

            payload = dashboard.build_dashboard_payload(root)
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)

            self.assertEqual(len(payload["alerts"]), 1)
            self.assertNotIn("last_error", payload["alerts"][0])
            self.assertNotIn("payload_json", payload["alerts"][0])
            self.assertNotIn("private diagnostic marker", encoded)

    def test_blob_in_alert_text_degrades_only_alert_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "options_latest.csv").write_text(
                "code,name,dte\nauC1,黄金购,5\n", encoding="utf-8"
            )
            alert_path = output / "alerts/alerts.db"
            enqueue_option_alerts(
                alert_path,
                pd.DataFrame([{"code": "auC1", "alert_type": "首次命中"}]),
                "2026-07-21T14:00:00+08:00",
            )
            with sqlite3.connect(alert_path) as connection:
                connection.execute(
                    "UPDATE alert_events SET message = ? WHERE id = 1",
                    (sqlite3.Binary(b"\xff\xfe"),),
                )

            payload = dashboard.build_dashboard_payload(root)

            self.assertEqual(payload["alerts"], [])
            self.assertEqual(payload["summary"]["option_count"], 1)
            self.assertEqual(payload["summary"]["health"], "degraded")
            self.assertFalse(payload["files"]["alerts"]["available"])
            self.assertIsNotNone(payload["files"]["alerts"]["error"])
            json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_corrupt_alert_database_degrades_only_alert_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "options_latest.csv").write_text(
                "code,name,dte\nauC1,黄金购,5\n", encoding="utf-8"
            )
            alert_path = output / "alerts/alerts.db"
            alert_path.parent.mkdir()
            alert_path.write_bytes(b"not a sqlite database")

            payload = dashboard.build_dashboard_payload(root)

            self.assertEqual(payload["alerts"], [])
            self.assertEqual(payload["summary"]["option_count"], 1)
            self.assertEqual(payload["summary"]["health"], "degraded")
            self.assertFalse(payload["files"]["alerts"]["available"])
            self.assertIsNotNone(payload["files"]["alerts"]["error"])

    def test_snapshot_exposes_hourly_option_signal_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output/history/options.db"
            base = {
                "code": "auC1", "name": "黄金购", "exchange": "SHFE",
                "bar_time": pd.Timestamp("2026-07-20 10:00"),
                "underlying": "au2608", "option_type": "CALL", "dte": 7,
                "expiry": "2026-07-27", "strike": 800.0, "last_price": 10.0,
                "moneyness": 0.01, "recent_volume": 1000.0, "open_interest": 500.0,
                "signal_score": 3, "confirmation_score": 4,
                "ma_bullish": True, "macd_bullish": False,
                "double_confirmed": False, "ma_direction_confirmed": False,
                "macd_direction_confirmed": False, "ma_cross_time": None,
                "macd_cross_time": None,
            }
            first = pd.DataFrame([base])
            latest = pd.DataFrame([{**base, "double_confirmed": True,
                                    "ma_direction_confirmed": True}])
            save_option_snapshot(path, first, scan_time="2026-07-20T10:05:00+08:00")
            save_option_snapshot(path, latest, scan_time="2026-07-20T11:05:00+08:00")

            payload = dashboard.build_dashboard_payload(root)

            self.assertEqual(payload["summary"]["option_history_count"], 1)
            self.assertEqual(payload["option_history"][0]["change_status"], "新晋双确认")
            json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_snapshot_exposes_daily_momentum_rank_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output/history/momentum.db"
            base = {
                "name": "黄金指数", "exchange": "SHFE", "sector": "贵金属",
                "momentum_score": 90.0, "short_rank": 2,
                "risk_adjusted_score": 88.0, "risk_short_rank": 2,
                "volatility_score": 60.0, "volatility_risk": "常态",
            }
            first = pd.DataFrame([
                {**base, "code": "au6666", "as_of": "2026-07-18",
                 "long_rank": 2, "risk_long_rank": 2},
            ])
            latest = pd.DataFrame([
                {**base, "code": "au6666", "as_of": "2026-07-20",
                 "long_rank": 1, "risk_long_rank": 1},
            ])
            save_momentum_snapshot(path, first)
            save_momentum_snapshot(path, latest)

            payload = dashboard.build_dashboard_payload(root)

            self.assertEqual(payload["summary"]["momentum_history_count"], 1)
            self.assertEqual(payload["momentum_history"][0]["code"], "au6666")
            self.assertEqual(payload["momentum_history"][0]["long_rank_change"], 1)
            json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_product_detail_combines_current_market_data_and_rank_trajectory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "momentum_latest.csv").write_text(
                "code,name,exchange,sector,as_of,momentum_score,risk_adjusted_score,"
                "long_rank,risk_long_rank,return_20d,volatility_risk\n"
                "au6666,黄金指数,SHFE,贵金属,2026-07-20,90,88,1,2,6.5,常态\n"
                "rb6666,螺纹钢指数,SHFE,黑色,2026-07-20,70,65,8,10,2.1,偏高\n",
                encoding="utf-8",
            )
            (output / "intraday_latest.csv").write_text(
                "code,name,exchange,rank_15m,side,turnover_15m_yi,bar_time\n"
                "au2608,黄金2608,SHFE,3,多,12.5,2026-07-21T14:55:00+08:00\n"
                "rb2610,螺纹钢2610,SHFE,1,空,18.0,2026-07-21T14:55:00+08:00\n",
                encoding="utf-8",
            )
            (output / "options_latest.csv").write_text(
                "code,name,exchange,underlying,option_type,dte,confirmation_score\n"
                "au2608C880,黄金购880,SHFE,au2608,CALL,6,6\n"
                "rb2610P3000,螺纹钢沽3000,SHFE,rb2610,PUT,8,4\n",
                encoding="utf-8",
            )
            history_path = output / "history" / "momentum.db"
            base = {
                "code": "au6666", "name": "黄金指数", "exchange": "SHFE",
                "sector": "贵金属", "short_rank": 80, "risk_short_rank": 79,
                "momentum_score": 90.0, "risk_adjusted_score": 88.0,
                "volatility_score": 50.0, "volatility_risk": "常态",
            }
            save_momentum_snapshot(history_path, pd.DataFrame([
                {**base, "as_of": "2026-07-18", "long_rank": 3,
                 "risk_long_rank": 4},
            ]))
            save_momentum_snapshot(history_path, pd.DataFrame([
                {**base, "as_of": "2026-07-20", "long_rank": 1,
                 "risk_long_rank": 2},
            ]))

            detail = dashboard.build_product_detail(root, "au6666")

            self.assertEqual(detail["code"], "au6666")
            self.assertEqual(detail["current"]["name"], "黄金指数")
            self.assertEqual(
                [row["long_rank"] for row in detail["momentum_trajectory"]], [3, 1]
            )
            self.assertEqual([row["code"] for row in detail["intraday"]], ["au2608"])
            self.assertEqual([row["code"] for row in detail["options"]], ["au2608C880"])
            uppercase = dashboard.build_product_detail(root, "AU6666")
            self.assertEqual(uppercase["code"], "au6666")
            self.assertEqual(len(uppercase["momentum_trajectory"]), 2)
            (root / "output/momentum_latest.csv").unlink()
            history_only = dashboard.build_product_detail(root, "AU6666")
            self.assertEqual(history_only["code"], "au6666")
            self.assertIsNone(history_only["current"])
            self.assertEqual(len(history_only["momentum_trajectory"]), 2)
            json.dumps(detail, ensure_ascii=False, allow_nan=False)

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
                "code,name,as_of,momentum_score,return_5d,return_20d,"
                "risk_adjusted_score,risk_long_rank,annualized_volatility_20d,"
                "volatility_risk\n"
                "rb6666,螺纹钢指数,2026-07-14,88.5,3.2,7.1,91.2,1,18.4,偏高\n",
                encoding="utf-8",
            )
            (output / "sector_momentum_latest.csv").write_text(
                "sector,constituents,as_of,sector_return_5d,sector_momentum_score,"
                "sector_risk_adjusted_score,sector_risk_long_rank,"
                "sector_mean_annualized_volatility_20d,sector_volatility_risk\n"
                "黑色,10,2026-07-14,2.4,90.9,92.1,1,16.8,常态\n",
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
            self.assertEqual(payload["summary"]["sector_count"], 1)
            self.assertEqual(payload["intraday"][0]["turnover_15m_yi"], 12.5)
            self.assertEqual(payload["options"][0]["dte"], 12)
            self.assertEqual(payload["momentum"][0]["momentum_score"], 88.5)
            self.assertEqual(payload["momentum"][0]["risk_adjusted_score"], 91.2)
            self.assertEqual(payload["momentum"][0]["volatility_risk"], "偏高")
            self.assertEqual(payload["sectors"][0]["sector"], "黑色")
            self.assertEqual(
                payload["sectors"][0]["sector_risk_adjusted_score"], 92.1
            )
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
            self.assertEqual(payload["sectors"], [])
            self.assertEqual(payload["tasks"], [])
            self.assertEqual(payload["summary"]["health"], "waiting")


class DashboardAssetTests(unittest.TestCase):
    def test_project_dashboard_assets_include_core_panels(self):
        index = (ROOT / "web/index.html").read_text(encoding="utf-8")
        script = (ROOT / "web/assets/dashboard.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "web/assets/dashboard.css").read_text(encoding="utf-8")

        for panel in (
            "overview", "intraday", "options", "option-history", "momentum",
            "sectors", "history", "product", "alerts", "tasks",
        ):
            self.assertIn(f'data-panel="{panel}"', index)
        self.assertIn('id="product-select"', index)
        self.assertIn('id="product-rank-chart"', index)
        self.assertIn('id="product-intraday-table"', index)
        self.assertIn('id="product-options-table"', index)
        self.assertIn("function renderProductDetail()", script)
        self.assertIn("function loadProductDetail", script)
        self.assertIn("/api/product?code=", script)
        self.assertIn("createElementNS", script)
        self.assertIn('class="product-layout"', index)
        self.assertIn('id="option-history-table"', index)
        self.assertIn('id="alerts-table"', index)
        self.assertIn("function renderAlerts()", script)
        self.assertIn("delivery_status", script)
        self.assertIn("pending_alert_count", script)
        self.assertIn("delivered_alert_count", script)
        self.assertIn("failed_alert_count", script)
        self.assertIn("function renderOptionHistory()", script)
        self.assertIn("change_status", script)
        self.assertIn('id="momentum-history-table"', index)
        self.assertIn("function renderMomentumHistory()", script)
        self.assertIn("long_rank_change", script)
        self.assertIn('id="sectors-table"', index)
        self.assertIn('id="momentum-long-table"', index)
        self.assertIn('id="momentum-short-table"', index)
        self.assertIn('id="momentum-risk-long-table"', index)
        self.assertIn('id="momentum-risk-short-table"', index)
        self.assertIn('id="sectors-long-table"', index)
        self.assertIn('id="sectors-short-table"', index)
        self.assertIn('id="sectors-risk-long-table"', index)
        self.assertIn('id="sectors-risk-short-table"', index)
        self.assertIn("function renderSectors()", script)
        self.assertIn("sector_momentum_score", script)
        self.assertIn("long_rank", script)
        self.assertIn("short_rank", script)
        self.assertIn("期权分 / 确认分", index)
        self.assertIn("risk_adjusted_score", script)
        self.assertIn("risk_long_rank", script)
        self.assertIn("risk_short_rank", script)
        self.assertIn("annualized_volatility_20d", script)
        self.assertIn("volatility_risk", script)
        self.assertIn('input === null || input === undefined || input === ""', script)
        self.assertIn("风险调整多头强势榜", index)
        self.assertIn("风险调整空头弱势榜", index)
        self.assertIn("原始动量多头强势榜", index)
        self.assertIn("原始动量空头弱势榜", index)
        self.assertIn('directionalRows(rows, "long_rank")', script)
        self.assertIn('directionalRows(rows, "short_rank")', script)
        self.assertIn('directionalRows(rows, "risk_long_rank")', script)
        self.assertIn('directionalRows(rows, "risk_short_rank")', script)
        self.assertIn('/assets/dashboard.css?v=20260721-11', index)
        self.assertIn('/assets/dashboard.js?v=20260721-11', index)
        self.assertIn('<link rel="icon" href="data:,">', index)
        self.assertIn(".table-card>.card-head{padding:", stylesheet)
        self.assertIn(".table-card+.table-card{margin-top:", stylesheet)
        self.assertIn(".risk-badge.high{", stylesheet)
        self.assertIn(".risk-badge.elevated{", stylesheet)
        self.assertIn(".risk-badge.normal{", stylesheet)
        self.assertIn(".risk-badge.unknown{", stylesheet)
        self.assertIn(
            'type === "momentum" ? directionalRows(rows, "risk_long_rank") : rows',
            script,
        )
        self.assertIn('risk === "常态" ? "normal" : "unknown"', script)
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

    def test_server_exposes_validated_product_detail_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "momentum_latest.csv").write_text(
                "code,name\nau6666,黄金指数\n", encoding="utf-8"
            )
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text("Watchman", encoding="utf-8")
            server = dashboard_cli.create_server(
                "127.0.0.1", 0, project_root=root, web_root=web
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/api/product?code=au6666") as response:
                    payload = json.load(response)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(payload["current"]["name"], "黄金指数")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(base + "/api/product?code=../../etc/passwd")
                self.assertEqual(raised.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_product_data_value_error_is_server_error_not_bad_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text("Watchman", encoding="utf-8")
            server = dashboard_cli.create_server(
                "127.0.0.1", 0, project_root=root, web_root=web
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with mock.patch.object(
                    dashboard_cli,
                    "build_product_detail",
                    side_effect=ValueError("corrupt history date"),
                ):
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(base + "/api/product?code=au6666")
                    self.assertEqual(raised.exception.code, 500)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
