"""SQLite persistence and analysis for intraday radar snapshots."""

from __future__ import annotations

import math
import sqlite3
from numbers import Integral
from pathlib import Path

import pandas as pd


SNAPSHOT_COLUMNS = (
    "code", "name", "exchange", "bar_time", "rank_15m", "side",
    "price_change_15m_pct", "turnover_5m", "turnover_15m",
    "turnover_60m", "turnover_acceleration_15m_pct", "oi_change_5m",
    "oi_change_15m", "oi_change_60m",
)
REQUIRED_SNAPSHOT_COLUMNS = (
    "code", "bar_time", "rank_15m", "turnover_15m",
    "turnover_acceleration_15m_pct",
)


def _connect(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS intraday_snapshots (
            scan_time TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            exchange TEXT,
            bar_time TEXT NOT NULL,
            rank_15m INTEGER NOT NULL,
            side TEXT,
            price_change_15m_pct REAL,
            turnover_5m REAL,
            turnover_15m REAL,
            turnover_60m REAL,
            turnover_acceleration_15m_pct REAL,
            oi_change_5m REAL,
            oi_change_15m REAL,
            oi_change_60m REAL,
            PRIMARY KEY (scan_time, code)
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_intraday_code_time "
        "ON intraday_snapshots(code, scan_time)"
    )
    return connection


def _db_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _snapshot_db_value(column, value):
    if column == "bar_time":
        return _shanghai_timestamp(value, "bar_time").isoformat()
    return _db_value(value)


def _parse_timestamp(value, name):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"invalid {name}: {value!r}")
    return timestamp


def _shanghai_timestamp(value, name):
    timestamp = _parse_timestamp(value, name)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("Asia/Shanghai")
    return timestamp.tz_convert("Asia/Shanghai")


def _validate_snapshot(snapshot):
    missing = [
        column for column in REQUIRED_SNAPSHOT_COLUMNS
        if column not in snapshot.columns
    ]
    if missing:
        raise ValueError(
            "snapshot missing required columns: " + ", ".join(missing)
        )
    for column in REQUIRED_SNAPSHOT_COLUMNS:
        if snapshot[column].isna().any():
            raise ValueError(f"snapshot column {column} must not contain null values")
    if snapshot["code"].duplicated().any():
        raise ValueError("snapshot contains duplicate code values")
    if snapshot["code"].astype(str).str.strip().eq("").any():
        raise ValueError("snapshot column code must not contain blank values")
    for value in snapshot["bar_time"]:
        _parse_timestamp(value, "bar_time")
    for value in snapshot["rank_15m"]:
        if (
            isinstance(value, bool)
            or not isinstance(value, Integral)
            or value <= 0
        ):
            raise ValueError("snapshot column rank_15m must contain positive integers")
    for column in ("turnover_15m", "turnover_acceleration_15m_pct"):
        numeric = pd.to_numeric(snapshot[column], errors="coerce")
        if numeric.isna().any() or not numeric.map(math.isfinite).all():
            raise ValueError(f"snapshot column {column} must contain finite numbers")
        if column == "turnover_15m" and numeric.lt(0).any():
            raise ValueError("snapshot column turnover_15m must be non-negative")


def save_intraday_snapshot(path, snapshot, scan_time=None):
    """Upsert one complete cross-sectional radar snapshot."""
    if snapshot is None or snapshot.empty:
        return 0
    _validate_snapshot(snapshot)
    if scan_time is None:
        timestamp = max(
            _shanghai_timestamp(value, "bar_time")
            for value in snapshot["bar_time"]
        )
    else:
        timestamp = _shanghai_timestamp(scan_time, "scan_time")
    scan_text = timestamp.isoformat()
    placeholders = ",".join("?" for _ in range(len(SNAPSHOT_COLUMNS) + 1))
    columns = "scan_time," + ",".join(SNAPSHOT_COLUMNS)
    update = ",".join(
        f"{column}=excluded.{column}" for column in SNAPSHOT_COLUMNS
    )
    sql = (
        f"INSERT INTO intraday_snapshots ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(scan_time, code) DO UPDATE SET {update}"
    )
    records = []
    for row in snapshot.to_dict("records"):
        records.append((scan_text,) + tuple(
            _snapshot_db_value(column, row.get(column))
            for column in SNAPSHOT_COLUMNS
        ))
    with _connect(path) as connection:
        connection.execute(
            "DELETE FROM intraday_snapshots WHERE scan_time = ?", (scan_text,)
        )
        connection.executemany(sql, records)
    return len(records)


