"""Durable SQLite outbox for Watchman operator alerts."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import uuid
from datetime import timedelta, timezone
from numbers import Integral, Real
from pathlib import Path

import pandas as pd

_INITIALIZATION_LOCK = threading.Lock()
_SQLITE_MAX_INTEGER = 2**63 - 1
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
        if record.get("last_requeued_at") is not None and not isinstance(
            record["last_requeued_at"], str
        ):
            raise ValueError("invalid alert last_requeued_at type")
        for column in ("id", "attempts", "total_attempts", "requeue_count"):
            value = record.get(column)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid alert {column}")
        payload = json.loads(
            record["payload_json"], parse_constant=_reject_json_constant
        )
        if not isinstance(payload, dict):
            raise ValueError("alert payload_json must contain an object")


def _connect(path, *, create=True):
    target = Path(path)
    if create:
        target.parent.mkdir(parents=True, exist_ok=True)
    elif not target.is_file():
        raise FileNotFoundError(target)
    with _INITIALIZATION_LOCK:
        if create:
            connection = sqlite3.connect(target)
        else:
            uri = f"{target.resolve().as_uri()}?mode=rw"
            connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    logical_slot TEXT NOT NULL,
                    entity_code TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL
                        CHECK(severity IN ('info', 'warning', 'critical')),
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    delivery_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(delivery_status IN ('pending', 'delivered', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    total_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK(total_attempts >= 0),
                    last_error TEXT,
                    next_attempt_at TEXT,
                    lease_token TEXT,
                    lease_until TEXT,
                    delivered_at TEXT,
                    requeue_count INTEGER NOT NULL DEFAULT 0
                        CHECK(requeue_count >= 0),
                    last_requeued_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, logical_slot, entity_code, alert_type)
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_events_recent "
                "ON alert_events(id DESC)"
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(alert_events)")
            }
            migrations = {
                "total_attempts": (
                    "ALTER TABLE alert_events ADD COLUMN total_attempts "
                    "INTEGER NOT NULL DEFAULT 0 CHECK(total_attempts >= 0)"
                ),
                "next_attempt_at": (
                    "ALTER TABLE alert_events ADD COLUMN next_attempt_at TEXT"
                ),
                "lease_token": (
                    "ALTER TABLE alert_events ADD COLUMN lease_token TEXT"
                ),
                "lease_until": (
                    "ALTER TABLE alert_events ADD COLUMN lease_until TEXT"
                ),
                "delivered_at": (
                    "ALTER TABLE alert_events ADD COLUMN delivered_at TEXT"
                ),
                "requeue_count": (
                    "ALTER TABLE alert_events ADD COLUMN requeue_count "
                    "INTEGER NOT NULL DEFAULT 0 CHECK(requeue_count >= 0)"
                ),
                "last_requeued_at": (
                    "ALTER TABLE alert_events ADD COLUMN last_requeued_at TEXT"
                ),
            }
            for name, statement in migrations.items():
                if name not in columns:
                    connection.execute(statement)
            if "total_attempts" not in columns:
                connection.execute(
                    "UPDATE alert_events SET total_attempts = attempts"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_events_delivery "
                "ON alert_events(delivery_status, next_attempt_at, lease_until, id)"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
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


def _utc_timestamp(value, name) -> str:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if pd.isna(parsed):
        raise ValueError(f"invalid {name}")
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(timezone.utc)
    else:
        parsed = parsed.tz_convert(timezone.utc)
    return parsed.isoformat(timespec="seconds")


def _positive_integer(value, name) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    result = int(value)
    if result > _SQLITE_MAX_INTEGER:
        raise ValueError(f"{name} exceeds SQLite integer range")
    return result


def claim_alerts(path, now, limit=20, lease_seconds=60) -> list[dict]:
    """Atomically lease due pending alerts and increment their attempt count."""
    batch_size = _positive_integer(limit, "limit")
    lease_duration = _positive_integer(lease_seconds, "lease_seconds")
    claimed_at = _utc_timestamp(now, "now")
    lease_until = _utc_timestamp(
        pd.Timestamp(now) + timedelta(seconds=lease_duration), "lease_until"
    )
    lease_token = uuid.uuid4().hex
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        ids = [
            row[0]
            for row in connection.execute(
                """SELECT id FROM alert_events
                   WHERE delivery_status = 'pending'
                     AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                     AND (lease_until IS NULL OR lease_until <= ?)
                   ORDER BY id LIMIT ?""",
                (claimed_at, claimed_at, batch_size),
            )
        ]
        connection.executemany(
            """UPDATE alert_events
               SET lease_token = ?, lease_until = ?, attempts = attempts + 1,
                   total_attempts = total_attempts + 1
               WHERE id = ? AND delivery_status = 'pending'
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                 AND (lease_until IS NULL OR lease_until <= ?)""",
            [
                (lease_token, lease_until, event_id, claimed_at, claimed_at)
                for event_id in ids
            ],
        )
        cursor = connection.execute(
            """SELECT id, source, logical_slot, entity_code, alert_type,
                      severity, title, message, payload_json, delivery_status,
                      attempts, total_attempts, requeue_count,
                      last_requeued_at, last_error, created_at,
                      lease_token, lease_until
               FROM alert_events WHERE lease_token = ? ORDER BY id""",
            (lease_token,),
        )
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
        _validate_loaded_alert_rows(rows, columns)
    records = []
    for row in rows:
        record = dict(zip(columns, row))
        record["payload"] = json.loads(
            record["payload_json"], parse_constant=_reject_json_constant
        )
        records.append(record)
    return records


def mark_alert_delivered(path, event_id, lease_token, delivered_at) -> bool:
    """Complete delivery only when the caller still owns the active lease."""
    identifier = _positive_integer(event_id, "event_id")
    token = _text(lease_token, "lease_token", limit=80)
    timestamp = _utc_timestamp(delivered_at, "delivered_at")
    with _connect(path) as connection:
        cursor = connection.execute(
            """UPDATE alert_events
               SET delivery_status = 'delivered', delivered_at = ?,
                   lease_token = NULL, lease_until = NULL,
                   next_attempt_at = NULL, last_error = NULL
               WHERE id = ? AND delivery_status = 'pending' AND lease_token = ?
                 AND lease_until > ?""",
            (timestamp, identifier, token, timestamp),
        )
        return cursor.rowcount == 1


def mark_alert_failed(
    path, event_id, lease_token, failed_at, error_code,
    max_attempts=5, base_delay_seconds=60,
) -> bool:
    """Release a failed lease with exponential backoff or dead-letter it."""
    identifier = _positive_integer(event_id, "event_id")
    token = _text(lease_token, "lease_token", limit=80)
    code = _text(error_code, "error_code", limit=80)
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", code) is None:
        raise ValueError("error_code must be an uppercase machine code")
    maximum = _positive_integer(max_attempts, "max_attempts")
    base_delay = _positive_integer(base_delay_seconds, "base_delay_seconds")
    failed_timestamp = _utc_timestamp(failed_at, "failed_at")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT attempts FROM alert_events
               WHERE id = ? AND delivery_status = 'pending' AND lease_token = ?
                 AND lease_until > ?""",
            (identifier, token, failed_timestamp),
        ).fetchone()
        if row is None:
            return False
        attempts = int(row[0])
        terminal = attempts >= maximum
        next_attempt = None
        if not terminal:
            delay = min(base_delay * (2 ** (attempts - 1)), 86400)
            next_attempt = _utc_timestamp(
                pd.Timestamp(failed_timestamp) + timedelta(seconds=delay),
                "next_attempt_at",
            )
        cursor = connection.execute(
            """UPDATE alert_events
               SET delivery_status = ?, last_error = ?, next_attempt_at = ?,
                   lease_token = NULL, lease_until = NULL
               WHERE id = ? AND delivery_status = 'pending' AND lease_token = ?
                 AND lease_until > ?""",
            (
                "failed" if terminal else "pending", code, next_attempt,
                identifier, token, failed_timestamp,
            ),
        )
        return cursor.rowcount == 1


