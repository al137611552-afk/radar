import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import momentum_cli  # noqa: E402


class MomentumCliTests(unittest.TestCase):
    def test_display_table_uses_compact_chinese_columns(self):
        source = pd.DataFrame([{
            "code": "au6666", "name": "黄金收益率指数",
            "sector": "贵金属",
            "as_of": pd.Timestamp("2026-07-14"),
            "return_5d": 1.2345, "excess_5d": 0.2345, "rank_5d": 1,
            "sector_return_5d": 0.9345, "sector_excess_5d": 0.3,
            "sector_rank_5d": 1,
            "momentum_score": 88.888,
        }])

        display = momentum_cli.build_display_table(source, horizons=(5,))

        self.assertEqual(display.columns.tolist(), [
            "代码", "名称", "板块", "数据截止", "5日收益%", "5日全商品超额%",
            "5日排名", "5日板块收益%", "5日板块超额%", "5日板块排名", "综合动量分"
        ])
        self.assertEqual(display.loc[0, "5日收益%"], 1.23)
        self.assertEqual(display.loc[0, "5日板块超额%"], 0.3)
        self.assertEqual(display.loc[0, "综合动量分"], 88.89)

    def test_sector_display_table_uses_compact_chinese_columns(self):
        source = pd.DataFrame([{
            "sector": "贵金属", "constituents": 4,
            "as_of": pd.Timestamp("2026-07-14"),
            "sector_return_5d": 1.2345, "sector_rank_5d": 1,
            "sector_momentum_score": 90.909,
        }])

        display = momentum_cli.build_sector_display_table(source, horizons=(5,))

        self.assertEqual(display.columns.tolist(), [
            "板块", "品种数", "数据截止", "5日板块收益%", "5日板块排名", "板块动量分"
        ])
        self.assertEqual(display.loc[0, "5日板块收益%"], 1.23)
        self.assertEqual(display.loc[0, "板块动量分"], 90.91)

    def test_main_exports_product_and_sector_rankings(self):
        source = pd.DataFrame([{
            "code": "au6666", "name": "黄金收益率指数", "exchange": "SHFE",
            "sector": "贵金属", "as_of": pd.Timestamp("2026-07-14"),
            "return_2d": 2.0, "excess_2d": 0.0, "rank_2d": 1,
            "sector_return_2d": 2.0, "sector_excess_2d": 0.0,
            "sector_rank_2d": 1, "momentum_score": 100.0,
        }])
        with tempfile.TemporaryDirectory() as directory:
            product_path = Path(directory) / "momentum.csv"
            sector_path = Path(directory) / "sectors.csv"
            with patch.object(momentum_cli, "QuoteClient", return_value=object()), \
                    patch.object(momentum_cli, "generate_ranking", return_value=source), \
                    redirect_stdout(io.StringIO()):
                code = momentum_cli.main([
                    "--top", "0", "--horizons", "2",
                    "--csv", str(product_path),
                    "--sector-csv", str(sector_path),
                ])

            self.assertEqual(code, 0)
            self.assertEqual(pd.read_csv(product_path)["sector"].tolist(), ["贵金属"])
            self.assertEqual(pd.read_csv(sector_path)["sector"].tolist(), ["贵金属"])


if __name__ == "__main__":
    unittest.main()