def _parse_history_times(result):
    if not result.empty:
        result["scan_time"] = pd.to_datetime(
            result["scan_time"], utc=True
        ).dt.tz_convert("Asia/Shanghai")
        result["bar_time"] = pd.to_datetime(
            result["bar_time"], format="mixed", utc=True
        ).dt.tz_convert("Asia/Shanghai")
    return result


def _require_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def load_rank_trajectory(path, code, limit=20):
    """Load one contract's latest rank observations in chronological order."""
    limit = _require_positive_int("limit", limit)
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    with _connect(target) as connection:
        result = pd.read_sql_query(
            "SELECT * FROM ("
            "SELECT * FROM intraday_snapshots WHERE code = ? "
            "ORDER BY scan_time DESC LIMIT ?"
            ") ORDER BY scan_time ASC",
            connection, params=(str(code), limit),
        )
    return _parse_history_times(result)


def summarize_hotspot_persistence(
    history, top_n=10, lookback_snapshots=12, pulse_threshold_pct=100.0
):
    """Classify current leaders from recent cross-sectional snapshots."""
    top_n = _require_positive_int("top_n", top_n)
    lookback_snapshots = _require_positive_int(
        "lookback_snapshots", lookback_snapshots
    )
    if history is None or history.empty:
        return pd.DataFrame()
    recent = history.copy()
    recent["scan_time"] = pd.to_datetime(recent["scan_time"])
    scan_times = sorted(recent["scan_time"].dropna().unique())
    scan_times = scan_times[-lookback_snapshots:]
    recent = recent.loc[recent["scan_time"].isin(scan_times)]
    latest_time = scan_times[-1]
    latest_codes = recent.loc[recent["scan_time"].eq(latest_time), "code"]
    rows = []
    for code in latest_codes:
        track = recent.loc[recent["code"].eq(code)].sort_values("scan_time")
        latest = track.iloc[-1]
        ranks = pd.to_numeric(track["rank_15m"], errors="coerce")
        top_flags = ranks.le(top_n).tolist()
        rank_by_time = pd.Series(
            ranks.to_numpy(), index=track["scan_time"]
        )
        top_streak = 0
        for scan_time in reversed(scan_times):
            if scan_time not in rank_by_time.index:
                break
            rank_at_scan = rank_by_time.loc[scan_time]
            if isinstance(rank_at_scan, pd.Series) or rank_at_scan > top_n:
                break
            top_streak += 1
        baseline = latest
        if top_streak:
            streak_start = scan_times[-top_streak]
            baseline = track.loc[track["scan_time"].eq(streak_start)].iloc[0]
        first_rank = int(baseline["rank_15m"])
        latest_rank = int(ranks.iloc[-1])
        first_turnover = float(baseline["turnover_15m"])
        latest_turnover = float(track["turnover_15m"].iloc[-1])
        turnover_growth = (
            (latest_turnover / first_turnover - 1) * 100
            if first_turnover > 0 else float("nan")
        )
        rank_improvement = first_rank - latest_rank
        acceleration = float(latest["turnover_acceleration_15m_pct"])
        if latest_rank > top_n:
            status = "热点降温"
        elif top_streak >= 3 and rank_improvement >= 2 and turnover_growth > 0:
            status = "持续升温"
        elif top_streak >= 3:
            status = "持续热点"
        elif acceleration >= pulse_threshold_pct:
            status = "脉冲热点"
        else:
            status = "新晋热点"
        rows.append({
            "code": code,
            "name": latest.get("name", code),
            "side": latest.get("side", ""),
            "latest_rank": latest_rank,
            "first_rank": first_rank,
            "best_rank": int(ranks.min()),
            "rank_improvement": rank_improvement,
            "observations": len(track),
            "top_appearances": int(sum(top_flags)),
            "top_streak": top_streak,
            "turnover_15m": latest_turnover,
            "turnover_growth_pct": turnover_growth,
            "turnover_acceleration_15m_pct": acceleration,
            "persistence_status": status,
            "latest_scan_time": latest_time,
        })
    return pd.DataFrame(rows).sort_values(
        ["latest_rank", "code"]
    ).reset_index(drop=True)


def load_intraday_history(path):
    """Load all intraday snapshots ordered newest scan first and then rank."""
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    with _connect(target) as connection:
        result = pd.read_sql_query(
            "SELECT * FROM intraday_snapshots "
            "ORDER BY scan_time DESC, rank_15m ASC",
            connection,
        )
    return _parse_history_times(result)
