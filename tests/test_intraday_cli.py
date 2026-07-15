import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import intraday_cli


class IntradayCliTests(unittest.TestCase):
    def setUp(self):
        self.source = pd.DataFrame([
            {
                "code": "rb2610", "name": "螺纹钢", "side": "多",
                "rank_15m": 1, "previous_rank": 3, "rank_change": 2,
                "entered_top": False, "exited_top": False,
                "direction_reversed": True,
                "price_change_15m_pct": 1.25,
                "turnover_5m_yi": 1.0, "turnover_15m_yi": 3.0,
                "turnover_60m_yi": 10.0,
                "turnover_acceleration_15m_pct": 20.0,
                "oi_change_5m": 10, "oi_change_15m": 30,
                "oi_change_60m": 100,
                "bar_time": pd.Timestamp("2026-07-15 11:00"),
            },
            {
                "code": "m2609", "name": "豆粕", "side": "空",
                "rank_15m": 2, "previous_rank": None, "rank_change": None,
                "entered_top": True, "exited_top": False,
                "direction_reversed": False,
                "price_change_15m_pct": -0.5,
                "turnover_5m_yi": 0.8, "turnover_15m_yi": 2.0,
                "turnover_60m_yi": 8.0,
                "turnover_acceleration_15m_pct": -10.0,
                "oi_change_5m": -5, "oi_change_15m": -15,
                "oi_change_60m": -50,
                "bar_time": pd.Timestamp("2026-07-15 11:00"),
            },
        ])

    def test_builds_compact_rank_change_table(self):
        table = intraday_cli.build_display_table(self.source)

        self.assertEqual(table.loc[0, "排名变化"], "↑2")
        self.assertEqual(table.loc[0, "事件"], "方向反转")
        self.assertEqual(table.loc[1, "排名变化"], "新进")
        self.assertEqual(table.loc[1, "事件"], "新进TOP")
        self.assertIn("15分额(亿)", table.columns)

    def test_persists_rank_snapshot_between_runs(self):
        current = self.source.drop(columns=[
            "previous_rank", "rank_change", "entered_top", "exited_top",
            "direction_reversed",
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rank.json"
            first = intraday_cli.apply_rank_state(current, path, top_n=2)
            second = intraday_cli.apply_rank_state(current, path, top_n=2)

            self.assertTrue(first["entered_top"].all())
            self.assertFalse(second["entered_top"].any())
            self.assertEqual(second["rank_change"].tolist(), [0, 0])
            self.assertTrue(path.exists())

    def test_parse_args_enables_sqlite_history_by_default(self):
        args = intraday_cli.parse_args([])

        self.assertEqual(args.history_db, Path("output/history/radar.db"))
    def test_parse_args_rejects_non_positive_top(self):
        for value in ("0", "-1"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit):
                    intraday_cli.parse_args(["--top", value])


if __name__ == "__main__":
    unittest.main()
