"""Cross-sectional momentum and excess-return ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

import pandas as pd


COMMODITY_EXCHANGES = frozenset({"SHFE", "DCE", "CZCE", "INE", "GFEX"})
RETURN_INDEX_CODE = re.compile(r"^[A-Za-z]+6666$")


def select_commodity_return_indices(instruments):
    """Select production commodity 6666 indices from instrument metadata."""
    selected = [
        item for item in instruments
        if item.get("category_type") == 1
        and item.get("variety_type") == 7
        and item.get("exchange_code") in COMMODITY_EXCHANGES
        and RETURN_INDEX_CODE.fullmatch(str(item.get("code", "")))
    ]
    return sorted(selected, key=lambda item: item["code"])


def closed_daily_bars(frame, now=None, settlement_hour=16):
    """Drop the last daily bar when its trading day is still in progress."""
    if frame is None or frame.empty or "datetime" not in frame:
        return frame
    now = pd.Timestamp.now(tz="Asia/Shanghai") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("Asia/Shanghai")
    else:
        now = now.tz_convert("Asia/Shanghai")
    latest_day = pd.Timestamp(frame["datetime"].iloc[-1]).date()
    current_day = now.date()
    if latest_day > current_day or (
        latest_day == current_day and now.hour < settlement_hour
    ):
        return frame.iloc[:-1].copy()
    return frame.copy()


def generate_ranking(client, horizons=(5, 20, 60, 120), now=None):
    """Discover commodity return indices, fetch once, and rank them."""
    horizons = tuple(horizons)
    instruments = select_commodity_return_indices(
        client.search(category_type=1)
    )
    codes = [item["code"] for item in instruments]
    if not codes:
        return build_momentum_ranking({}, horizons=horizons)
    frames = client.get_klines_by_count(
        codes, interval="day", count=max(horizons) + 2
    )
    frames = {
        code: closed_daily_bars(frame, now=now)
        for code, frame in frames.items()
    }
    metadata = {item["code"]: item for item in instruments}
    return build_momentum_ranking(frames, metadata, horizons)


def build_momentum_ranking(
    frames: Mapping[str, pd.DataFrame],
    metadata: Mapping[str, Mapping] | None = None,
    horizons: Sequence[int] = (5, 20, 60, 120),
) -> pd.DataFrame:
    """Rank symbols by trailing returns and equal-weight benchmark excess.

    A horizon of N means close[-1] / close[-N-1] - 1. Symbols without
    enough valid, positive closes for every requested horizon are omitted.
    Values are returned as percentage points.
    """
    horizons = tuple(dict.fromkeys(int(value) for value in horizons))
    if not horizons or any(value < 1 for value in horizons):
        raise ValueError("horizons must contain positive integers")

    metadata = metadata or {}
    rows = []
    required = max(horizons) + 1
    for code, source in frames.items():
        if source is None or "close" not in source:
            continue
        closes = pd.to_numeric(source["close"], errors="coerce").dropna()
        if len(closes) < required or (closes.iloc[-required:] <= 0).any():
            continue
        info = metadata.get(code, {})
        row = {
            "code": code,
            "name": info.get("name", code),
            "exchange": info.get("exchange_code", ""),
            "as_of": source["datetime"].iloc[-1] if "datetime" in source else pd.NaT,
        }
        for horizon in horizons:
            row[f"return_{horizon}d"] = (
                closes.iloc[-1] / closes.iloc[-horizon - 1] - 1
            ) * 100
        rows.append(row)

    return_columns = ["code", "name", "exchange", "as_of"]
    for horizon in horizons:
        return_columns.extend([
            f"return_{horizon}d", f"excess_{horizon}d", f"rank_{horizon}d"
        ])
    return_columns.append("momentum_score")
    if not rows:
        return pd.DataFrame(columns=return_columns)

    result = pd.DataFrame(rows)
    percentile_columns = []
    for horizon in horizons:
        return_col = f"return_{horizon}d"
        excess_col = f"excess_{horizon}d"
        rank_col = f"rank_{horizon}d"
        result[excess_col] = result[return_col] - result[return_col].mean()
        result[rank_col] = result[return_col].rank(
            method="min", ascending=False
        ).astype(int)
        pct_col = f"_pct_{horizon}d"
        result[pct_col] = result[return_col].rank(pct=True)
        percentile_columns.append(pct_col)

    result["momentum_score"] = result[percentile_columns].mean(axis=1) * 100
    result = result.sort_values(
        ["momentum_score", f"return_{horizons[0]}d", "code"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return result[return_columns]
