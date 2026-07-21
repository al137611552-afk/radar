"""Unified automatic scheduler, status, and manual backfill CLI."""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scheduler import RunStore, TaskRunner, due_slots

SHANGHAI = ZoneInfo("Asia/Shanghai")
TASKS = ("intraday", "options", "momentum")
DEFAULT_DB = Path("output/scheduler/runs.db")
DEFAULT_HISTORY_DB = Path("output/history/radar.db")
DEFAULT_LOG_DIR = Path("output/logs")


class CommandTask:
    """Execute one task without a shell and append captured output to a log."""

    def __init__(self, command, cwd: Path, log_path: Path, timeout: int = 300):
        self.command = [str(part) for part in command]
        self.cwd = Path(cwd)
        self.log_path = Path(log_path)
        self.timeout = timeout

    def __call__(self, slot: str) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        started = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        env = os.environ.copy()
        env["WATCHMAN_LOGICAL_SLOT"] = slot
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{started}] slot={slot} {' '.join(self.command)}\n")
            handle.flush()
            process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=env,
                text=True,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            try:
                return_code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if process.poll() is None:
                        process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                handle.write(f"[timeout after {self.timeout}s]\n")
                handle.flush()
                raise TimeoutError(
                    f"task timed out after {self.timeout}s"
                ) from exc
            handle.write(f"[exit={return_code}]\n")
            handle.flush()
            return return_code


def load_holidays(path: Path | None) -> set[str]:
    if path is None:
        return set()
    holidays = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid holiday at line {line_number}: {value}") from exc
        holidays.add(parsed.isoformat())
    return holidays


def build_handlers(project_dir: Path, log_dir: Path, timeout: int = 300):
    python = Path(sys.executable)
    return {
        "intraday": CommandTask(
            [
                python, "intraday_cli.py", "--top", "15",
                "--state-file", "output/state/intraday_rank.json",
                "--csv", "output/intraday_latest.csv",
                "--history-db", "output/history/radar.db",
            ],
            project_dir, log_dir / "intraday.log", timeout,
        ),
        "options": CommandTask(
            [
                python, "option_cli.py", "--mode", "double", "--new-only",
                "--state-file", "output/state/options.json",
                "--snapshot-csv", "output/options_candidates_latest.csv",
                "--filtered-csv", "output/options_latest.csv",
                "--csv", "output/options_alerts.csv",
            ],
            project_dir, log_dir / "options.log", timeout,
        ),
        "momentum": CommandTask(
            [
                python, "momentum_cli.py", "--top", "20",
                "--csv", "output/momentum_latest.csv",
                "--sector-csv", "output/sector_momentum_latest.csv",
            ],
            project_dir, log_dir / "momentum.log", timeout,
        ),
    }


def snapshot_stats(path: Path) -> dict[str, int | str | None]:
    if not path.exists():
        return {"rows": 0, "snapshots": 0, "contracts": 0, "latest": None}
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                """SELECT COUNT(*), COUNT(DISTINCT scan_time), COUNT(DISTINCT code),
                          MAX(scan_time) FROM intraday_snapshots"""
            ).fetchone()
    except sqlite3.Error:
        return {"rows": 0, "snapshots": 0, "contracts": 0, "latest": None}
    return {"rows": row[0], "snapshots": row[1], "contracts": row[2], "latest": row[3]}


