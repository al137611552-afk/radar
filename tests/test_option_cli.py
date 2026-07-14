import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import option_cli


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


if __name__ == "__main__":
    unittest.main()
