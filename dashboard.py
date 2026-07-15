"""Read-only data aggregation for the Watchman web dashboard."""

from __future__ import annotations

import csv
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
DATA_FILES = {
    "intraday": Path("output/intraday_latest.csv"),
    "options": Path("output/options_latest.csv"),
    "momentum": Path("output/momentum_latest.csv"),
}
SCHEDULER_DB = Path("output/scheduler/runs.db")


def _coerce(value: str):
    value = value.strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        number = float(value)
    except ValueError:
        return value
    if not math.isfinite(number):
        return None
    if number.is_integer() and not any(marker in value.lower() for marker in (".", "e")):
        return int(number)
    return number


def _read_csv(path: Path, limit: int = 200) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if not reader.fieldnames:
            return []
        rows = []
        for row in reader:
            if None in row:
                raise ValueError("CSV row has more fields than its header")
            if any(value is None for value in row.values()):
                raise ValueError("CSV row has fewer fields than its header")
            if len(rows) < limit:
                rows.append({
                    key: _coerce(value or "") for key, value in row.items()
                })
    return rows


def _load_csv(path: Path) -> tuple[list[dict], str | None]:
    try:
        return _read_csv(path), None
    except (OSError, UnicodeError, csv.Error, ValueError, AttributeError):
        return [], "CSV文件损坏或暂时无法读取"


def _redact_error(error):
    if not error:
        return error
    # Never expose upstream error text to browsers: it may contain credentials in
    # formats that cannot be safely covered by a finite collection of regexes.
    return "任务失败详情已隐藏 [REDACTED]"


def _read_tasks(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        uri = f"file:{path.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT current.task, current.slot, current.attempt,
                          current.status, current.started_at, current.finished_at,
                          current.error,
                          (SELECT MAX(success.finished_at)
                           FROM task_runs AS success
                           WHERE success.task = current.task
                             AND success.status = 'success') AS last_success_at
                   FROM task_runs AS current
                   JOIN (SELECT task, MAX(id) AS id FROM task_runs GROUP BY task) latest
                     ON latest.id = current.id
                   ORDER BY current.task"""
            ).fetchall()
    except sqlite3.Error:
        return []
    result = []
    for row in rows:
        item = dict(row)
        item["error"] = _redact_error(item.get("error"))
        result.append(item)
    return result


def _file_status(
    root: Path,
    relative: Path,
    row_count: int,
    now: datetime,
    error: str | None = None,
) -> dict:
    path = root / relative
    missing = {
        "path": str(relative), "exists": False, "available": False, "rows": 0,
        "modified_at": None, "age_seconds": None, "error": error,
    }
    try:
        if not path.is_file():
            return missing
        stat_result = path.stat()
    except OSError:
        missing["error"] = error or "文件已移除或暂时无法读取元数据"
        return missing
    modified = datetime.fromtimestamp(stat_result.st_mtime, tz=SHANGHAI)
    return {
        "path": str(relative),
        "exists": True,
        "available": error is None,
        "rows": row_count,
        "modified_at": modified.isoformat(timespec="seconds"),
        "age_seconds": max(0, int((now - modified).total_seconds())),
        "error": error,
    }


def build_dashboard_payload(root: Path, now: datetime | None = None) -> dict:
    """Build one JSON-safe snapshot without mutating runtime data."""
    root = Path(root).resolve()
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    datasets = {}
    data_errors = {}
    for name, relative in DATA_FILES.items():
        datasets[name], data_errors[name] = _load_csv(root / relative)
    tasks = _read_tasks(root / SCHEDULER_DB)
    files = {
        name: _file_status(
            root, DATA_FILES[name], len(rows), current, data_errors[name]
        )
        for name, rows in datasets.items()
    }
    failed_tasks = sum(row.get("status") == "failed" for row in tasks)
    total_rows = sum(len(rows) for rows in datasets.values())
    has_data_errors = any(data_errors.values())
    health = (
        "degraded" if failed_tasks or has_data_errors
        else ("healthy" if total_rows else "waiting")
    )
    return {
        "generated_at": current.isoformat(timespec="seconds"),
        "summary": {
            "health": health,
            "intraday_count": len(datasets["intraday"]),
            "option_count": len(datasets["options"]),
            "momentum_count": len(datasets["momentum"]),
            "failed_tasks": failed_tasks,
        },
        "files": files,
        "tasks": tasks,
        **datasets,
    }
