"""Trading-session-aware scheduling primitives and durable task run state."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY_SESSIONS = (
    (time(9, 0), time(10, 15)),
    (time(10, 30), time(11, 30)),
    (time(13, 30), time(15, 0)),
)


def _normalize_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=SHANGHAI)
    return now.astimezone(SHANGHAI)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _floor_five_minutes(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 5) * 5, second=0, microsecond=0)


def _is_holiday(day: date, holidays: set[str]) -> bool:
    return day.isoformat() in holidays


def _latest_intraday_slot(now: datetime, holidays: set[str]) -> datetime | None:
    local_time = now.timetz().replace(tzinfo=None)
    day = now.date()

    previous_day = day - timedelta(days=1)
    if time(0, 0) <= local_time <= time(2, 30):
        if (
            day.weekday() not in (1, 2, 3, 4)
            or _is_holiday(day, holidays)
            or _is_holiday(previous_day, holidays)
        ):
            return None
        start = datetime.combine(day, time(0, 0), SHANGHAI)
        slot = _floor_five_minutes(now)
        return slot if slot >= start + timedelta(minutes=5) else None

    if time(2, 30) < local_time < time(9, 5):
        if (
            day.weekday() not in (1, 2, 3, 4)
            or _is_holiday(day, holidays)
            or _is_holiday(previous_day, holidays)
        ):
            return None
        return datetime.combine(day, time(2, 30), SHANGHAI)

    if time(21, 0) <= local_time <= time(23, 59, 59):
        trading_day = day + timedelta(days=1)
        if (
            day.weekday() not in (0, 1, 2, 3)
            or _is_holiday(day, holidays)
            or _is_holiday(trading_day, holidays)
        ):
            return None
        start = datetime.combine(day, time(21, 0), SHANGHAI)
        slot = _floor_five_minutes(now)
        return slot if slot >= start + timedelta(minutes=5) else None

    if day.weekday() >= 5 or _is_holiday(day, holidays):
        return None

    latest = None
    for start_time, end_time in DAY_SESSIONS:
        start = datetime.combine(day, start_time, SHANGHAI)
        end = datetime.combine(day, end_time, SHANGHAI)
        if now <= start:
            break
        candidate = min(_floor_five_minutes(now), end)
        if candidate >= start + timedelta(minutes=5):
            latest = candidate
        if now <= end:
            break
    return latest


def _latest_option_slot(now: datetime, intraday_slot: datetime) -> datetime | None:
    local_time = now.timetz().replace(tzinfo=None)
    day = now.date()
    candidate = now.replace(minute=0, second=0, microsecond=0)

    if (
        intraday_slot.time() == time(2, 30)
        and local_time < time(9, 5)
    ):
        return datetime.combine(day, time(2, 0), SHANGHAI)
    if time(0, 0) <= local_time <= time(2, 30):
        return candidate
    if time(21, 0) <= local_time:
        return candidate if candidate.hour >= 22 else None
    if candidate.time() < time(10, 0):
        return None
    if candidate.time() > time(15, 0):
        candidate = datetime.combine(day, time(15, 0), SHANGHAI)
    if time(11, 30) < local_time < time(14, 0):
        candidate = datetime.combine(day, time(11, 0), SHANGHAI)
    return candidate if candidate <= intraday_slot else None


def due_slots(now: datetime, holidays: set[str] | None = None) -> dict[str, str]:
    """Return each task's latest due logical slot in Shanghai time."""
    now = _normalize_now(now)
    holidays = holidays or set()
    slots: dict[str, str] = {}
    intraday = _latest_intraday_slot(now, holidays)
    if intraday is not None:
        slots["intraday"] = _iso(intraday)
        option = _latest_option_slot(now, intraday)
        if option is not None:
            slots["options"] = _iso(option)

    day = now.date()
    if (
        day.weekday() < 5
        and not _is_holiday(day, holidays)
        and now.timetz().replace(tzinfo=None) >= time(15, 0)
    ):
        slots["momentum"] = _iso(datetime.combine(day, time(15, 0), SHANGHAI))
    return slots


