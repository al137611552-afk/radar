"""SQLite persistence and lifecycle analysis for hourly option signal snapshots."""

from __future__ import annotations

import math
import sqlite3
import threading
from numbers import Integral
from pathlib import Path

import pandas as pd


SNAPSHOT_COLUMNS = (
    "code",
    "name",
    "exchange",
    "bar_time",
    "underlying",
    "option_type",
    "dte",
    "expiry",
    "strike",
    "last_price",
    "moneyness",
    "recent_volume",
    "open_interest",
    "signal_score",
    "confirmation_score",
    "ma_bullish",
    "macd_bullish",
    "double_confirmed",
    "ma_direction_confirmed",
    "macd_direction_confirmed",
    "ma_cross_time",
    "macd_cross_time",
)
NUMERIC_COLUMNS = (
    "strike",
    "last_price",
    "moneyness",
    "recent_volume",
    "open_interest",
    "signal_score",
    "confirmation_score",
)
BOOLEAN_COLUMNS = (
    "ma_bullish",
    "macd_bullish",
    "double_confirmed",
    "ma_direction_confirmed",
    "macd_direction_confirmed",
)
TIME_COLUMNS = ("bar_time", "ma_cross_time", "macd_cross_time")
_INITIALIZATION_LOCK = threading.Lock()


