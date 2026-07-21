"""SQLite persistence for daily cross-sectional momentum snapshots."""

from __future__ import annotations

import math
import sqlite3
from numbers import Integral
from pathlib import Path

import pandas as pd


SNAPSHOT_COLUMNS = (
    "code",
    "as_of",
    "name",
    "exchange",
    "sector",
    "momentum_score",
    "long_rank",
    "short_rank",
    "risk_adjusted_score",
    "risk_long_rank",
    "risk_short_rank",
    "volatility_score",
    "volatility_risk",
)


def _connect(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS momentum_snapshots (
            snapshot_date TEXT NOT NULL,
            code TEXT NOT NULL,
            as_of TEXT NOT NULL,
            name TEXT,
            exchange TEXT,
            sector TEXT,
            momentum_score REAL NOT NULL,
            long_rank INTEGER NOT NULL,
            short_rank INTEGER NOT NULL,
            risk_adjusted_score REAL NOT NULL,
            risk_long_rank INTEGER NOT NULL,
            risk_short_rank INTEGER NOT NULL,
            volatility_score REAL NOT NULL,
            volatility_risk TEXT NOT NULL,
            PRIMARY KEY (snapshot_date, code)
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_momentum_code_date "
        "ON momentum_snapshots(code, snapshot_date)"
    )
    return connection


def _snapshot_date(snapshot):
    dates = pd.to_datetime(snapshot["as_of"], errors="raise")
    if dates.isna().any():
        raise ValueError("snapshot as_of must not contain null values")
    return dates.max().date().isoformat()


def _db_value(value):
    if hasattr(value, "item"):
        return value.item()
    return value


def _snapshot_db_value(column, value):
    if column == "as_of":
        return pd.Timestamp(value).date().isoformat()
    return _db_value(value)


def save_momentum_snapshot(path, snapshot):
    """Transactionally replace one complete daily momentum cross-section."""
    if snapshot is None or snapshot.empty:
        return 0
    required = SNAPSHOT_COLUMNS
    missing = [column for column in required if column not in snapshot.columns]
    if missing:
        raise ValueError("snapshot missing required column: " + ", ".join(missing))
    if snapshot["code"].isna().any() or snapshot["code"].astype(str).str.strip().eq("").any():
        raise ValueError("snapshot code must not be null or blank")
    if snapshot["code"].duplicated().any():
        raise ValueError("snapshot contains duplicate code values")
    for column in (
        "long_rank", "short_rank", "risk_long_rank", "risk_short_rank"
    ):
        if column not in snapshot.columns:
            raise ValueError(f"snapshot missing required column: {column}")
        for value in snapshot[column]:
            if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                raise ValueError(f"{column} must contain positive integers")
    for column in ("momentum_score", "risk_adjusted_score", "volatility_score"):
        numeric = pd.Series(pd.to_numeric(snapshot[column], errors="coerce"))
        if numeric.isna().any() or not numeric.map(math.isfinite).all():
            raise ValueError(f"{column} must contain finite numbers")
    if snapshot["volatility_risk"].isna().any() or snapshot[
        "volatility_risk"
    ].astype(str).str.strip().eq("").any():
        raise ValueError("volatility_risk must not be null or blank")
    snapshot_date = _snapshot_date(snapshot)
    columns = ",".join(SNAPSHOT_COLUMNS)
    placeholders = ",".join("?" for _ in range(len(SNAPSHOT_COLUMNS) + 1))
    records = [
        (snapshot_date,) + tuple(
            _snapshot_db_value(column, row.get(column)) for column in SNAPSHOT_COLUMNS
        )
        for row in snapshot.to_dict("records")
    ]
    with _connect(path) as connection:
        connection.execute(
            "DELETE FROM momentum_snapshots WHERE snapshot_date = ?",
            (snapshot_date,),
        )
        connection.executemany(
            f"INSERT INTO momentum_snapshots (snapshot_date,{columns}) "
            f"VALUES ({placeholders})",
            records,
        )
    return len(records)


def load_momentum_history(path):
    """Load all daily snapshots newest first and then by original long rank."""
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            result = pd.read_sql_query(
                "SELECT * FROM momentum_snapshots "
                "ORDER BY snapshot_date DESC, long_rank ASC, code ASC",
                connection,
            )
    except sqlite3.Error:
        return pd.DataFrame()
    if not result.empty:
        result["snapshot_date"] = pd.to_datetime(result["snapshot_date"])
        result["as_of"] = pd.to_datetime(result["as_of"])
    return result


def summarize_momentum_changes(history, top_n=20):
    """Compare the latest daily ranking with the preceding snapshot."""
    if isinstance(top_n, bool) or not isinstance(top_n, Integral) or top_n <= 0:
        raise ValueError("top_n must be a positive integer")
    if history is None or history.empty:
        return pd.DataFrame()
    frame = history.copy()
    frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"])
    dates = sorted(frame["snapshot_date"].dropna().unique())
    if not dates:
        return pd.DataFrame()
    latest = frame.loc[frame["snapshot_date"].eq(dates[-1])].copy()
    previous = frame.iloc[0:0].copy()
    if len(dates) > 1:
        previous = frame.loc[frame["snapshot_date"].eq(dates[-2])].copy()
    previous = previous[[
        "code", "long_rank", "short_rank", "risk_long_rank", "risk_short_rank"
    ]].rename(columns={
        "long_rank": "previous_long_rank",
        "short_rank": "previous_short_rank",
        "risk_long_rank": "previous_risk_long_rank",
        "risk_short_rank": "previous_risk_short_rank",
    })
    result = latest.merge(previous, on="code", how="left", validate="one_to_one")
    for rank in ("long_rank", "short_rank", "risk_long_rank", "risk_short_rank"):
        prior = f"previous_{rank}"
        result[f"{rank}_change"] = result[prior] - result[rank]
        result[f"new_{rank.removesuffix('_rank')}_entry"] = (
            result[rank].le(top_n)
            & (result[prior].isna() | result[prior].gt(top_n))
        )
    return result.sort_values(["risk_long_rank", "code"]).reset_index(drop=True)


def load_momentum_trajectory(path, code, limit=60):
    """Load one product's latest daily rank observations chronologically."""
    if isinstance(limit, bool) or not isinstance(limit, Integral) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            result = pd.read_sql_query(
                "SELECT * FROM ("
                "SELECT * FROM momentum_snapshots WHERE code = ? "
                "ORDER BY snapshot_date DESC LIMIT ?"
                ") ORDER BY snapshot_date ASC",
                connection,
                params=[str(code), int(limit)],
            )
    except sqlite3.Error:
        return pd.DataFrame()
    if not result.empty:
        result["snapshot_date"] = pd.to_datetime(result["snapshot_date"])
        result["as_of"] = pd.to_datetime(result["as_of"])
    return result