class RunStore:
    """SQLite-backed task claims, attempts, completion state, and status queries."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT,
                    UNIQUE(task, slot, attempt)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_runs_latest "
                "ON task_runs(task, slot, attempt DESC)"
            )

    def claim(
        self,
        task: str,
        slot: str,
        max_attempts: int = 3,
        force: bool = False,
        stale_after_seconds: int = 1800,
    ) -> int | None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")
        now = _iso(datetime.now(SHANGHAI))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """SELECT id, attempt, status, started_at FROM task_runs
                   WHERE task = ? AND slot = ? ORDER BY attempt DESC LIMIT 1""",
                (task, slot),
            ).fetchone()
            if latest is not None and latest["status"] == "running":
                started = datetime.fromisoformat(latest["started_at"])
                age = datetime.now(SHANGHAI) - started.astimezone(SHANGHAI)
                if age.total_seconds() <= stale_after_seconds:
                    connection.rollback()
                    return None
                connection.execute(
                    """UPDATE task_runs
                       SET status = 'failed', finished_at = ?, error = ?
                       WHERE id = ?""",
                    (now, "stale task lock recovered", latest["id"]),
                )
            if latest is not None and not force and (
                latest["status"] == "success" or latest["attempt"] >= max_attempts
            ):
                connection.rollback()
                return None
            attempt = 1 if latest is None else latest["attempt"] + 1
            cursor = connection.execute(
                """INSERT INTO task_runs(task, slot, attempt, status, started_at)
                   VALUES (?, ?, ?, 'running', ?)""",
                (task, slot, attempt, now),
            )
            connection.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a run id")
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def finish(self, run_id: int, success: bool, error: str | None = None):
        status = "success" if success else "failed"
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE task_runs SET status = ?, finished_at = ?, error = ?
                   WHERE id = ? AND status = 'running'""",
                (status, _iso(datetime.now(SHANGHAI)), error, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"run {run_id} is not active")

    def status(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*,
                       (SELECT MAX(s.finished_at) FROM task_runs s
                        WHERE s.task = r.task AND s.status = 'success') AS last_success_at
                FROM task_runs r
                JOIN (
                    SELECT task, MAX(id) AS id FROM task_runs GROUP BY task
                ) latest ON latest.id = r.id
                ORDER BY r.task
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_stale_runs(self, stale_after_seconds: int = 1800) -> int:
        if stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")
        now = datetime.now(SHANGHAI)
        cutoff = _iso(now - timedelta(seconds=stale_after_seconds))
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE task_runs
                   SET status = 'failed', finished_at = ?, error = ?
                   WHERE status = 'running' AND started_at < ?""",
                (_iso(now), "stale task lock recovered", cutoff),
            )
            return cursor.rowcount

    def retryable_slots(self, max_attempts: int = 3) -> dict[str, str]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.task, r.slot FROM task_runs r
                   WHERE r.status = 'failed' AND r.attempt < ?
                     AND r.id = (
                         SELECT MAX(s.id) FROM task_runs s
                         WHERE s.task = r.task AND s.slot = r.slot
                     )
                   ORDER BY r.slot ASC""",
                (max_attempts,),
            ).fetchall()
        pending = {}
        for row in rows:
            pending.setdefault(row["task"], row["slot"])
        return pending

    def successful_slots(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT task, slot FROM task_runs r
                   WHERE status = 'success' AND id = (
                       SELECT MAX(id) FROM task_runs s
                       WHERE s.task = r.task AND s.status = 'success'
                   )"""
            ).fetchall()
        return {row["task"]: row["slot"] for row in rows}

    def slot_status(self, task: str, slot: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM task_runs WHERE task = ? AND slot = ?
                   ORDER BY attempt DESC LIMIT 1""",
                (task, slot),
            ).fetchone()
        return dict(row) if row is not None else None

    def history(self, limit: int = 100) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


class TaskRunner:
    """Claim due slots, execute injected task handlers, and persist outcomes."""

    def __init__(
        self,
        store: RunStore,
        handlers: dict[str, Callable[[str], int | None]],
        max_attempts: int = 3,
        stale_after_seconds: int = 1800,
    ):
        if stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")
        self.store = store
        self.handlers = handlers
        self.max_attempts = max_attempts
        self.stale_after_seconds = stale_after_seconds

    def run_due(self, slots: dict[str, str], force: bool = False) -> dict[str, str]:
        results = {}
        for task, slot in slots.items():
            handler = self.handlers.get(task)
            if handler is None:
                results[task] = "unavailable"
                continue
            run_id = self.store.claim(
                task,
                slot,
                max_attempts=self.max_attempts,
                force=force,
                stale_after_seconds=self.stale_after_seconds,
            )
            if run_id is None:
                latest = self.store.slot_status(task, slot)
                if (
                    latest is not None
                    and latest["status"] == "failed"
                    and latest["attempt"] >= self.max_attempts
                ):
                    results[task] = "exhausted"
                else:
                    results[task] = "skipped"
                continue
            try:
                exit_code = handler(slot)
                if exit_code not in (None, 0):
                    raise RuntimeError(f"task exited with code {exit_code}")
            except Exception as exc:
                self.store.finish(run_id, success=False, error=str(exc))
                results[task] = "failed"
            else:
                self.store.finish(run_id, success=True)
                results[task] = "success"
        return results
