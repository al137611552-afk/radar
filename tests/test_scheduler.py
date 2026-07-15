import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scheduler  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")


class ScheduleSlotTests(unittest.TestCase):
    def test_intraday_uses_latest_completed_five_minute_slot(self):
        now = datetime(2026, 7, 15, 9, 6, tzinfo=SH)

        slots = scheduler.due_slots(now)

        self.assertEqual(slots["intraday"], "2026-07-15T09:05:00+08:00")

    def test_midday_break_does_not_create_new_intraday_slot(self):
        now = datetime(2026, 7, 15, 10, 25, tzinfo=SH)

        slots = scheduler.due_slots(now)

        self.assertEqual(slots["intraday"], "2026-07-15T10:15:00+08:00")

    def test_option_runs_after_completed_clock_hour(self):
        now = datetime(2026, 7, 15, 11, 1, tzinfo=SH)

        slots = scheduler.due_slots(now)

        self.assertEqual(slots["options"], "2026-07-15T11:00:00+08:00")

    def test_momentum_becomes_due_after_day_close(self):
        before = scheduler.due_slots(datetime(2026, 7, 15, 14, 59, tzinfo=SH))
        after = scheduler.due_slots(datetime(2026, 7, 15, 15, 5, tzinfo=SH))

        self.assertNotIn("momentum", before)
        self.assertEqual(after["momentum"], "2026-07-15T15:00:00+08:00")

    def test_friday_night_is_skipped(self):
        slots = scheduler.due_slots(datetime(2026, 7, 17, 21, 6, tzinfo=SH))

        self.assertNotIn("intraday", slots)
        self.assertNotIn("options", slots)

    def test_early_morning_continues_previous_weekday_night(self):
        slots = scheduler.due_slots(datetime(2026, 7, 16, 0, 6, tzinfo=SH))

        self.assertEqual(slots["intraday"], "2026-07-16T00:05:00+08:00")

    def test_early_morning_after_holiday_does_not_invent_night_session(self):
        for now in (
            datetime(2026, 10, 9, 0, 6, tzinfo=SH),
            datetime(2026, 10, 9, 2, 31, tzinfo=SH),
            datetime(2026, 10, 9, 9, 1, tzinfo=SH),
        ):
            with self.subTest(now=now):
                self.assertEqual(
                    scheduler.due_slots(now, holidays={"2026-10-08"}), {}
                )

    def test_holiday_evening_before_reopened_day_is_skipped(self):
        slots = scheduler.due_slots(
            datetime(2026, 10, 8, 21, 6, tzinfo=SH),
            holidays={"2026-10-08"},
        )

        self.assertNotIn("intraday", slots)
        self.assertNotIn("options", slots)

    def test_restart_after_night_close_keeps_final_slot_due(self):
        for now in (
            datetime(2026, 7, 16, 2, 31, tzinfo=SH),
            datetime(2026, 7, 16, 9, 1, tzinfo=SH),
        ):
            with self.subTest(now=now):
                slots = scheduler.due_slots(now)
                self.assertEqual(
                    slots["intraday"], "2026-07-16T02:30:00+08:00"
                )
                self.assertEqual(
                    slots["options"], "2026-07-16T02:00:00+08:00"
                )

    def test_configured_holiday_skips_all_tasks(self):
        slots = scheduler.due_slots(
            datetime(2026, 7, 15, 10, 1, tzinfo=SH),
            holidays={"2026-07-15"},
        )

        self.assertEqual(slots, {})


