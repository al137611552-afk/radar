import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import history_cli


class HistoryCliTests(unittest.TestCase):
    def test_builds_persistence_summary_table(self):
        summary = pd.DataFrame([{
            "code": "rb2610", "name": "螺纹钢", "side": "多",
            "latest_rank": 2, "rank_improvement": 4,
            "top_streak": 5, "top_appearances": 5,
            "turnover_15m": 300_000_000,
            "turnover_growth_pct": 80.5,
            "turnover_acceleration_15m_pct": 25.0,
            "persistence_status": "持续升温",
        }])

        display = history_cli.build_persistence_table(summary)

        self.assertEqual(display.loc[0, "状态"], "持续升温")
        self.assertEqual(display.loc[0, "排名改善"], 4)
        self.assertEqual(display.loc[0, "连续TOP"], 5)
        self.assertEqual(display.loc[0, "15分额(亿)"], 3.0)

    def test_rejects_non_positive_integer_options_during_parsing(self):
        for option in ("--top", "--snapshots", "--limit"):
            for value in ("0", "-1"):
                with self.subTest(option=option, value=value):
                    with self.assertRaises(SystemExit):
                        history_cli.parse_args([option, value])

    def test_builds_rank_trajectory_table(self):
        trajectory = pd.DataFrame([
            {
                "scan_time": pd.Timestamp("2026-07-15 11:00", tz="Asia/Shanghai"),
                "bar_time": pd.Timestamp("2026-07-15 11:00"),
                "rank_15m": 5, "side": "多", "turnover_15m": 200_000_000,
                "turnover_acceleration_15m_pct": 10,
                "price_change_15m_pct": 0.5,
            },
        ])

        display = history_cli.build_trajectory_table(trajectory)

        self.assertEqual(display.loc[0, "排名"], 5)
        self.assertEqual(display.loc[0, "15分额(亿)"], 2.0)
        self.assertEqual(display.loc[0, "扫描时间"], "07-15 11:00")


if __name__ == "__main__":
    unittest.main()
