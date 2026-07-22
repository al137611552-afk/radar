import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alert_store  # noqa: E402
import option_cli  # noqa: E402


class OptionCliTests(unittest.TestCase):
    def test_parse_args_supports_option_history_database(self):
        args = option_cli.parse_args(["--history-db", "output/history/custom-options.db"])

        self.assertEqual(args.history_db, Path("output/history/custom-options.db"))

    def test_builds_compact_chinese_signal_table(self):
        source = pd.DataFrame([{
            "code": "cu2608C106000", "name": "沪铜2608购106000",
            "dte": 13, "underlying": "cu2608", "option_type": "CALL",
            "strike": 106000, "moneyness": 0.015423,
            "bar_time": pd.Timestamp("2026-07-14 15:00"), "last_price": 120,
            "recent_volume": 1000, "open_interest": 500,
            "ma_bullish": True, "ma_cross_bars_ago": 0,
            "macd_bullish": True, "macd_cross_bars_ago": 2,
            "underlying_ma_bullish": True,
            "underlying_ma_cross_bars_ago": None,
            "underlying_macd_bullish": True,
            "underlying_macd_cross_bars_ago": 1,
            "double_confirmed": True, "confirmation_score": 8,
            "signal_score": 6,
        }])

        display = option_cli.build_display_table(source)

        self.assertEqual(display.columns.tolist(), [
            "代码", "DTE", "类型", "行权价", "虚实值%", "最新价", "小时线截止",
            "近20小时量", "持仓量", "期权MA", "期权MACD", "标的MA",
            "标的MACD", "双确认", "确认分",
        ])
        self.assertEqual(display.loc[0, "期权MA"], "刚金叉")
        self.assertEqual(display.loc[0, "期权MACD"], "2根前金叉")
        self.assertEqual(display.loc[0, "标的MACD"], "1根前金叉")
        self.assertEqual(display.loc[0, "双确认"], "是")
        self.assertEqual(display.loc[0, "虚实值%"], 1.54)

    def test_double_mode_keeps_only_direction_confirmed_options(self):
        source = pd.DataFrame({
            "code": ["recent-confirmed", "unconfirmed", "stale-confirmed"],
            "double_confirmed": [True, False, True],
            "ma_cross_bars_ago": [0, 0, None],
            "macd_cross_bars_ago": [None, None, None],
        })

        result = option_cli.filter_signal_mode(source, "double")

        self.assertEqual(result["code"].tolist(), ["recent-confirmed"])

    def test_incremental_filter_persists_and_suppresses_repeats(self):
        source = pd.DataFrame([{
            "code": "A", "ma_cross_time": pd.Timestamp("2026-07-14 10:00"),
            "macd_cross_time": None, "double_confirmed": True,
            "ma_direction_confirmed": True, "macd_direction_confirmed": False,
        }])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "options.json"

            first = option_cli.filter_incremental_signals(
                source, "double", state_path
            )
            repeated = option_cli.filter_incremental_signals(
                source, "double", state_path
            )

            self.assertEqual(first["alert_type"].tolist(), ["首次命中"])
            self.assertTrue(repeated.empty)
            self.assertTrue(state_path.exists())

    def test_display_includes_alert_type_for_incremental_rows(self):
        source = pd.DataFrame([{
            "code": "A", "dte": 5, "option_type": "CALL", "strike": 100,
            "moneyness": 0.01, "last_price": 2,
            "bar_time": pd.Timestamp("2026-07-14 15:00"),
            "recent_volume": 100, "open_interest": 200,
            "ma_bullish": True, "ma_cross_bars_ago": 0,
            "macd_bullish": False, "macd_cross_bars_ago": None,
            "underlying_ma_bullish": True,
            "underlying_ma_cross_bars_ago": None,
            "underlying_macd_bullish": False,
            "underlying_macd_cross_bars_ago": None,
            "double_confirmed": True, "confirmation_score": 4,
            "alert_type": "首次命中",
        }])

        display = option_cli.build_display_table(source)

        self.assertEqual(display.columns[0], "告警")
        self.assertEqual(display.loc[0, "告警"], "首次命中")

    def test_parse_args_supports_incremental_state_options(self):
        args = option_cli.parse_args([
            "--new-only", "--state-file", "output/state/custom.json",
            "--snapshot-csv", "output/options_candidates_latest.csv",
            "--filtered-csv", "output/options_latest.csv",
            "--alert-db", "output/alerts/custom.db",
        ])

        self.assertTrue(args.new_only)
        self.assertEqual(args.state_file, Path("output/state/custom.json"))
        self.assertEqual(
            args.snapshot_csv, Path("output/options_candidates_latest.csv")
        )
        self.assertEqual(args.filtered_csv, Path("output/options_latest.csv"))
        self.assertEqual(args.alert_db, Path("output/alerts/custom.db"))

    def test_main_new_only_suppresses_repeated_cli_output(self):
        source = pd.DataFrame([{
            "code": "A", "dte": 5, "option_type": "CALL", "strike": 100,
            "moneyness": 0.01, "last_price": 2,
            "bar_time": pd.Timestamp("2026-07-14 15:00"),
            "recent_volume": 100, "open_interest": 200,
            "ma_bullish": True, "ma_cross_bars_ago": 0,
            "ma_cross_time": pd.Timestamp("2026-07-14 15:00"),
            "macd_bullish": False, "macd_cross_bars_ago": None,
            "macd_cross_time": None,
            "underlying_ma_bullish": True,
            "underlying_ma_cross_bars_ago": None,
            "underlying_macd_bullish": False,
            "underlying_macd_cross_bars_ago": None,
            "double_confirmed": True, "ma_direction_confirmed": True,
            "macd_direction_confirmed": False, "confirmation_score": 4,
            "signal_score": 3,
        }, {
            "code": "B", "dte": 5, "option_type": "PUT", "strike": 100,
            "moneyness": 0.01, "last_price": 2,
            "bar_time": pd.Timestamp("2026-07-14 15:00"),
            "recent_volume": 100, "open_interest": 200,
            "ma_bullish": False, "ma_cross_bars_ago": None,
            "ma_cross_time": None,
            "macd_bullish": False, "macd_cross_bars_ago": None,
            "macd_cross_time": None,
            "underlying_ma_bullish": True,
            "underlying_ma_cross_bars_ago": None,
            "underlying_macd_bullish": True,
            "underlying_macd_cross_bars_ago": None,
            "double_confirmed": False, "ma_direction_confirmed": False,
            "macd_direction_confirmed": False, "confirmation_score": 0,
            "signal_score": 0,
        }])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            snapshot_path = Path(directory) / "options_candidates_latest.csv"
            filtered_path = Path(directory) / "options_latest.csv"
            argv = [
                "--new-only", "--no-history", "--state-file", str(state_path),
                "--snapshot-csv", str(snapshot_path),
                "--filtered-csv", str(filtered_path),
                "--alert-db", str(Path(directory) / "alerts.db"),
            ]
            first_output, second_output = StringIO(), StringIO()
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source):
                with redirect_stdout(first_output):
                    first_code = option_cli.main(argv)
                with redirect_stdout(second_output):
                    second_code = option_cli.main(argv)

            self.assertEqual((first_code, second_code), (0, 0))
            self.assertIn("首次命中", first_output.getvalue())
            self.assertIn("没有新增或变化", second_output.getvalue())
            snapshot = pd.read_csv(snapshot_path)
            filtered = pd.read_csv(filtered_path)
            self.assertEqual(snapshot["code"].tolist(), ["A", "B"])
            self.assertEqual(filtered["code"].tolist(), ["A"])
            self.assertNotIn(0, filtered["confirmation_score"].tolist())

    def test_outbox_failure_does_not_advance_incremental_state(self):
        source = pd.DataFrame([{
            "code": "A", "name": "A购", "underlying": "a2608",
            "option_type": "CALL", "dte": 5, "strike": 100,
            "moneyness": 0.01, "last_price": 2, "bar_time": pd.Timestamp(
                "2026-07-21 14:00"
            ),
            "recent_volume": 100, "open_interest": 200,
            "ma_bullish": True, "ma_cross_bars_ago": 0,
            "ma_cross_time": pd.Timestamp("2026-07-21 14:00"),
            "macd_bullish": False, "macd_cross_bars_ago": None,
            "macd_cross_time": None, "underlying_ma_bullish": True,
            "underlying_ma_cross_bars_ago": None,
            "underlying_macd_bullish": False,
            "underlying_macd_cross_bars_ago": None,
            "double_confirmed": True, "ma_direction_confirmed": True,
            "macd_direction_confirmed": False, "confirmation_score": 4,
            "signal_score": 3,
        }])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            alert_path = Path(directory) / "alerts.db"
            argv = [
                "--new-only", "--no-history", "--state-file", str(state_path),
                "--alert-db", str(alert_path),
            ]
            environment = {"WATCHMAN_LOGICAL_SLOT": "2026-07-21T14:00:00+08:00"}
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict(os.environ, environment, clear=True), \
                 patch.object(option_cli, "enqueue_option_alerts", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    option_cli.main(argv)

            self.assertFalse(state_path.exists())
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict(os.environ, environment, clear=True), redirect_stdout(StringIO()):
                code = option_cli.main(argv)

            self.assertEqual(code, 0)
            alerts = alert_store.load_recent_alerts(alert_path)
            self.assertEqual(alerts["entity_code"].tolist(), ["A"])
            self.assertEqual(alerts["logical_slot"].tolist(), [environment["WATCHMAN_LOGICAL_SLOT"]])

    def test_state_failure_after_outbox_commit_is_idempotently_recoverable(self):
        source = pd.DataFrame([{
            "code": "A", "name": "黄金购", "underlying": "au2608",
            "option_type": "CALL", "dte": 6, "confirmation_score": 4,
            "ma_cross_time": pd.Timestamp("2026-07-21 13:00"),
            "macd_cross_time": None, "double_confirmed": True,
            "ma_direction_confirmed": True,
            "macd_direction_confirmed": False,
            "ma_cross_bars_ago": 0, "macd_cross_bars_ago": None,
        }])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            alert_path = Path(directory) / "alerts.db"
            argv = [
                "--new-only", "--no-history", "--state-file", str(state_path),
                "--alert-db", str(alert_path),
            ]
            environment = {"WATCHMAN_LOGICAL_SLOT": "2026-07-21T14:00:00+08:00"}
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict(os.environ, environment, clear=True), \
                 patch.object(option_cli, "build_display_table", return_value=pd.DataFrame()), \
                 patch.object(option_cli, "save_state", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    option_cli.main(argv)

            self.assertFalse(state_path.exists())
            self.assertEqual(len(alert_store.load_recent_alerts(alert_path)), 1)
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict(os.environ, environment, clear=True), \
                 patch.object(option_cli, "build_display_table", return_value=pd.DataFrame()), \
                 redirect_stdout(StringIO()):
                code = option_cli.main(argv)

            self.assertEqual(code, 0)
            self.assertTrue(state_path.exists())
            self.assertEqual(len(alert_store.load_recent_alerts(alert_path)), 1)

    def test_manual_state_retry_across_hour_uses_market_bar_for_outbox_identity(self):
        source = pd.DataFrame([{
            "code": "A", "name": "黄金购", "underlying": "au2608",
            "option_type": "CALL", "dte": 6, "confirmation_score": 4,
            "bar_time": pd.Timestamp("2026-07-21 13:00"),
            "ma_cross_time": pd.Timestamp("2026-07-21 13:00"),
            "macd_cross_time": None, "double_confirmed": True,
            "ma_direction_confirmed": True,
            "macd_direction_confirmed": False,
            "ma_cross_bars_ago": 0, "macd_cross_bars_ago": None,
        }])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            alert_path = Path(directory) / "alerts.db"
            argv = [
                "--new-only", "--no-history", "--state-file", str(state_path),
                "--alert-db", str(alert_path),
            ]
            first_now = datetime.fromisoformat("2026-07-21T14:59:00+08:00")
            retry_now = datetime.fromisoformat("2026-07-21T15:01:00+08:00")
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict(os.environ, {}, clear=True), \
                 patch.object(option_cli, "datetime", create=True) as clock, \
                 patch.object(option_cli, "build_display_table", return_value=pd.DataFrame()), \
                 patch.object(option_cli, "save_state", side_effect=OSError("disk full")):
                clock.now.return_value = first_now
                with self.assertRaisesRegex(OSError, "disk full"):
                    option_cli.main(argv)

            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict(os.environ, {}, clear=True), \
                 patch.object(option_cli, "datetime", create=True) as clock, \
                 patch.object(option_cli, "build_display_table", return_value=pd.DataFrame()), \
                 redirect_stdout(StringIO()):
                clock.now.return_value = retry_now
                code = option_cli.main(argv)

            alerts = alert_store.load_recent_alerts(alert_path)
            self.assertEqual(code, 0)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(
                alerts["logical_slot"].tolist(), ["2026-07-21T13:00:00+08:00"]
            )

    def test_main_saves_complete_candidate_snapshot_before_mode_filtering(self):
        source = pd.DataFrame([{
            "code": "A", "name": "A", "exchange": "SHFE",
            "bar_time": pd.Timestamp("2026-07-20 10:00"),
            "underlying": "au2608", "option_type": "CALL", "dte": 7,
            "expiry": "2026-07-27", "strike": 100.0, "last_price": 2.0,
            "moneyness": 0.01, "recent_volume": 1000.0, "open_interest": 500.0,
            "signal_score": 0, "confirmation_score": 0,
            "ma_bullish": False, "macd_bullish": False,
            "double_confirmed": False, "ma_direction_confirmed": False,
            "macd_direction_confirmed": False, "ma_cross_time": None,
            "macd_cross_time": None, "ma_cross_bars_ago": None,
            "macd_cross_bars_ago": None,
        }])
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "options.db"
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(option_cli, "scan_near_expiry_options", return_value=source), \
                 patch.dict("os.environ", {"WATCHMAN_LOGICAL_SLOT": "2026-07-20T10:05:00+08:00"}):
                code = option_cli.main(["--history-db", str(history_path)])

            self.assertEqual(code, 0)
            from option_history_store import load_option_history
            history = load_option_history(history_path)
            self.assertEqual(history["code"].tolist(), ["A"])
            self.assertEqual(history["scan_time"].dt.strftime("%H:%M").tolist(), ["10:05"])

    def test_main_uses_scheduler_logical_slot_as_scanner_now(self):
        logical_slot = "2026-07-20T10:05:00+08:00"
        with patch.object(option_cli, "QuoteClient", return_value=object()), \
             patch.object(
                 option_cli, "scan_near_expiry_options", return_value=pd.DataFrame()
             ) as scanner, patch.dict(
                 os.environ, {"WATCHMAN_LOGICAL_SLOT": logical_slot}, clear=True
             ):
            code = option_cli.main(["--no-history"])

        self.assertEqual(code, 0)
        self.assertEqual(scanner.call_args.kwargs["now"], pd.Timestamp(logical_slot))

    def test_main_empty_manual_scan_replaces_current_shanghai_hour_and_confirms_save(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "options.db"
            scan_time = "2026-07-20T10:00:00+08:00"
            with sqlite3.connect(history_path) as connection:
                connection.execute(
                    "CREATE TABLE option_scans (scan_time TEXT PRIMARY KEY)"
                )
                connection.execute(
                    "CREATE TABLE option_snapshots ("
                    "scan_time TEXT NOT NULL, code TEXT NOT NULL, "
                    "PRIMARY KEY (scan_time, code))"
                )
                connection.execute(
                    "INSERT INTO option_scans(scan_time) VALUES (?)", (scan_time,)
                )
                connection.execute(
                    "INSERT INTO option_snapshots(scan_time, code) VALUES (?, ?)",
                    (scan_time, "stale"),
                )
            output = StringIO()
            fixed_now = datetime.fromisoformat("2026-07-20T10:47:31+08:00")
            with patch.object(option_cli, "QuoteClient", return_value=object()), \
                 patch.object(
                     option_cli, "scan_near_expiry_options", return_value=pd.DataFrame()
                 ), patch.object(option_cli, "datetime", create=True) as clock, \
                 patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
                clock.now.return_value = fixed_now
                code = option_cli.main(["--history-db", str(history_path)])

            self.assertEqual(code, 0)
            with sqlite3.connect(history_path) as connection:
                rows = connection.execute(
                    "SELECT scan_time, code FROM option_snapshots"
                ).fetchall()
                scans = connection.execute(
                    "SELECT scan_time FROM option_scans"
                ).fetchall()
            self.assertEqual(rows, [])
            self.assertEqual(scans, [(scan_time,)])
            self.assertIn("历史快照已保存", output.getvalue())
            self.assertIn("（0条）", output.getvalue())


if __name__ == "__main__":
    unittest.main()