class RunStoreTests(unittest.TestCase):
    def test_successful_slot_is_not_claimed_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            first = store.claim("intraday", "2026-07-15T09:05:00+08:00")
            store.finish(first, success=True)

            second = store.claim("intraday", "2026-07-15T09:05:00+08:00")

            self.assertIsNone(second)
            self.assertEqual(store.status()[0]["status"], "success")

    def test_stale_running_claim_is_failed_and_reclaimed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.db"
            store = scheduler.RunStore(path)
            first = store.claim("options", "2026-07-15T10:00:00+08:00")
            self.assertIsNotNone(first)
            import sqlite3
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE task_runs SET started_at = ? WHERE id = ?",
                    ("2020-01-01T00:00:00+08:00", first),
                )

            replacement = store.claim(
                "options", "2026-07-15T10:00:00+08:00", stale_after_seconds=60
            )

            self.assertIsNotNone(replacement)
            self.assertNotEqual(first, replacement)
            history = store.history()
            stale = next(row for row in history if row["id"] == first)
            self.assertEqual(stale["status"], "failed")
            self.assertEqual(stale["error"], "stale task lock recovered")

    def test_failed_latest_status_retains_last_success_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            slot = "2026-07-15T09:05:00+08:00"
            first = store.claim("intraday", slot)
            store.finish(first, success=True)
            second = store.claim("intraday", slot, force=True)
            store.finish(second, success=False, error="late API failure")

            status = store.status()[0]

            self.assertEqual(status["status"], "failed")
            self.assertIsNotNone(status["last_success_at"])

    def test_retryable_slots_include_failed_older_logical_point(self):
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            failed_slot = "2026-07-15T09:05:00+08:00"
            run_id = store.claim("intraday", failed_slot)
            store.finish(run_id, success=False, error="temporary")

            pending = store.retryable_slots(max_attempts=3)

            self.assertEqual(pending, {"intraday": failed_slot})

    def test_global_stale_recovery_finds_old_slot_without_reclaiming_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.db"
            store = scheduler.RunStore(path)
            run_id = store.claim("options", "2026-07-15T10:00:00+08:00")
            import sqlite3
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE task_runs SET started_at = ? WHERE id = ?",
                    ("2020-01-01T00:00:00+08:00", run_id),
                )

            recovered = store.recover_stale_runs(stale_after_seconds=60)

            self.assertEqual(recovered, 1)
            self.assertEqual(store.history()[0]["status"], "failed")
            self.assertEqual(
                store.retryable_slots(max_attempts=3),
                {"options": "2026-07-15T10:00:00+08:00"},
            )

    def test_failed_slot_can_retry_up_to_max_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            first = store.claim("intraday", "2026-07-15T09:05:00+08:00")
            store.finish(first, success=False, error="temporary")

            second = store.claim(
                "intraday", "2026-07-15T09:05:00+08:00", max_attempts=2
            )
            store.finish(second, success=False, error="still failing")
            third = store.claim(
                "intraday", "2026-07-15T09:05:00+08:00", max_attempts=2
            )

            self.assertIsNotNone(second)
            self.assertIsNone(third)
            self.assertEqual(store.status()[0]["attempt"], 2)
            self.assertEqual(store.status()[0]["error"], "still failing")

    def test_force_claim_allows_manual_rerun_of_successful_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            first = store.claim("momentum", "2026-07-15T15:00:00+08:00")
            store.finish(first, success=True)

            forced = store.claim(
                "momentum", "2026-07-15T15:00:00+08:00", force=True
            )

            self.assertIsNotNone(forced)


class TaskRunnerTests(unittest.TestCase):
    def test_run_due_records_success_and_deduplicates_slot(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            runner = scheduler.TaskRunner(store, {"intraday": lambda slot: calls.append(slot)})
            slots = {"intraday": "2026-07-15T09:05:00+08:00"}

            first = runner.run_due(slots)
            second = runner.run_due(slots)

            self.assertEqual(first, {"intraday": "success"})
            self.assertEqual(second, {"intraday": "skipped"})
            self.assertEqual(calls, ["2026-07-15T09:05:00+08:00"])

    def test_long_running_task_is_not_reclaimed_before_configured_stale_limit(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            slot = "2026-07-15T09:05:00+08:00"
            self.assertIsNotNone(store.claim("intraday", slot))
            old_started = (
                datetime.now(SH) - timedelta(seconds=1900)
            ).isoformat(timespec="seconds")
            with store._connect() as connection:
                connection.execute(
                    "UPDATE task_runs SET started_at = ? WHERE task = ? AND slot = ?",
                    (old_started, "intraday", slot),
                )

            second_store = scheduler.RunStore(Path(directory) / "runs.db")
            runner = scheduler.TaskRunner(
                second_store,
                {"intraday": lambda value: calls.append(value)},
                stale_after_seconds=3600,
            )
            result = runner.run_due({"intraday": slot})

            self.assertEqual(result, {"intraday": "skipped"})
            self.assertEqual(calls, [])
            self.assertEqual(len(second_store.history()), 1)

    def test_run_due_reports_exhausted_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            slot = "2026-07-15T09:05:00+08:00"
            for _ in range(2):
                run_id = store.claim("intraday", slot, max_attempts=2)
                store.finish(run_id, success=False, error="failed")
            runner = scheduler.TaskRunner(
                store, {"intraday": lambda _slot: 0}, max_attempts=2
            )

            result = runner.run_due({"intraday": slot})

            self.assertEqual(result, {"intraday": "exhausted"})

    def test_run_due_records_failure_without_stopping_other_tasks(self):
        calls = []

        def fail(_slot):
            raise RuntimeError("API unavailable")

        with tempfile.TemporaryDirectory() as directory:
            store = scheduler.RunStore(Path(directory) / "runs.db")
            runner = scheduler.TaskRunner(
                store,
                {"intraday": fail, "options": lambda slot: calls.append(slot)},
                max_attempts=2,
            )

            result = runner.run_due({
                "intraday": "2026-07-15T10:00:00+08:00",
                "options": "2026-07-15T10:00:00+08:00",
            })

            self.assertEqual(result["intraday"], "failed")
            self.assertEqual(result["options"], "success")
            self.assertEqual(len(calls), 1)
            failures = [row for row in store.history() if row["task"] == "intraday"]
            self.assertEqual(failures[0]["error"], "API unavailable")


if __name__ == "__main__":
    unittest.main()
