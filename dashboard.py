"""Read-only data aggregation for the Watchman web dashboard."""

from __future__ import annotations

import csv
import math
import re
import sqlite3
from datetime import datetime
from numbers import Real
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from alert_store import load_alert_dashboard, load_alert_delivery_health
from momentum_history_store import (
    load_momentum_history,
    load_momentum_trajectory,
    summarize_momentum_changes,
)
from option_history_store import load_option_history, summarize_option_changes

SHANGHAI = ZoneInfo("Asia/Shanghai")
DATA_FILES = {
    "intraday": Path("output/intraday_latest.csv"),
    "options": Path("output/options_latest.csv"),
    "momentum": Path("output/momentum_latest.csv"),
    "sectors": Path("output/sector_momentum_latest.csv"),
}
SCHEDULER_DB = Path("output/scheduler/runs.db")
MOMENTUM_HISTORY_DB = Path("output/history/momentum.db")
OPTION_HISTORY_DB = Path("output/history/options.db")
ALERT_DB = Path("output/alerts/alerts.db")
_DASHBOARD_ALERT_FIELDS = (
    "id",
    "source",
    "logical_slot",
    "entity_code",
    "alert_type",
    "severity",
    "title",
    "message",
    "delivery_status",
    "attempts",
    "total_attempts",
    "requeue_count",
    "last_requeued_at",
    "created_at",
)
_DASHBOARD_ALERT_HEALTH_FIELDS = (
    "total",
    "ready",
    "retry_waiting",
    "active_leases",
    "stale_leases",
    "failed",
    "delivered",
    "oldest_undelivered_at",
    "oldest_undelivered_age_seconds",
    "last_delivered_at",
)
_EMPTY_ALERT_HEALTH = {
    field: None if field in {
        "oldest_undelivered_at",
        "oldest_undelivered_age_seconds",
        "last_delivered_at",
    } else 0
    for field in _DASHBOARD_ALERT_HEALTH_FIELDS
}


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


def _product_prefix(code) -> str:
    match = re.fullmatch(r"([A-Za-z]{1,3})6666", str(code).strip())
    if not match:
        raise ValueError("product code must be 1-3 letters followed by 6666")
    return match.group(1).lower()


def normalize_product_code(code) -> str:
    """Validate and return the canonical lowercase product-index code."""
    return f"{_product_prefix(code)}6666"


def _belongs_to_product(code, prefix: str) -> bool:
    match = re.match(r"([A-Za-z]{1,3})", str(code or ""))
    return bool(match and match.group(1).lower() == prefix)


def build_product_detail(root: Path, code, trajectory_limit: int = 60) -> dict:
    """Build one bounded, JSON-safe product drill-down from read-only sources."""
    root = Path(root).resolve()
    canonical = normalize_product_code(code)
    prefix = _product_prefix(canonical)
    momentum, _ = _load_csv(root / DATA_FILES["momentum"])
    intraday, _ = _load_csv(root / DATA_FILES["intraday"])
    options, _ = _load_csv(root / DATA_FILES["options"])
    current = next(
        (row for row in momentum if str(row.get("code", "")).lower() == canonical),
        None,
    )
    trajectory = load_momentum_trajectory(
        root / MOMENTUM_HISTORY_DB, canonical, limit=trajectory_limit
    )
    return {
        "code": canonical,
        "current": current,
        "momentum_trajectory": _dataframe_records(trajectory),
        "intraday": [
            row for row in intraday if _belongs_to_product(row.get("code"), prefix)
        ],
        "options": [
            row
            for row in options
            if _belongs_to_product(row.get("underlying"), prefix)
        ],
    }


def _dataframe_records(frame: pd.DataFrame) -> list[dict]:
    records = []
    for row in frame.to_dict("records"):
        item = {}
        for key, value in row.items():
            if value is None or pd.isna(value):
                item[key] = None
            elif isinstance(value, pd.Timestamp):
                item[key] = (
                    value.isoformat(timespec="seconds")
                    if key in {"scan_time", "bar_time", "ma_cross_time", "macd_cross_time"}
                    else value.date().isoformat()
                )
            else:
                scalar = value.item() if hasattr(value, "item") else value
                item[key] = (
                    None
                    if isinstance(scalar, Real) and not math.isfinite(float(scalar))
                    else scalar
                )
        records.append(item)
    return records


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


def _read_alerts(
    path: Path, limit: int = 100
) -> tuple[list[dict], dict[str, int], str | None]:
    empty_counts = {"total": 0, "pending": 0, "delivered": 0, "failed": 0}
    try:
        rows, counts = load_alert_dashboard(path, limit=limit)
        records = _dataframe_records(rows)
        public_records = [
            {field: record.get(field) for field in _DASHBOARD_ALERT_FIELDS}
            for record in records
        ]
        return public_records, counts, None
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return [], empty_counts, "告警数据库损坏或暂时无法读取"


def _read_alert_health(path: Path, now: datetime) -> tuple[dict, str | None]:
    try:
        health = load_alert_delivery_health(path, now=now)
        return {
            field: health[field] for field in _DASHBOARD_ALERT_HEALTH_FIELDS
        }, None
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return dict(_EMPTY_ALERT_HEALTH), "告警健康指标暂时无法读取"


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
    alerts, alert_counts, alert_error = _read_alerts(root / ALERT_DB)
    alert_health, alert_health_error = _read_alert_health(root / ALERT_DB, current)
    momentum_history = _dataframe_records(summarize_momentum_changes(
        load_momentum_history(root / MOMENTUM_HISTORY_DB)
    ))
    option_history = _dataframe_records(summarize_option_changes(
        load_option_history(root / OPTION_HISTORY_DB)
    ))
    files = {
        name: _file_status(
            root, DATA_FILES[name], len(rows), current, data_errors[name]
        )
        for name, rows in datasets.items()
    }
    alert_source_error = alert_error or alert_health_error
    files["alerts"] = _file_status(
        root, ALERT_DB, alert_counts["total"], current, alert_source_error
    )
    failed_tasks = sum(row.get("status") == "failed" for row in tasks)
    total_rows = sum(len(rows) for rows in datasets.values()) + alert_counts["total"]
    has_data_errors = any(data_errors.values()) or alert_source_error is not None
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
            "sector_count": len(datasets["sectors"]),
            "momentum_history_count": len(momentum_history),
            "option_history_count": len(option_history),
            "alert_count": alert_counts["total"],
            "pending_alert_count": alert_counts["pending"],
            "delivered_alert_count": alert_counts["delivered"],
            "failed_alert_count": alert_counts["failed"],
            "failed_tasks": failed_tasks,
        },
        "files": files,
        "tasks": tasks,
        "alerts": alerts,
        "alert_health": alert_health,
        "momentum_history": momentum_history,
        "option_history": option_history,
        **datasets,
    }
