import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import momentum_cli


class MomentumCliTests(unittest.TestCase):
    def test_display_table_uses_compact_chinese_columns(self):
        source = pd.DataFrame([{
            "code": "au6666", "name": "黄金收益率指数",
            "as_of": pd.Timestamp("2026-07-14"),
            "return_5d": 1.2345, "excess_5d": 0.2345, "rank_5d": 1,
            "momentum_score": 88.888,
        }])

        display = momentum_cli.build_display_table(source, horizons=(5,))

        self.assertEqual(display.columns.tolist(), [
            "代码", "名称", "数据截止", "5日收益%", "5日超额%", "5日排名", "综合动量分"
        ])
        self.assertEqual(display.loc[0, "5日收益%"], 1.23)
        self.assertEqual(display.loc[0, "综合动量分"], 88.89)


if __name__ == "__main__":
    unittest.main()
