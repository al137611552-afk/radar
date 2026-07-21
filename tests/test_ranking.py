import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ranking  # noqa: E402


def frame(closes, start="2026-01-01"):
    return pd.DataFrame({
        "datetime": pd.date_range(start, periods=len(closes), freq="D"),
        "close": closes,
    })


class MomentumRankingTests(unittest.TestCase):
    def test_ranks_return_and_cross_sectional_excess(self):
        frames = {
            "au6666": frame([100, 110, 121]),
            "rb6666": frame([100, 100, 100]),
        }
        metadata = {
            "au6666": {"name": "黄金收益率指数", "exchange_code": "SHFE"},
            "rb6666": {"name": "螺纹钢收益率指数", "exchange_code": "SHFE"},
        }

        result = ranking.build_momentum_ranking(
            frames, metadata=metadata, horizons=(2,)
        )

        self.assertEqual(result["code"].tolist(), ["au6666", "rb6666"])
        self.assertEqual(str(result.loc[0, "as_of"]), "2026-01-03 00:00:00")
        self.assertAlmostEqual(result.loc[0, "return_2d"], 21.0)
        self.assertAlmostEqual(result.loc[1, "return_2d"], 0.0)
        self.assertAlmostEqual(result.loc[0, "excess_2d"], 10.5)
        self.assertAlmostEqual(result.loc[1, "excess_2d"], -10.5)
        self.assertEqual(result["rank_2d"].tolist(), [1, 2])

    def test_assigns_sector_and_calculates_sector_relative_metrics(self):
        frames = {
            "au6666": frame([100, 110, 120]),
            "ag6666": frame([100, 105, 110]),
            "rb6666": frame([100, 100, 100]),
        }

        result = ranking.build_momentum_ranking(frames, horizons=(2,)).set_index("code")

        self.assertEqual(result.loc["au6666", "sector"], "贵金属")
        self.assertEqual(result.loc["ag6666", "sector"], "贵金属")
        self.assertEqual(result.loc["rb6666", "sector"], "黑色")
        self.assertAlmostEqual(result.loc["au6666", "sector_return_2d"], 15.0)
        self.assertAlmostEqual(result.loc["au6666", "sector_excess_2d"], 5.0)
        self.assertAlmostEqual(result.loc["ag6666", "sector_excess_2d"], -5.0)
        self.assertEqual(result.loc["au6666", "sector_rank_2d"], 1)
        self.assertEqual(result.loc["ag6666", "sector_rank_2d"], 2)

    def test_builds_sector_momentum_leaderboard(self):
        product_ranking = ranking.build_momentum_ranking(
            {
                "au6666": frame([100, 110, 120]),
                "ag6666": frame([100, 105, 110]),
                "rb6666": frame([100, 100, 100]),
            },
            horizons=(2,),
        )

        result = ranking.build_sector_ranking(product_ranking, horizons=(2,))

        self.assertEqual(result["sector"].tolist(), ["贵金属", "黑色"])
        self.assertEqual(result["constituents"].tolist(), [2, 1])
        self.assertAlmostEqual(result.loc[0, "sector_return_2d"], 15.0)
        self.assertAlmostEqual(result.loc[1, "sector_return_2d"], 0.0)
        self.assertEqual(result["sector_rank_2d"].tolist(), [1, 2])
        self.assertAlmostEqual(result.loc[0, "sector_momentum_score"], 100.0)
        self.assertAlmostEqual(result.loc[1, "sector_momentum_score"], 50.0)

    def test_discovers_only_official_commodity_return_indices(self):
        instruments = [
            {"code": "au6666", "name": "黄金收益率指数", "category_type": 1,
             "variety_type": 7, "exchange_code": "SHFE"},
            {"code": "sc6666_01", "name": "测试", "category_type": 1,
             "variety_type": 7, "exchange_code": "INE"},
            {"code": "IF6666", "name": "股指", "category_type": 1,
             "variety_type": 7, "exchange_code": "CFFEX"},
            {"code": "rb9999", "name": "螺纹主连", "category_type": 1,
             "variety_type": 2, "exchange_code": "SHFE"},
        ]

        result = ranking.select_commodity_return_indices(instruments)

        self.assertEqual([item["code"] for item in result], ["au6666"])

    def test_generate_ranking_discovers_and_fetches_in_one_batch(self):
        class Client:
            def __init__(self):
                self.fetch_args = None

            def search(self, **kwargs):
                self.search_args = kwargs
                return [
                    {"code": "au6666", "name": "黄金收益率指数",
                     "category_type": 1, "variety_type": 7,
                     "exchange_code": "SHFE"},
                    {"code": "rb9999", "name": "螺纹主连",
                     "category_type": 1, "variety_type": 2,
                     "exchange_code": "SHFE"},
                ]

            def get_klines_by_count(self, codes, interval, count):
                self.fetch_args = (codes, interval, count)
                return {"au6666": frame([100, 110, 121])}

        client = Client()
        result = ranking.generate_ranking(client, horizons=(2,))

        self.assertEqual(client.search_args, {"category_type": 1})
        self.assertEqual(client.fetch_args, (["au6666"], "day", 4))
        self.assertEqual(result["code"].tolist(), ["au6666"])

    def test_drops_current_incomplete_daily_bar(self):
        source = frame([100, 110, 999], start="2026-07-13")

        closed = ranking.closed_daily_bars(
            source, now=pd.Timestamp("2026-07-15 10:00", tz="Asia/Shanghai")
        )

        self.assertEqual(closed["close"].tolist(), [100, 110])


if __name__ == "__main__":
    unittest.main()
