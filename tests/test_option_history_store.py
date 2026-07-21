import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from option_history_store import (  # noqa: E402
    load_option_history,
    load_option_trajectory,
    save_option_snapshot,
    summarize_option_changes,
)


class OptionHistoryStoreTests(unittest.TestCase):
    def _snapshot(self, scan_time="2026-07-20T10:05:00+08:00", codes=("cuC1", "agC1")):
        rows = []
        for index, code in enumerate(codes):
            rows.append({
                "code": code,
                "name": code,
                "exchange": "SHFE",
                "bar_time": pd.Timestamp("2026-07-20 10:00"),
                "underlying": code[:2] + "2608",
                "option_type": "CALL",
                "dte": 7,
                "expiry": "2026-07-27",
                "strike": 100 + index,
                "last_price": 10 + index,
                "moneyness": 0.01,
                "recent_volume": 1000 + index,
                "open_interest": 500 + index,
                "signal_score": 3 + index,
                "confirmation_score": 4 + index,
                "ma_bullish": True,
                "macd_bullish": bool(index),
                "double_confirmed": index == 0,
                "ma_direction_confirmed": index == 0,
                "macd_direction_confirmed": False,
                "ma_cross_time": pd.Timestamp("2026-07-20 10:00") if index == 0 else None,
                "macd_cross_time": None,
            })
        frame = pd.DataFrame(rows)
        frame.attrs["scan_time"] = scan_time
        return frame

    def test_concurrent_first_writes_initialize_new_database_without_lock_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            original_connect = sqlite3.connect
            first_has_write_lock = threading.Event()
            contender_reached_journal_mode = threading.Event()
            connection_count_lock = threading.Lock()
            connection_count = 0

            class CoordinatedConnection:
                def __init__(self, connection, ordinal):
                    self.connection = connection
                    self.ordinal = ordinal

                def execute(self, statement, *args, **kwargs):
                    if statement.strip().upper() == "PRAGMA JOURNAL_MODE=WAL":
                        if self.ordinal == 1:
                            result = self.connection.execute(statement, *args, **kwargs)
                            self.connection.execute("BEGIN IMMEDIATE")
                            first_has_write_lock.set()
                            contender_reached_journal_mode.wait(timeout=0.5)
                            return result
                        first_has_write_lock.wait(timeout=0.5)
                        contender_reached_journal_mode.set()
                    return self.connection.execute(statement, *args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self.connection, name)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return self.connection.__exit__(*args)

            def coordinated_connect(*args, **kwargs):
                nonlocal connection_count
                kwargs["timeout"] = 0
                connection = original_connect(*args, **kwargs)
                with connection_count_lock:
                    connection_count += 1
                    ordinal = connection_count
                return CoordinatedConnection(connection, ordinal)

            barrier = threading.Barrier(2)

            def save(snapshot):
                barrier.wait()
                return save_option_snapshot(path, snapshot)

            snapshots = (
                self._snapshot(codes=("cuC1",)),
                self._snapshot("2026-07-20T11:05:00+08:00", codes=("agC1",)),
            )
            with patch("option_history_store.sqlite3.connect", side_effect=coordinated_connect):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(save, snapshots))

            self.assertEqual(results, [1, 1])
            self.assertEqual(
                set(load_option_history(path)["code"]),
                {"cuC1", "agC1"},
            )

    def test_same_scan_is_idempotent_and_smaller_retry_replaces_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            full = self._snapshot()

            self.assertEqual(save_option_snapshot(path, full), 2)
            self.assertEqual(save_option_snapshot(path, full), 2)
            self.assertEqual(save_option_snapshot(path, full.iloc[:1], scan_time=full.attrs["scan_time"]), 1)

            history = load_option_history(path)
            self.assertEqual(history["code"].tolist(), ["cuC1"])
            with sqlite3.connect(path) as connection:
                duplicates = connection.execute(
                    "SELECT COUNT(*) FROM (SELECT scan_time, code, COUNT(*) n "
                    "FROM option_snapshots GROUP BY scan_time, code HAVING n > 1)"
                ).fetchone()[0]
            self.assertEqual(duplicates, 0)

    def test_rejects_duplicate_codes_before_replacing_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            original = self._snapshot()
            save_option_snapshot(path, original)
            duplicate = pd.concat([original.iloc[:1], original.iloc[:1]], ignore_index=True)

            with self.assertRaisesRegex(ValueError, "duplicate code"):
                save_option_snapshot(path, duplicate, scan_time=original.attrs["scan_time"])

            self.assertEqual(
                sorted(load_option_history(path)["code"].tolist()), ["agC1", "cuC1"]
            )

    def test_summarizes_new_confirmed_lost_confirmed_and_removed_candidates(self):
        first = self._snapshot()
        second = self._snapshot(
            scan_time="2026-07-20T11:05:00+08:00",
            codes=("cuC1", "znC1"),
        )
        second.loc[second["code"].eq("cuC1"), ["double_confirmed", "ma_direction_confirmed"]] = False
        second.loc[second["code"].eq("znC1"), ["double_confirmed", "ma_direction_confirmed"]] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            save_option_snapshot(path, first)
            save_option_snapshot(path, second)

            changes = summarize_option_changes(load_option_history(path)).set_index("code")

            self.assertEqual(changes.loc["cuC1", "change_status"], "双确认失效")
            self.assertEqual(changes.loc["znC1", "change_status"], "新晋双确认")
            self.assertEqual(changes.loc["agC1", "change_status"], "移出候选")
            self.assertEqual(changes.loc["cuC1", "confirmation_score_change"], 0)

    def test_empty_scan_marks_previous_candidates_as_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            save_option_snapshot(path, self._snapshot())

            self.assertEqual(
                save_option_snapshot(
                    path,
                    pd.DataFrame(),
                    scan_time="2026-07-20T11:05:00+08:00",
                ),
                0,
            )

            changes = summarize_option_changes(load_option_history(path))
            self.assertEqual(set(changes["change_status"]), {"移出候选"})
            self.assertFalse(changes["is_current"].any())
            self.assertEqual(
                changes["scan_time"].dt.strftime("%H:%M").unique().tolist(),
                ["11:05"],
            )

    def test_corrupt_snapshot_or_scan_times_degrade_to_empty_history(self):
        corruptions = {
            "snapshot time": (
                "UPDATE option_snapshots SET bar_time = ?", "not-a-time"
            ),
            "scan registry time": (
                "UPDATE option_scans SET scan_time = ?", "not-a-time"
            ),
        }
        for label, (statement, value) in corruptions.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "options.db"
                save_option_snapshot(path, self._snapshot())
                with sqlite3.connect(path) as connection:
                    connection.execute(statement, (value,))

                history = load_option_history(path)

                self.assertTrue(history.empty)

    def test_load_uses_one_database_snapshot_during_concurrent_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            save_option_snapshot(path, self._snapshot())
            original_read_sql = pd.read_sql_query
            injected = False

            def read_then_write(*args, **kwargs):
                nonlocal injected
                result = original_read_sql(*args, **kwargs)
                if not injected:
                    injected = True
                    save_option_snapshot(
                        path, self._snapshot("2026-07-20T11:05:00+08:00")
                    )
                return result

            with patch(
                "option_history_store.pd.read_sql_query", side_effect=read_then_write
            ):
                history = load_option_history(path)

            self.assertEqual(
                [value.strftime("%H:%M") for value in history.attrs["scan_times"]],
                ["10:05"],
            )

    def test_loads_contract_trajectory_chronologically_and_validates_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            save_option_snapshot(path, self._snapshot())
            save_option_snapshot(path, self._snapshot("2026-07-20T11:05:00+08:00"))

            trajectory = load_option_trajectory(path, "cuC1", limit=10)

            self.assertEqual(
                trajectory["scan_time"].dt.strftime("%H:%M").tolist(),
                ["10:05", "11:05"],
            )
            with self.assertRaisesRegex(ValueError, "positive integer"):
                load_option_trajectory(path, "cuC1", limit=True)

    def test_validates_required_fields_and_finite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "options.db"
            with self.assertRaisesRegex(ValueError, "missing required column"):
                save_option_snapshot(path, self._snapshot().drop(columns=["code"]))
            invalid = self._snapshot()
            invalid["confirmation_score"] = invalid["confirmation_score"].astype(float)
            invalid.loc[0, "confirmation_score"] = float("inf")
            with self.assertRaisesRegex(ValueError, "finite numbers"):
                save_option_snapshot(path, invalid)


if __name__ == "__main__":
    unittest.main()
