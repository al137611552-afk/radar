import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import hotspot_radar


class HotspotUniverseTests(unittest.TestCase):
    def test_selects_only_domestic_commodity_main_contracts(self):
        mains = {
            "rb9999": "rb2610",
            "IF9999": "IF2608",
            "CL.9999.USD": "CL.202608.USD",
            "m9999": "m2609",
        }
        metadata = [
            {"code": "rb2610", "name": "螺纹钢2610", "exchange_code": "SHFE"},
            {"code": "IF2608", "name": "沪深300", "exchange_code": "CFFEX"},
            {"code": "CL.202608.USD", "name": "美原油", "exchange_code": "NYMEX"},
            {"code": "m2609", "name": "豆粕2609", "exchange_code": "DCE"},
        ]

        result = hotspot_radar.select_domestic_main_contracts(mains, metadata)

        self.assertEqual([item["code"] for item in result], ["m2609", "rb2610"])


class HotspotRankingTests(unittest.TestCase):
    def test_ranks_turnover_by_price_direction_and_open_interest_quadrant(self):
        metadata = [
            {"code": "rb2610", "name": "螺纹钢", "exchange_code": "SHFE", "multiplier": 10},
            {"code": "m2609", "name": "豆粕", "exchange_code": "DCE", "multiplier": 10},
            {"code": "au2608", "name": "黄金", "exchange_code": "SHFE", "multiplier": 1000},
            {"code": "ag2608", "name": "白银", "exchange_code": "SHFE", "multiplier": 15},
        ]

        def bars(previous_close, close, money, previous_oi, open_interest):
            return pd.DataFrame({
                "datetime": pd.to_datetime(["2026-07-14", "2026-07-15"]),
                "close": [previous_close, close],
                "volume": [100, 200],
                "money": [money / 2, money],
                "open_interest": [previous_oi, open_interest],
            })

        frames = {
            "rb2610": bars(100, 110, 5_000_000_000, 1000, 1100),
            "m2609": bars(100, 90, 6_000_000_000, 1000, 1100),
            "au2608": bars(100, 105, 4_000_000_000, 1000, 900),
            "ag2608": bars(100, 95, 3_000_000_000, 1000, 900),
        }

        result = hotspot_radar.build_hotspot_ranking(metadata, frames)

        self.assertEqual(result["code"].tolist(), ["m2609", "rb2610", "au2608", "ag2608"])
        by_code = result.set_index("code")
        self.assertEqual(by_code.loc["rb2610", "side"], "多")
        self.assertEqual(by_code.loc["m2609", "side"], "空")
        self.assertEqual(by_code.loc["rb2610", "position_action"], "多头增仓")
        self.assertEqual(by_code.loc["m2609", "position_action"], "空头增仓")
        self.assertEqual(by_code.loc["au2608", "position_action"], "空头减仓")
        self.assertEqual(by_code.loc["ag2608", "position_action"], "多头减仓")
        self.assertEqual(by_code.loc["rb2610", "side_rank"], 1)
        self.assertEqual(by_code.loc["au2608", "side_rank"], 2)
        self.assertEqual(by_code.loc["m2609", "side_rank"], 1)
        self.assertAlmostEqual(by_code.loc["m2609", "turnover_yi"], 60.0)
        self.assertAlmostEqual(by_code.loc["rb2610", "change_pct"], 10.0)

    def test_generates_radar_with_one_batch_kline_request(self):
        metadata = [
            {"code": "rb2610", "name": "螺纹钢", "exchange_code": "SHFE", "multiplier": 10}
        ]
        bars = pd.DataFrame({
            "datetime": pd.to_datetime(["2026-07-14", "2026-07-15"]),
            "close": [3000, 3030], "volume": [100, 200],
            "money": [1_000_000, 2_000_000],
            "open_interest": [1000, 1100],
        })

        class Client:
            def main_contracts(self):
                return {"rb9999": "rb2610"}

            def search(self, **kwargs):
                self.search_args = kwargs
                return metadata

            def get_klines_by_count(self, codes, interval, count):
                self.fetch_args = (codes, interval, count)
                return {"rb2610": bars}

        client = Client()
        result = hotspot_radar.generate_hotspot_radar(client)

        self.assertEqual(client.search_args, {"category_type": 1})
        self.assertEqual(client.fetch_args, (["rb2610"], "day", 2))
        self.assertEqual(result["code"].tolist(), ["rb2610"])

    def test_excludes_contracts_without_a_bar_for_current_market_trade_date(self):
        metadata = [
            {"code": "rb2610", "name": "螺纹钢", "exchange_code": "SHFE"},
            {"code": "jd2608", "name": "鸡蛋", "exchange_code": "DCE"},
        ]

        def bars(dates):
            return pd.DataFrame({
                "datetime": pd.to_datetime(dates), "close": [100, 101],
                "volume": [100, 200], "money": [1_000_000, 2_000_000],
                "open_interest": [1000, 1100],
            })

        result = hotspot_radar.build_hotspot_ranking(metadata, {
            "rb2610": bars(["2026-07-14", "2026-07-15"]),
            "jd2608": bars(["2026-07-13", "2026-07-14"]),
        })

        self.assertEqual(result["code"].tolist(), ["rb2610"])
        self.assertEqual(result["trade_date"].unique().tolist(), ["2026-07-15"])


if __name__ == "__main__":
    unittest.main()