def _connect(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _INITIALIZATION_LOCK:
        connection = sqlite3.connect(target)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS option_scans (
                scan_time TEXT PRIMARY KEY
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS option_snapshots (
                scan_time TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                exchange TEXT,
                bar_time TEXT NOT NULL,
                underlying TEXT NOT NULL,
                option_type TEXT NOT NULL,
                dte INTEGER NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                last_price REAL NOT NULL,
                moneyness REAL NOT NULL,
                recent_volume REAL NOT NULL,
                open_interest REAL NOT NULL,
                signal_score REAL NOT NULL,
                confirmation_score REAL NOT NULL,
                ma_bullish INTEGER NOT NULL,
                macd_bullish INTEGER NOT NULL,
                double_confirmed INTEGER NOT NULL,
                ma_direction_confirmed INTEGER NOT NULL,
                macd_direction_confirmed INTEGER NOT NULL,
                ma_cross_time TEXT,
                macd_cross_time TEXT,
                PRIMARY KEY (scan_time, code)
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_option_code_time "
            "ON option_snapshots(code, scan_time)"
        )
    return connection


def _timestamp(value, name):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"invalid {name}: {value!r}")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("Asia/Shanghai")
    return timestamp.tz_convert("Asia/Shanghai")


def _scan_time(snapshot, scan_time):
    value = scan_time
    if value is None:
        value = snapshot.attrs.get("scan_time")
    if value is None:
        value = max(_timestamp(item, "bar_time") for item in snapshot["bar_time"])
    return _timestamp(value, "scan_time").isoformat()


def _validate_snapshot(snapshot):
    missing = [column for column in SNAPSHOT_COLUMNS if column not in snapshot.columns]
    if missing:
        raise ValueError("snapshot missing required column: " + ", ".join(missing))
    if snapshot["code"].isna().any() or snapshot["code"].astype(str).str.strip().eq("").any():
        raise ValueError("snapshot code must not be null or blank")
    if snapshot["code"].duplicated().any():
        raise ValueError("snapshot contains duplicate code values")
    for column in ("bar_time", "underlying", "option_type", "expiry"):
        if snapshot[column].isna().any() or snapshot[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"snapshot {column} must not be null or blank")
    for value in snapshot["bar_time"]:
        _timestamp(value, "bar_time")
    for column in ("ma_cross_time", "macd_cross_time"):
        for value in snapshot[column].dropna():
            _timestamp(value, column)
    for value in snapshot["dte"]:
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
            raise ValueError("dte must contain non-negative integers")
    for column in NUMERIC_COLUMNS:
        numeric = pd.to_numeric(snapshot[column], errors="coerce")
        if numeric.isna().any() or not numeric.map(math.isfinite).all():
            raise ValueError(f"{column} must contain finite numbers")
    for column in BOOLEAN_COLUMNS:
        if not snapshot[column].map(lambda value: isinstance(value, (bool,))).all():
            raise ValueError(f"{column} must contain boolean values")


def _db_value(column, value):
    if value is None or pd.isna(value):
        return None
    if column in TIME_COLUMNS:
        return _timestamp(value, column).isoformat()
    if column in BOOLEAN_COLUMNS:
        return int(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def save_option_snapshot(path, snapshot, scan_time=None):
    """Transactionally replace one complete hourly option candidate snapshot."""
    if snapshot is None or snapshot.empty:
        if scan_time is not None:
            scan_text = _timestamp(scan_time, "scan_time").isoformat()
            with _connect(path) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO option_scans(scan_time) VALUES (?)",
                    (scan_text,),
                )
                connection.execute(
                    "DELETE FROM option_snapshots WHERE scan_time = ?", (scan_text,)
                )
        return 0
    _validate_snapshot(snapshot)
    scan_text = _scan_time(snapshot, scan_time)
    columns = ",".join(SNAPSHOT_COLUMNS)
    placeholders = ",".join("?" for _ in range(len(SNAPSHOT_COLUMNS) + 1))
    records = [
        (scan_text,) + tuple(_db_value(column, row.get(column)) for column in SNAPSHOT_COLUMNS)
        for row in snapshot.to_dict("records")
    ]
    with _connect(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO option_scans(scan_time) VALUES (?)", (scan_text,)
        )
        connection.execute("DELETE FROM option_snapshots WHERE scan_time = ?", (scan_text,))
        connection.executemany(
            f"INSERT INTO option_snapshots (scan_time,{columns}) VALUES ({placeholders})",
            records,
        )
    return len(records)


def _parse_times(frame):
    if frame.empty:
        return frame
    for column in ("scan_time", "bar_time", "ma_cross_time", "macd_cross_time"):
        frame[column] = pd.to_datetime(frame[column], format="mixed", utc=True).dt.tz_convert(
            "Asia/Shanghai"
        )
    for column in BOOLEAN_COLUMNS:
        frame[column] = frame[column].astype(bool)
    return frame


def load_option_history(path):
    """Load all option snapshots, newest scan first and strongest signal first."""
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.execute("BEGIN")
            result = pd.read_sql_query(
                "SELECT * FROM option_snapshots "
                "ORDER BY scan_time DESC, confirmation_score DESC, signal_score DESC, code ASC",
                connection,
            )
            scan_times = [
                row[0]
                for row in connection.execute(
                    "SELECT scan_time FROM option_scans ORDER BY scan_time"
                ).fetchall()
            ]
    except sqlite3.Error:
        return pd.DataFrame()
    try:
        result = _parse_times(result)
        result.attrs["scan_times"] = pd.to_datetime(
            scan_times, format="mixed", utc=True
        ).tz_convert("Asia/Shanghai").tolist()
    except (TypeError, ValueError, OverflowError):
        return pd.DataFrame()
    return result


def _positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def load_option_trajectory(path, code, limit=60):
    """Load one option contract's latest observations in chronological order."""
    limit = _positive_int("limit", limit)
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            result = pd.read_sql_query(
                "SELECT * FROM (SELECT * FROM option_snapshots WHERE code = ? "
                "ORDER BY scan_time DESC LIMIT ?) ORDER BY scan_time ASC",
                connection,
                params=[str(code), limit],
            )
    except sqlite3.Error:
        return pd.DataFrame()
    return _parse_times(result)


def summarize_option_changes(history):
    """Compare the latest two complete option candidate scans."""
    if history is None or history.empty:
        return pd.DataFrame()
    known_scan_times = history.attrs.get("scan_times", [])
    frame = history.copy()
    frame["scan_time"] = pd.to_datetime(frame["scan_time"])
    scan_times = sorted(
        pd.to_datetime(known_scan_times).tolist()
        if known_scan_times else frame["scan_time"].dropna().unique()
    )
    if not scan_times:
        return pd.DataFrame()
    latest = frame.loc[frame["scan_time"].eq(scan_times[-1])].copy()
    previous = frame.iloc[0:0].copy()
    if len(scan_times) > 1:
        previous = frame.loc[frame["scan_time"].eq(scan_times[-2])].copy()
    result = latest.merge(
        previous,
        on="code",
        how="outer",
        suffixes=("", "_previous"),
        validate="one_to_one",
        indicator=True,
    )
    result["previous_confirmation_score"] = result["confirmation_score_previous"]
    result["confirmation_score_change"] = (
        result["confirmation_score"] - result["previous_confirmation_score"]
    )

    def classify(row):
        where = row["_merge"]
        current_confirmed = bool(row.get("double_confirmed")) if where != "right_only" else False
        previous_confirmed = (
            bool(row.get("double_confirmed_previous")) if where != "left_only" else False
        )
        if where == "right_only":
            return "移出候选"
        if current_confirmed and not previous_confirmed:
            return "新晋双确认"
        if previous_confirmed and not current_confirmed:
            return "双确认失效"
        change = row.get("confirmation_score_change")
        if pd.notna(change) and change > 0:
            return "信号增强"
        if pd.notna(change) and change < 0:
            return "信号减弱"
        if current_confirmed:
            return "双确认持续"
        if where == "left_only":
            return "新候选"
        return "状态不变"

    result["change_status"] = result.apply(classify, axis=1)
    for column in SNAPSHOT_COLUMNS:
        previous_column = f"{column}_previous"
        if column != "code" and previous_column in result:
            result[column] = result[column].combine_first(result[previous_column])
    result["scan_time"] = pd.Timestamp(scan_times[-1])
    result["is_current"] = result["_merge"].ne("right_only")
    result = result.drop(columns=[column for column in result if column.endswith("_previous")] + ["_merge"])
    return result.sort_values(
        ["is_current", "double_confirmed", "confirmation_score", "code"],
        ascending=[False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)