def _positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _slot(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("slot must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI).isoformat(timespec="seconds")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watchman交易时段感知自动调度器")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--history-db", type=Path, default=DEFAULT_HISTORY_DB)
    parser.add_argument("--holidays-file", type=Path)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="运行到期任务")
    run.add_argument("--once", action="store_true", help="仅检查一次后退出")
    run.add_argument("--poll-seconds", type=_positive_int, default=30)
    run.add_argument("--max-attempts", type=_positive_int, default=3)
    run.add_argument("--timeout", type=_positive_int, default=300)
    run.add_argument(
        "--stale-after", type=_positive_int,
        help="运行锁陈旧秒数；默认比任务超时多60秒",
    )

    subparsers.add_parser("status", help="查看最近运行和历史快照状态")

    backfill = subparsers.add_parser("backfill", help="手工补跑一个逻辑时点")
    backfill.add_argument("task", choices=TASKS)
    backfill.add_argument("slot", type=_slot)
    backfill.add_argument("--force", action="store_true")
    backfill.add_argument("--timeout", type=_positive_int, default=300)
    backfill.add_argument(
        "--stale-after", type=_positive_int,
        help="运行锁陈旧秒数；默认比任务超时多60秒",
    )
    args = parser.parse_args(argv)
    if args.command != "status":
        if args.stale_after is None:
            args.stale_after = args.timeout + 60
        if args.stale_after <= args.timeout:
            parser.error("--stale-after must be greater than --timeout")
    return args


def _print_results(results):
    if not results:
        print("当前没有到期任务。")
        return
    for task, outcome in results.items():
        print(f"{task}: {outcome}")


def _result_exit_code(results):
    failures = {"failed", "unavailable", "exhausted"}
    return 1 if any(outcome in failures for outcome in results.values()) else 0


def _print_status(
    store: RunStore, history_db: Path, now: datetime, holidays: set[str]
):
    rows = store.status()
    if not rows:
        print("尚无任务运行记录。")
    for row in rows:
        success = row.get("last_success_at") or "—"
        error = f" error={row['error']}" if row.get("error") else ""
        print(
            f"{row['task']}: {row['status']} slot={row['slot']} "
            f"attempt={row['attempt']} last_success={success}{error}"
        )
    successful = store.successful_slots()
    current_due = due_slots(now, holidays=holidays)
    for task, due in current_due.items():
        if successful.get(task) != due:
            print(f"ALERT {task}: due={due} has no successful run")
    stats = snapshot_stats(history_db)
    expected_snapshot = current_due.get("intraday")
    if expected_snapshot and stats["latest"] != expected_snapshot:
        print(
            f"ALERT intraday_snapshot: expected={expected_snapshot} "
            f"latest={stats['latest'] or '—'}"
        )
    print(
        "intraday_history: "
        f"snapshots={stats['snapshots']} rows={stats['rows']} "
        f"contracts={stats['contracts']} latest={stats['latest'] or '—'}"
    )


def main(argv=None, now: datetime | None = None, handlers=None):
    args = parse_args(argv)
    try:
        holidays = load_holidays(args.holidays_file)
    except (OSError, ValueError) as exc:
        print(f"节假日配置错误：{exc}", file=sys.stderr)
        return 2
    store = RunStore(args.db)

    if args.command == "status":
        _print_status(
            store, args.history_db, now or datetime.now(SHANGHAI), holidays
        )
        return 0

    task_handlers = handlers if handlers is not None else build_handlers(
        args.project_dir, args.log_dir, timeout=args.timeout
    )
    max_attempts = getattr(args, "max_attempts", 3)
    runner = TaskRunner(
        store,
        task_handlers,
        max_attempts=max_attempts,
        stale_after_seconds=args.stale_after,
    )

    if args.command == "backfill":
        results = runner.run_due({args.task: args.slot}, force=args.force)
        _print_results(results)
        return _result_exit_code(results)

    while True:
        current = now or datetime.now(SHANGHAI)
        store.recover_stale_runs(stale_after_seconds=args.stale_after)
        pending_results = runner.run_due(
            store.retryable_slots(max_attempts=max_attempts)
        )
        current_results = runner.run_due(due_slots(current, holidays=holidays))
        if pending_results:
            print("重试任务：")
            _print_results(pending_results)
        _print_results(current_results)
        if args.once or now is not None:
            return max(
                _result_exit_code(pending_results),
                _result_exit_code(current_results),
            )
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