def requeue_failed_alerts(path, now, event_ids=None, limit=100) -> list[int]:
    """Atomically reset a bounded set of dead letters for a fresh delivery cycle."""
    batch_size = _positive_integer(limit, "limit")
    requeued_at = _utc_timestamp(now, "now")
    identifiers = None
    if event_ids is not None:
        if isinstance(event_ids, (str, bytes)):
            raise ValueError("event_ids must be a sequence of positive integers")
        identifiers = []
        seen = set()
        for value in event_ids:
            identifier = _positive_integer(value, "event_id")
            if identifier not in seen:
                identifiers.append(identifier)
                seen.add(identifier)
        if not identifiers:
            raise ValueError("event_ids must not be empty")
        if len(identifiers) > batch_size:
            raise ValueError("event_ids exceeds limit")

    with _connect(path, create=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if identifiers is None:
            selected = [
                row[0]
                for row in connection.execute(
                    """SELECT id FROM alert_events
                       WHERE delivery_status = 'failed'
                       ORDER BY id LIMIT ?""",
                    (batch_size,),
                )
            ]
        else:
            selected = []
            for identifier in identifiers:
                row = connection.execute(
                    """SELECT id FROM alert_events
                       WHERE id = ? AND delivery_status = 'failed'""",
                    (identifier,),
                ).fetchone()
                if row is not None:
                    selected.append(row[0])
        connection.executemany(
            """UPDATE alert_events
               SET delivery_status = 'pending', attempts = 0, last_error = NULL,
                   next_attempt_at = NULL, lease_token = NULL, lease_until = NULL,
                   delivered_at = NULL, requeue_count = requeue_count + 1,
                   last_requeued_at = ?
               WHERE id = ? AND delivery_status = 'failed'""",
            [(requeued_at, identifier) for identifier in selected],
        )
        return selected


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
        replay_columns = {
            "total_attempts", "requeue_count", "last_requeued_at"
        }
        available_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(alert_events)")
        }
        present_replay_columns = replay_columns & available_columns
        if present_replay_columns == replay_columns:
            cursor = connection.execute(
                """SELECT id, source, logical_slot, entity_code, alert_type,
                          severity, title, message, payload_json, delivery_status,
                          attempts, total_attempts, requeue_count,
                          last_requeued_at, last_error, created_at
                   FROM alert_events ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            )
        elif not present_replay_columns:
            cursor = connection.execute(
                """SELECT id, source, logical_slot, entity_code, alert_type,
                          severity, title, message, payload_json, delivery_status,
                          attempts, attempts AS total_attempts,
                          0 AS requeue_count, NULL AS last_requeued_at,
                          last_error, created_at
                   FROM alert_events ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            )
        else:
            raise ValueError("incomplete alert replay schema")
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
        _validate_loaded_alert_rows(rows, columns)
        frame = pd.DataFrame.from_records(rows, columns=columns)
    return frame, counts
