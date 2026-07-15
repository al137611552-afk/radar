import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import intraday_radar


class IntradayMetricTests(unittest.TestCase):
    def test_uses_completed_bars_for_5_15_60_minute_metrics(self):
        times = pd.date_range("2026-07-15 10:00", periods=14, freq="5min")
        frame = pd.DataFrame({
            "datetime": times,
            "close": range(100, 114),
            "money": range(1, 15),
            "volume": range(10, 24),
            "open_interest": range(1000, 1014),
        })

        closed = intraday_radar.closed_intraday_bars(
            frame, as_of="2026-07-15 11:01:00+08:00"
        )
        metrics = intraday_radar.calculate_intraday_metrics(closed)

        self.assertEqual(closed["datetime"].iloc[-1], pd.Timestamp("2026-07-15 11:00"))
        self.assertEqual(metrics["turnover_5m"], 13.0)
        self.assertEqual(metrics["turnover_15m"], 36.0)
        self.assertEqual(metrics["turnover_60m"], 90.0)
        self.assertEqual(metrics["oi_change_5m"], 1.0)
        self.assertEqual(metrics["oi_change_15m"], 3.0)
        self.assertEqual(metrics["oi_change_60m"], 12.0)
        self.assertAlmostEqual(metrics["price_change_15m_pct"], 3 / 109 * 100)

    def test_ranks_recent_contracts_by_15_minute_turnover(self):
        metadata = [
            {"code": "rb2610", "name": "螺纹钢", "exchange_code": "SHFE"},
            {"code": "m2609", "name": "豆粕", "exchange_code": "DCE"},
            {"code": "jd2609", "name": "鸡蛋", "exchange_code": "DCE"},
        ]

        def bars(end, money_start, close_step):
            times = pd.date_range(end=pd.Timestamp(end), periods=13, freq="5min")
            return pd.DataFrame({
                "datetime": times,
                "close": [100 + i * close_step for i in range(13)],
                "money": range(money_start, money_start + 13),
                "volume": range(10, 23),
                "open_interest": range(1000, 1013),
            })

        result = intraday_radar.build_intraday_ranking(metadata, {
            "rb2610": bars("2026-07-15 11:00", 1, 1),
            "m2609": bars("2026-07-15 10:55", 2, -1),
            "jd2609": bars("2026-07-15 10:15", 100, 1),
        })

        self.assertEqual(result["code"].tolist(), ["m2609", "rb2610"])
        self.assertEqual(result["rank_15m"].tolist(), [1, 2])
        self.assertEqual(result.set_index("code").loc["m2609", "side"], "空")
        self.assertEqual(result.set_index("code").loc["rb2610", "side"], "多")

    def test_generates_intraday_radar_with_one_batch_request(self):
        metadata = [{
            "code": "rb2610", "name": "螺纹钢", "exchange_code": "SHFE"
        }]
        frame = pd.DataFrame({
            "datetime": pd.date_range("2026-07-15 10:00", periods=14, freq="5min"),
            "close": range(100, 114), "money": range(1, 15),
            "volume": range(10, 24), "open_interest": range(1000, 1014),
        })

        class Client:
            def search(self, **kwargs):
                self.search_args = kwargs
                return metadata

            def main_contracts(self):
                return {"rb": "rb2610"}

            def get_klines_by_count(self, codes, interval, count):
                self.fetch_args = (codes, interval, count)
                return {"rb2610": frame}

        client = Client()
        result = intraday_radar.generate_intraday_radar(
            client, as_of="2026-07-15 11:01:00+08:00"
        )

        self.assertEqual(client.search_args, {"category_type": 1})
        self.assertEqual(client.fetch_args, (["rb2610"], "5m", 25))
        self.assertEqual(result.loc[0, "bar_time"], pd.Timestamp("2026-07-15 11:00"))

    def test_annotates_rank_moves_top_entries_and_direction_reversals(self):
        current = pd.DataFrame([
            {"code": "B", "rank_15m": 1, "side": "空"},
            {"code": "C", "rank_15m": 2, "side": "多"},
            {"code": "A", "rank_15m": 3, "side": "空"},
        ])
        previous = {
            "version": 1, "scope": "intraday-rank",
            "ranks": {
                "A": {"rank": 1, "side": "多"},
                "B": {"rank": 2, "side": "多"},
            },
        }

        result, state = intraday_radar.annotate_rank_changes(
            current, previous, top_n=2
        )
        by_code = result.set_index("code")

        self.assertEqual(by_code.loc["B", "rank_change"], 1)
        self.assertTrue(by_code.loc["B", "direction_reversed"])
        self.assertTrue(by_code.loc["C", "entered_top"])
        self.assertTrue(by_code.loc["A", "exited_top"])
        self.assertEqual(state["ranks"]["C"], {"rank": 2, "side": "多"})


if __name__ == "__main__":
    unittest.main()
