import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import option_cli  # noqa: E402


class OptionCliTests(unittest.TestCase):
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
        ])

        self.assertTrue(args.new_only)
        self.assertEqual(args.state_file, Path("output/state/custom.json"))
        self.assertEqual(
            args.snapshot_csv, Path("output/options_candidates_latest.csv")
        )
        self.assertEqual(args.filtered_csv, Path("output/options_latest.csv"))

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
                "--new-only", "--state-file", str(state_path),
                "--snapshot-csv", str(snapshot_path),
                "--filtered-csv", str(filtered_path),
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


if __name__ == "__main__":
    unittest.main()
