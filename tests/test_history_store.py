import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import history_store


class IntradayHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = pd.DataFrame([
            {
                "code": "rb2610", "name": "螺纹钢", "exchange": "SHFE",
                "bar_time": pd.Timestamp("2026-07-15 11:00"),
                "rank_15m": 1, "side": "多", "price_change_15m_pct": 1.0,
                "turnover_5m": 100.0, "turnover_15m": 300.0,
                "turnover_60m": 1000.0,
                "turnover_acceleration_15m_pct": 20.0,
                "oi_change_5m": 10.0, "oi_change_15m": 30.0,
                "oi_change_60m": 100.0,
            },
            {
                "code": "m2609", "name": "豆粕", "exchange": "DCE",
                "bar_time": pd.Timestamp("2026-07-15 11:00"),
                "rank_15m": 2, "side": "空", "price_change_15m_pct": -0.5,
                "turnover_5m": 80.0, "turnover_15m": 200.0,
                "turnover_60m": 800.0,
                "turnover_acceleration_15m_pct": -10.0,
                "oi_change_5m": -5.0, "oi_change_15m": -15.0,
                "oi_change_60m": -50.0,
            },
        ])

    def test_saves_snapshot_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            scan_time = "2026-07-15T11:01:00+08:00"

            first_count = history_store.save_intraday_snapshot(
                path, self.snapshot, scan_time=scan_time
            )
            second_count = history_store.save_intraday_snapshot(
                path, self.snapshot, scan_time=scan_time
            )
            loaded = history_store.load_intraday_history(path)

            self.assertEqual((first_count, second_count), (2, 2))
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded["code"].tolist(), ["rb2610", "m2609"])
            self.assertEqual(loaded["scan_time"].nunique(), 1)

    def test_normalizes_mixed_bar_time_inputs_to_shanghai_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            snapshot = self.snapshot.copy()
            snapshot["bar_time"] = snapshot["bar_time"].astype(object)
            snapshot.loc[0, "bar_time"] = pd.Timestamp("2026-07-15 11:00")
            snapshot.loc[1, "bar_time"] = pd.Timestamp(
                "2026-07-15 03:00", tz="UTC"
            )

            history_store.save_intraday_snapshot(
                path, snapshot, scan_time="2026-07-15 11:01"
            )
            loaded = history_store.load_intraday_history(path)

            self.assertEqual(
                str(loaded["bar_time"].dt.tz),
                "Asia/Shanghai",
            )
            self.assertEqual(loaded["bar_time"].nunique(), 1)
            self.assertTrue(
                loaded["bar_time"].eq(
                    pd.Timestamp("2026-07-15 11:00", tz="Asia/Shanghai")
                ).all()
            )

    def test_validates_snapshot_input_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            for column in ("code", "bar_time", "rank_15m"):
                with self.subTest(missing=column):
                    with self.assertRaisesRegex(ValueError, column):
                        history_store.save_intraday_snapshot(
                            path, self.snapshot.drop(columns=column),
                            scan_time="2026-07-15 11:01",
                        )
                invalid = self.snapshot.copy()
                invalid.loc[0, column] = None
                with self.subTest(null=column):
                    with self.assertRaisesRegex(ValueError, column):
                        history_store.save_intraday_snapshot(
                            path, invalid, scan_time="2026-07-15 11:01"
                        )

            duplicate = pd.concat([self.snapshot, self.snapshot.iloc[[0]]])
            with self.assertRaisesRegex(ValueError, "duplicate.*code"):
                history_store.save_intraday_snapshot(
                    path, duplicate, scan_time="2026-07-15 11:01"
                )
            with self.assertRaisesRegex(ValueError, "scan_time"):
                history_store.save_intraday_snapshot(
                    path, self.snapshot, scan_time="not-a-time"
                )
            invalid_bar = self.snapshot.copy()
            invalid_bar["bar_time"] = invalid_bar["bar_time"].astype(object)
            invalid_bar.loc[0, "bar_time"] = "not-a-time"
            with self.assertRaisesRegex(ValueError, "bar_time"):
                history_store.save_intraday_snapshot(
                    path, invalid_bar, scan_time="2026-07-15 11:01"
                )

    def test_rejects_invalid_analysis_metrics_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            for column in ("turnover_15m", "turnover_acceleration_15m_pct"):
                with self.subTest(missing=column):
                    with self.assertRaisesRegex(ValueError, column):
                        history_store.save_intraday_snapshot(
                            path,
                            self.snapshot.drop(columns=column),
                            scan_time="2026-07-15 11:01",
                        )

            for invalid_rank in (0, -1, 1.5, True, "not-an-int"):
                invalid = self.snapshot.copy()
                invalid["rank_15m"] = invalid["rank_15m"].astype(object)
                invalid.loc[0, "rank_15m"] = invalid_rank
                with self.subTest(rank=invalid_rank):
                    with self.assertRaisesRegex(ValueError, "rank_15m"):
                        history_store.save_intraday_snapshot(
                            path, invalid, scan_time="2026-07-15 11:01"
                        )

            for column in ("turnover_15m", "turnover_acceleration_15m_pct"):
                for invalid_value in (float("nan"), float("inf"), "not-a-number"):
                    invalid = self.snapshot.copy()
                    invalid[column] = invalid[column].astype(object)
                    invalid.loc[0, column] = invalid_value
                    with self.subTest(column=column, value=invalid_value):
                        with self.assertRaisesRegex(ValueError, column):
                            history_store.save_intraday_snapshot(
                                path, invalid, scan_time="2026-07-15 11:01"
                            )

            negative_turnover = self.snapshot.copy()
            negative_turnover.loc[0, "turnover_15m"] = -1
            with self.assertRaisesRegex(ValueError, "turnover_15m"):
                history_store.save_intraday_snapshot(
                    path, negative_turnover, scan_time="2026-07-15 11:01"
                )

    def test_replaces_complete_snapshot_at_same_scan_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            scan_time = "2026-07-15T11:01:00+08:00"
            history_store.save_intraday_snapshot(
                path, self.snapshot, scan_time=scan_time
            )

            history_store.save_intraday_snapshot(
                path, self.snapshot.iloc[[0]], scan_time=scan_time
            )

            loaded = history_store.load_intraday_history(path)
            self.assertEqual(loaded["code"].tolist(), ["rb2610"])

    def test_failed_replacement_rolls_back_to_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            scan_time = "2026-07-15T11:01:00+08:00"
            history_store.save_intraday_snapshot(
                path, self.snapshot, scan_time=scan_time
            )
            invalid = self.snapshot.iloc[[0]].copy()
            invalid["name"] = invalid["name"].astype(object)
            invalid.loc[invalid.index[0], "name"] = object()

            with self.assertRaises(sqlite3.ProgrammingError):
                history_store.save_intraday_snapshot(
                    path, invalid, scan_time=scan_time
                )

            loaded = history_store.load_intraday_history(path)
            self.assertEqual(loaded["code"].tolist(), ["rb2610", "m2609"])

    def test_default_snapshot_identity_uses_latest_completed_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"

            history_store.save_intraday_snapshot(path, self.snapshot)
            history_store.save_intraday_snapshot(path, self.snapshot)
            loaded = history_store.load_intraday_history(path)

            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded["scan_time"].nunique(), 1)
            self.assertEqual(
                loaded["scan_time"].iloc[0],
                pd.Timestamp("2026-07-15 11:00", tz="Asia/Shanghai"),
            )

    def test_loads_rank_trajectory_in_chronological_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            for minute, rank in ((1, 1), (6, 3), (11, 2)):
                snapshot = self.snapshot.copy()
                snapshot.loc[snapshot["code"].eq("rb2610"), "rank_15m"] = rank
                history_store.save_intraday_snapshot(
                    path, snapshot,
                    scan_time=f"2026-07-15T11:{minute:02d}:00+08:00",
                )

            trajectory = history_store.load_rank_trajectory(
                path, "rb2610", limit=2
            )

            self.assertEqual(trajectory["rank_15m"].tolist(), [3, 2])
            self.assertTrue(trajectory["scan_time"].is_monotonic_increasing)
            self.assertEqual(trajectory["code"].unique().tolist(), ["rb2610"])

    def test_rejects_non_positive_analysis_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            for limit in (0, -1, 1.5, True):
                with self.subTest(parameter="limit", value=limit):
                    with self.assertRaisesRegex(ValueError, "limit"):
                        history_store.load_rank_trajectory(path, "rb2610", limit)

        history = pd.DataFrame([{
            "scan_time": pd.Timestamp("2026-07-15 10:00"),
            "code": "A", "rank_15m": 1, "turnover_15m": 100,
            "turnover_acceleration_15m_pct": 0,
        }])
        for parameter in ("top_n", "lookback_snapshots"):
            for value in (0, -1, 1.5, True):
                kwargs = {parameter: value}
                with self.subTest(parameter=parameter, value=value):
                    with self.assertRaisesRegex(ValueError, parameter):
                        history_store.summarize_hotspot_persistence(
                            history, **kwargs
                        )

    def test_classifies_sustained_warming_and_pulse_hotspots(self):
        scans = pd.date_range("2026-07-15 10:00", periods=4, freq="5min", tz="Asia/Shanghai")
        rows = []
        for index, scan in enumerate(scans):
            rows.extend([
                {
                    "scan_time": scan, "code": "A", "name": "持续升温",
                    "rank_15m": [8, 6, 4, 3][index], "side": "多",
                    "turnover_15m": [100, 130, 170, 220][index],
                    "turnover_acceleration_15m_pct": 30,
                },
                {
                    "scan_time": scan, "code": "B", "name": "持续热点",
                    "rank_15m": [2, 2, 2, 1][index], "side": "多",
                    "turnover_15m": [200, 210, 220, 230][index],
                    "turnover_acceleration_15m_pct": 5,
                },
            ])
        rows.extend([
            {
                "scan_time": scans[-1], "code": "C", "name": "脉冲",
                "rank_15m": 2, "side": "空", "turnover_15m": 300,
                "turnover_acceleration_15m_pct": 180,
            },
            {
                "scan_time": scans[-1], "code": "D", "name": "新晋",
                "rank_15m": 4, "side": "多", "turnover_15m": 150,
                "turnover_acceleration_15m_pct": 20,
            },
        ])

        summary = history_store.summarize_hotspot_persistence(
            pd.DataFrame(rows), top_n=10, lookback_snapshots=6
        ).set_index("code")

        self.assertEqual(summary.loc["A", "persistence_status"], "持续升温")
        self.assertEqual(summary.loc["B", "persistence_status"], "持续热点")
        self.assertEqual(summary.loc["C", "persistence_status"], "脉冲热点")
        self.assertEqual(summary.loc["D", "persistence_status"], "新晋热点")
        self.assertEqual(summary.loc["A", "top_streak"], 4)
        self.assertEqual(summary.loc["A", "rank_improvement"], 5)

    def test_top_streak_breaks_when_contract_is_missing_from_a_scan(self):
        scans = pd.date_range(
            "2026-07-15 10:00", periods=4, freq="5min", tz="Asia/Shanghai"
        )
        rows = []
        for index in (0, 2, 3):
            rows.append({
                "scan_time": scans[index], "code": "A", "rank_15m": 1,
                "turnover_15m": 100 + index,
                "turnover_acceleration_15m_pct": 0,
            })
        for scan in scans:
            rows.append({
                "scan_time": scan, "code": "B", "rank_15m": 2,
                "turnover_15m": 100,
                "turnover_acceleration_15m_pct": 0,
            })

        summary = history_store.summarize_hotspot_persistence(
            pd.DataFrame(rows), top_n=10, lookback_snapshots=4
        ).set_index("code")

        self.assertEqual(summary.loc["A", "top_streak"], 2)

    def test_warming_metrics_start_at_current_continuous_top_interval(self):
        scans = pd.date_range(
            "2026-07-15 10:00", periods=5, freq="5min", tz="Asia/Shanghai"
        )
        rows = []
        for scan, rank, turnover in zip(
            scans, [1, 20, 8, 6, 4], [1000, 1000, 100, 150, 200]
        ):
            rows.append({
                "scan_time": scan, "code": "A", "rank_15m": rank,
                "turnover_15m": turnover,
                "turnover_acceleration_15m_pct": 10,
            })

        summary = history_store.summarize_hotspot_persistence(
            pd.DataFrame(rows), top_n=10, lookback_snapshots=5
        ).set_index("code")

        self.assertEqual(summary.loc["A", "top_streak"], 3)
        self.assertEqual(summary.loc["A", "rank_improvement"], 4)
        self.assertEqual(summary.loc["A", "turnover_growth_pct"], 100.0)
        self.assertEqual(summary.loc["A", "persistence_status"], "持续升温")


if __name__ == "__main__":
    unittest.main()
