import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scheduler  # noqa: E402
import scheduler_cli  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")


class SchedulerCliTests(unittest.TestCase):
    def test_handlers_publish_all_dashboard_snapshot_paths(self):
        handlers = scheduler_cli.build_handlers(Path("/project"), Path("/logs"))

        self.assertIn("output/intraday_latest.csv", handlers["intraday"].command)
        option_command = handlers["options"].command
        self.assertIn("output/options_latest.csv", option_command)
        self.assertEqual(
            option_command[option_command.index("--snapshot-csv") + 1],
            "output/options_candidates_latest.csv",
        )
        self.assertEqual(
            option_command[option_command.index("--filtered-csv") + 1],
            "output/options_latest.csv",
        )
        self.assertIn("output/momentum_latest.csv", handlers["momentum"].command)
        self.assertIn("output/sector_momentum_latest.csv", handlers["momentum"].command)
        self.assertEqual(handlers["momentum"].command[0], sys.executable)

    def test_once_executes_due_task_and_status_reports_success(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "scheduler.db"
            output = io.StringIO()
            with redirect_stdout(output):
                code = scheduler_cli.main(
                    ["--db", str(db), "run", "--once"],
                    now=datetime(2026, 7, 15, 9, 6, tzinfo=SH),
                    handlers={"intraday": lambda slot: calls.append(slot)},
                )

            self.assertEqual(code, 0)
            self.assertEqual(calls, ["2026-07-15T09:05:00+08:00"])
            self.assertIn("intraday: success", output.getvalue())
            self.assertEqual(scheduler.RunStore(db).status()[0]["status"], "success")

    def test_status_alerts_when_due_slot_has_no_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                code = scheduler_cli.main(
                    ["--db", str(Path(directory) / "runs.db"), "status"],
                    now=datetime(2026, 7, 15, 9, 6, tzinfo=SH),
                )

            self.assertEqual(code, 0)
            self.assertIn(
                "ALERT intraday: due=2026-07-15T09:05:00+08:00 has no successful run",
                output.getvalue(),
            )

    def test_status_alerts_when_successful_task_did_not_advance_snapshot(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_db = root / "runs.db"
            history_db = root / "history.db"
            store = scheduler.RunStore(run_db)
            run_id = store.claim("intraday", "2026-07-15T09:05:00+08:00")
            store.finish(run_id, success=True)
            with sqlite3.connect(history_db) as connection:
                connection.execute(
                    "CREATE TABLE intraday_snapshots(scan_time TEXT, code TEXT)"
                )
                connection.execute(
                    "INSERT INTO intraday_snapshots VALUES (?, ?)",
                    ("2026-07-15T09:00:00+08:00", "rb2610"),
                )
            output = io.StringIO()

            with redirect_stdout(output):
                scheduler_cli.main(
                    ["--db", str(run_db), "--history-db", str(history_db), "status"],
                    now=datetime(2026, 7, 15, 9, 6, tzinfo=SH),
                )

            self.assertIn(
                "ALERT intraday_snapshot: expected=2026-07-15T09:05:00+08:00 "
                "latest=2026-07-15T09:00:00+08:00",
                output.getvalue(),
            )

    def test_stale_after_must_exceed_task_timeout(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            scheduler_cli.main(
                [
                    "run", "--timeout", "60",
                    "--stale-after", "60", "--once",
                ],
                now=datetime(2026, 7, 15, 9, 6, tzinfo=SH),
                handlers={},
            )

        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--stale-after must be greater than --timeout", stderr.getvalue())

    def test_failed_once_returns_nonzero(self):
        def fail(_slot):
            raise RuntimeError("network down")

        with tempfile.TemporaryDirectory() as directory:
            code = scheduler_cli.main(
                ["--db", str(Path(directory) / "runs.db"), "run", "--once"],
                now=datetime(2026, 7, 15, 9, 6, tzinfo=SH),
                handlers={"intraday": fail},
            )

            self.assertEqual(code, 1)

    def test_next_poll_retries_failed_older_slot_before_current_slot(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "runs.db"

            def fail(slot):
                calls.append(slot)
                raise RuntimeError("temporary")

            scheduler_cli.main(
                ["--db", str(db), "run", "--once"],
                now=datetime(2026, 7, 15, 9, 6, tzinfo=SH),
                handlers={"intraday": fail},
            )
            scheduler_cli.main(
                ["--db", str(db), "run", "--once"],
                now=datetime(2026, 7, 15, 9, 11, tzinfo=SH),
                handlers={"intraday": lambda slot: calls.append(slot)},
            )

            self.assertEqual(calls, [
                "2026-07-15T09:05:00+08:00",
                "2026-07-15T09:05:00+08:00",
                "2026-07-15T09:10:00+08:00",
            ])

    def test_backfill_requires_force_to_repeat_successful_slot(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "scheduler.db"
            argv = [
                "--db", str(db), "backfill", "intraday",
                "2026-07-15T09:05:00+08:00",
            ]
            first = scheduler_cli.main(argv, handlers={"intraday": lambda slot: calls.append(slot)})
            second = scheduler_cli.main(argv, handlers={"intraday": lambda slot: calls.append(slot)})
            forced = scheduler_cli.main(
                argv + ["--force"], handlers={"intraday": lambda slot: calls.append(slot)}
            )

            self.assertEqual((first, second, forced), (0, 0, 0))
            self.assertEqual(len(calls), 2)

    def test_holiday_file_accepts_comments_and_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "holidays.txt"
            path.write_text("# exchange closure\n2026-10-01\n\n2026-10-02\n", encoding="utf-8")

            holidays = scheduler_cli.load_holidays(path)

            self.assertEqual(holidays, {"2026-10-01", "2026-10-02"})

    def test_command_timeout_kills_spawned_process_group(self):
        import os
        import signal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "child.pid"
            code = (
                "import pathlib, subprocess, time; "
                "p=subprocess.Popen(['sleep','30']); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); "
                "time.sleep(30)"
            )
            task = scheduler_cli.CommandTask(
                [sys.executable, "-c", code],
                cwd=ROOT,
                log_path=root / "timeout.log",
                timeout=1,
            )

            with self.assertRaises(TimeoutError):
                task("2026-07-15T09:05:00+08:00")

            child_pid = int(pid_file.read_text())
            proc_stat = Path(f"/proc/{child_pid}/stat")
            try:
                child_state = proc_stat.read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                child_alive = False
            else:
                child_alive = child_state != "Z"
            if child_alive:
                os.kill(child_pid, signal.SIGKILL)
            self.assertFalse(child_alive)
            self.assertIn("timeout", (root / "timeout.log").read_text())

    def test_command_task_writes_stdout_and_stderr_to_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "task.log"
            task = scheduler_cli.CommandTask(
                [sys.executable, "-c", "import sys; print('ok'); print('warn', file=sys.stderr)"],
                cwd=ROOT,
                log_path=log,
                timeout=10,
            )

            code = task("2026-07-15T09:05:00+08:00")

            self.assertEqual(code, 0)
            text = log.read_text(encoding="utf-8")
            self.assertIn("ok", text)
            self.assertIn("warn", text)
            self.assertIn("slot=2026-07-15T09:05:00+08:00", text)


if __name__ == "__main__":
    unittest.main()
