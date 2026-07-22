"""Durable SQLite outbox for Watchman operator alerts."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from numbers import Integral, Real
from pathlib import Path

import pandas as pd

_INITIALIZATION_LOCK = threading.Lock()
_REQUIRED_OPTION_COLUMNS = ("code", "alert_type")
_OPTION_PAYLOAD_COLUMNS = (
    "name", "underlying", "option_type", "dte", "confirmation_score"
)
_SEVERITY = {
    "首次命中": "info",
    "新金叉": "info",
    "确认变化": "warning",
    "信号失效": "warning",
}
_DELIVERY_STATUSES = {"pending", "delivered", "failed"}
_SEVERITIES = {"info", "warning", "critical"}
_LOADED_TEXT_COLUMNS = (
    "source",
    "logical_slot",
    "entity_code",
    "alert_type",
    "severity",
    "title",
    "message",
    "payload_json",
    "delivery_status",
    "created_at",
)


def _timestamp(value, name="logical_slot") -> str:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc
    if pd.isna(parsed):
        raise ValueError(f"invalid {name}: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("Asia/Shanghai")
    else:
        parsed = parsed.tz_convert("Asia/Shanghai")
    return parsed.isoformat()


def _text(value, name, limit=200) -> str:
    if value is None or pd.isna(value):
        raise ValueError(f"{name} must not be null or blank")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must not be null or blank")
    if len(result) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return result


def _json_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return _timestamp(value, "payload timestamp")
    return str(value)


def _reject_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _validate_loaded_alert_rows(rows, columns) -> None:
    """Reject semantically corrupt SQLite values before building API records."""
    for raw in rows:
        record = dict(zip(columns, raw))
        for column in _LOADED_TEXT_COLUMNS:
            if not isinstance(record.get(column), str):
                raise ValueError(f"invalid alert {column} type")
        if record["severity"] not in _SEVERITIES:
            raise ValueError("invalid alert severity")
        if record["delivery_status"] not in _DELIVERY_STATUSES:
            raise ValueError("invalid alert delivery status")
        if record.get("last_error") is not None and not isinstance(
            record["last_error"], str
        ):
            raise ValueError("invalid alert last_error type")
        for column in ("id", "attempts"):
            value = record.get(column)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid alert {column}")
        payload = json.loads(
            record["payload_json"], parse_constant=_reject_json_constant
        )
        if not isinstance(payload, dict):
            raise ValueError("alert payload_json must contain an object")


def _connect(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _INITIALIZATION_LOCK:
        connection = sqlite3.connect(target)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                logical_slot TEXT NOT NULL,
                entity_code TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'critical')),
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(delivery_status IN ('pending', 'delivered', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, logical_slot, entity_code, alert_type)
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_events_recent "
            "ON alert_events(id DESC)"
        )
    return connection


def enqueue_option_alerts(path, alerts: pd.DataFrame, logical_slot) -> int:
    """Append new option transitions once per logical scan slot."""
    if alerts is None or alerts.empty:
        return 0
    missing = [column for column in _REQUIRED_OPTION_COLUMNS if column not in alerts]
    if missing:
        raise ValueError("missing required column: " + ", ".join(missing))
    slot = _timestamp(logical_slot)
    records = []
    for row in alerts.to_dict("records"):
        code = _text(row.get("code"), "code")
        alert_type = _text(row.get("alert_type"), "alert_type", limit=80)
        severity = _SEVERITY.get(alert_type, "warning")
        payload = {
            key: _json_value(row.get(key))
            for key in _OPTION_PAYLOAD_COLUMNS
            if key in row
        }
        name = payload.get("name") or code
        related = payload.get("underlying") or "—"
        message = f"{name} · 标的 {related} · {alert_type}"
        payload_json = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        records.append((
            "option", slot, code, alert_type, severity,
            f"期权信号：{alert_type}", message, payload_json,
        ))
    with _connect(path) as connection:
        before = connection.total_changes
        connection.executemany(
            """INSERT OR IGNORE INTO alert_events (
                   source, logical_slot, entity_code, alert_type, severity,
                   title, message, payload_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
        return connection.total_changes - before


def load_recent_alerts(path, limit=100) -> pd.DataFrame:
    """Read a bounded newest-first alert view without creating a database."""
    rows, _ = load_alert_dashboard(path, limit=limit)
    return rows


def load_alert_dashboard(path, limit=100) -> tuple[pd.DataFrame, dict[str, int]]:
    """Read a bounded recent window and full outbox counts from one DB snapshot."""
    if isinstance(limit, bool) or not isinstance(limit, Integral) or limit < 1:
        raise ValueError("limit must be a positive integer")
    counts = {"total": 0, "pending": 0, "delivered": 0, "failed": 0}
    target = Path(path)
    if not target.is_file():
        return pd.DataFrame(), counts
    uri = f"{target.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("BEGIN")
        for status, amount in connection.execute(
            "SELECT delivery_status, COUNT(*) FROM alert_events GROUP BY delivery_status"
        ):
            if status not in _DELIVERY_STATUSES or not isinstance(amount, int):
                raise ValueError("invalid alert status aggregate")
            counts[status] = amount
            counts["total"] += amount
        cursor = connection.execute(
            """SELECT id, source, logical_slot, entity_code, alert_type,
                      severity, title, message, payload_json, delivery_status,
                      attempts, last_error, created_at
               FROM alert_events ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        )
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
        _validate_loaded_alert_rows(rows, columns)
        frame = pd.DataFrame.from_records(rows, columns=columns)
    return frame, counts
