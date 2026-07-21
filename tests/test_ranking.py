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
    def test_omits_non_contiguous_or_non_finite_trailing_close_windows(self):
        result = ranking.build_momentum_ranking(
            {
                "good6666": frame([100.0, 110.0, 121.0]),
                "gap6666": frame([100.0, float("nan"), 121.0]),
                "tail6666": frame([100.0, 110.0, float("nan")]),
                "inf6666": frame([100.0, 110.0, float("inf")]),
            },
            horizons=(2,),
        )

        self.assertEqual(result["code"].tolist(), ["good6666"])
        self.assertEqual(result.loc[0, "as_of"], frame([1, 2, 3])["datetime"].iloc[-1])

    def test_calculates_annualized_volatility_and_risk_adjusted_momentum(self):
        source = frame([100.0, 120.0, 90.0, 110.0])

        result = ranking.build_momentum_ranking(
            {"au6666": source}, horizons=(3,)
        ).iloc[0]

        daily_returns = source["close"].pct_change().dropna().iloc[-3:]
        expected_volatility = daily_returns.std(ddof=1) * (252 ** 0.5) * 100
        self.assertAlmostEqual(
            result["annualized_volatility_3d"], expected_volatility
        )
        self.assertAlmostEqual(
            result["risk_adjusted_3d"],
            result["return_3d"] / max(expected_volatility, 0.01),
        )
        self.assertEqual(result["risk_adjusted_score"], 100.0)
        self.assertEqual(result["risk_long_rank"], 1)
        self.assertEqual(result["risk_short_rank"], 1)

    def test_risk_adjusted_rank_rewards_lower_volatility_for_same_return(self):
        result = ranking.build_momentum_ranking(
            {
                "au6666": frame([100.0, 102.0, 104.0, 106.0, 110.0]),
                "ag6666": frame([100.0, 130.0, 80.0, 140.0, 110.0]),
            },
            horizons=(4,),
        ).set_index("code")

        self.assertAlmostEqual(
            result.loc["au6666", "return_4d"],
            result.loc["ag6666", "return_4d"],
        )
        self.assertLess(
            result.loc["au6666", "annualized_volatility_4d"],
            result.loc["ag6666", "annualized_volatility_4d"],
        )
        self.assertGreater(
            result.loc["au6666", "risk_adjusted_score"],
            result.loc["ag6666", "risk_adjusted_score"],
        )
        self.assertEqual(result.loc["au6666", "risk_long_rank"], 1)
        self.assertEqual(result.loc["ag6666", "risk_short_rank"], 1)

    def test_labels_cross_sectional_volatility_risk(self):
        result = ranking.build_momentum_ranking(
            {
                "au6666": frame([100.0, 101.0, 102.0, 103.0]),
                "ag6666": frame([100.0, 103.0, 99.0, 104.0]),
                "rb6666": frame([100.0, 110.0, 95.0, 105.0]),
                "br6666": frame([100.0, 140.0, 70.0, 110.0]),
            },
            horizons=(3,),
        ).set_index("code")

        self.assertEqual(result.loc["br6666", "volatility_risk"], "高波动")
        self.assertEqual(result.loc["au6666", "volatility_risk"], "常态")
        self.assertGreater(
            result.loc["br6666", "volatility_score"],
            result.loc["au6666", "volatility_score"],
        )

    def test_assigns_separate_long_and_short_momentum_ranks(self):
        result = ranking.build_momentum_ranking(
            {
                "au6666": frame([100, 110, 120]),
                "rb6666": frame([100, 100, 100]),
                "br6666": frame([100, 90, 80]),
            },
            horizons=(2,),
        ).set_index("code")

        self.assertEqual(result.loc["au6666", "long_rank"], 1)
        self.assertEqual(result.loc["rb6666", "long_rank"], 2)
        self.assertEqual(result.loc["br6666", "long_rank"], 3)
        self.assertEqual(result.loc["br6666", "short_rank"], 1)
        self.assertEqual(result.loc["rb6666", "short_rank"], 2)
        self.assertEqual(result.loc["au6666", "short_rank"], 3)

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

    def test_builds_sector_risk_adjusted_leaderboard(self):
        product_ranking = ranking.build_momentum_ranking(
            {
                "au6666": frame([100.0, 102.0, 104.0, 106.0]),
                "rb6666": frame([100.0, 110.0, 90.0, 106.0]),
                "br6666": frame([100.0, 130.0, 80.0, 106.0]),
            },
            horizons=(3,),
        )

        result = ranking.build_sector_ranking(
            product_ranking, horizons=(3,)
        ).set_index("sector")

        product = product_ranking.set_index("code")
        self.assertAlmostEqual(
            result.loc["贵金属", "sector_risk_adjusted_3d"],
            product.loc["au6666", "risk_adjusted_3d"],
        )
        self.assertEqual(result.loc["贵金属", "sector_risk_long_rank"], 1)
        self.assertEqual(result.loc["能源化工", "sector_risk_short_rank"], 1)
        self.assertEqual(result.loc["能源化工", "sector_volatility_risk"], "高波动")

    def test_sector_ranking_excludes_members_with_non_finite_metrics(self):
        product_ranking = ranking.build_momentum_ranking(
            {
                "au6666": frame([100.0, 102.0, 104.0, 106.0]),
                "ag6666": frame([100.0, 103.0, 105.0, 107.0]),
                "rb6666": frame([100.0, 101.0, 102.0, 103.0]),
            },
            horizons=(3,),
        )
        product_ranking.loc[
            product_ranking["code"] == "ag6666", "risk_adjusted_3d"
        ] = float("inf")

        result = ranking.build_sector_ranking(
            product_ranking, horizons=(3,)
        ).set_index("sector")
        products = product_ranking.set_index("code")

        self.assertEqual(result.loc["贵金属", "constituents"], 1)
        self.assertAlmostEqual(
            result.loc["贵金属", "sector_risk_adjusted_3d"],
            products.loc["au6666", "risk_adjusted_3d"],
        )

    def test_assigns_separate_sector_long_and_short_ranks(self):
        product_ranking = ranking.build_momentum_ranking(
            {
                "au6666": frame([100, 110, 120]),
                "rb6666": frame([100, 100, 100]),
                "br6666": frame([100, 90, 80]),
            },
            horizons=(2,),
        )

        result = ranking.build_sector_ranking(
            product_ranking, horizons=(2,)
        ).set_index("sector")

        self.assertEqual(result.loc["贵金属", "sector_long_rank"], 1)
        self.assertEqual(result.loc["能源化工", "sector_short_rank"], 1)

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
