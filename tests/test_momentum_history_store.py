import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from momentum_history_store import (  # noqa: E402
    load_momentum_history,
    load_momentum_trajectory,
    save_momentum_snapshot,
    summarize_momentum_changes,
)


class MomentumHistoryStoreTests(unittest.TestCase):
    def _snapshot(self, codes=("au6666", "ag6666"), as_of="2026-07-20"):
        rows = []
        for index, code in enumerate(codes, start=1):
            rows.append({
                "code": code,
                "name": code,
                "exchange": "SHFE",
                "sector": "贵金属",
                "as_of": pd.Timestamp(as_of),
                "momentum_score": 100.0 - index,
                "long_rank": index,
                "short_rank": len(codes) - index + 1,
                "risk_adjusted_score": 90.0 - index,
                "risk_long_rank": index,
                "risk_short_rank": len(codes) - index + 1,
                "volatility_score": 50.0 + index,
                "volatility_risk": "常态",
            })
        return pd.DataFrame(rows)

    def test_same_date_is_idempotent_and_smaller_retry_replaces_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            full = self._snapshot()

            self.assertEqual(save_momentum_snapshot(path, full), 2)
            self.assertEqual(save_momentum_snapshot(path, full), 2)
            self.assertEqual(save_momentum_snapshot(path, full.iloc[:1]), 1)

            history = load_momentum_history(path)
            self.assertEqual(history["code"].tolist(), ["au6666"])
            with sqlite3.connect(path) as connection:
                duplicate_groups = connection.execute(
                    "SELECT COUNT(*) FROM ("
                    "SELECT snapshot_date, code, COUNT(*) AS n "
                    "FROM momentum_snapshots GROUP BY snapshot_date, code HAVING n > 1)"
                ).fetchone()[0]
            self.assertEqual(duplicate_groups, 0)

    def test_uses_latest_row_cutoff_as_snapshot_identity_without_losing_row_cutoffs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            snapshot = self._snapshot()
            snapshot.loc[0, "as_of"] = pd.Timestamp("2026-07-18")

            save_momentum_snapshot(path, snapshot)

            history = load_momentum_history(path)
            self.assertEqual(
                history["snapshot_date"].dt.strftime("%Y-%m-%d").unique().tolist(),
                ["2026-07-20"],
            )
            self.assertEqual(
                sorted(history["as_of"].dt.strftime("%Y-%m-%d").tolist()),
                ["2026-07-18", "2026-07-20"],
            )

    def test_rejects_duplicate_codes_before_replacing_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            original = self._snapshot()
            save_momentum_snapshot(path, original)
            duplicate = pd.concat([original.iloc[:1], original.iloc[:1]], ignore_index=True)

            with self.assertRaisesRegex(ValueError, "duplicate code"):
                save_momentum_snapshot(path, duplicate)

            history = load_momentum_history(path)
            self.assertEqual(history["code"].tolist(), ["au6666", "ag6666"])

    def test_loads_one_product_trajectory_in_chronological_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            first = self._snapshot(as_of="2026-07-18")
            second = self._snapshot(as_of="2026-07-20")
            second.loc[second["code"].eq("au6666"), "long_rank"] = 2
            save_momentum_snapshot(path, first)
            save_momentum_snapshot(path, second)

            trajectory = load_momentum_trajectory(path, "au6666", limit=10)

            self.assertEqual(
                trajectory["snapshot_date"].dt.strftime("%Y-%m-%d").tolist(),
                ["2026-07-18", "2026-07-20"],
            )
            self.assertEqual(trajectory["long_rank"].tolist(), [1, 2])

    def test_rejects_boolean_or_fractional_ranks_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            for invalid in (True, 1.5):
                snapshot = self._snapshot()
                snapshot["long_rank"] = snapshot["long_rank"].astype(object)
                snapshot.loc[0, "long_rank"] = invalid
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    ValueError, "long_rank must contain positive integers"
                ):
                    save_momentum_snapshot(path, snapshot)

    def test_summarizes_latest_rank_changes_and_new_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            save_momentum_snapshot(
                path, self._snapshot(("au6666", "ag6666"), "2026-07-18")
            )
            latest = self._snapshot(("au6666", "cu6666"), "2026-07-20")
            latest.loc[
                latest["code"].eq("au6666"), ["long_rank", "risk_long_rank"]
            ] = 2
            save_momentum_snapshot(path, latest)

            changes = summarize_momentum_changes(
                load_momentum_history(path), top_n=2
            ).set_index("code")

            self.assertEqual(changes.loc["au6666", "long_rank_change"], -1)
            self.assertFalse(changes.loc["au6666", "new_long_entry"])
            self.assertTrue(changes.loc["cu6666", "new_long_entry"])
            self.assertTrue(pd.isna(changes.loc["cu6666", "previous_long_rank"]))

    def test_validates_required_finite_and_nonblank_snapshot_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "momentum.db"
            missing = self._snapshot().drop(columns=["momentum_score"])
            with self.assertRaisesRegex(ValueError, "missing required column"):
                save_momentum_snapshot(path, missing)

            nonfinite = self._snapshot()
            nonfinite.loc[0, "risk_adjusted_score"] = float("inf")
            with self.assertRaisesRegex(ValueError, "finite numbers"):
                save_momentum_snapshot(path, nonfinite)

            blank = self._snapshot()
            blank.loc[0, "code"] = "  "
            with self.assertRaisesRegex(ValueError, "blank"):
                save_momentum_snapshot(path, blank)


if __name__ == "__main__":
    unittest.main()
