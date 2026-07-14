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
            "signal_score": 6,
        }])

        display = option_cli.build_display_table(source)

        self.assertEqual(display.columns.tolist(), [
            "代码", "DTE", "类型", "行权价", "虚实值%", "最新价", "小时线截止",
            "近20小时量", "持仓量", "MA状态", "MACD状态", "信号分",
        ])
        self.assertEqual(display.loc[0, "MA状态"], "刚金叉")
        self.assertEqual(display.loc[0, "MACD状态"], "2根前金叉")
        self.assertEqual(display.loc[0, "虚实值%"], 1.54)


if __name__ == "__main__":
    unittest.main()
