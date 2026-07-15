"""Intraday commodity futures turnover and rank-change radar."""

from __future__ import annotations

import pandas as pd

from hotspot_radar import select_domestic_main_contracts


def _shanghai_naive(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp
    return timestamp.tz_convert("Asia/Shanghai").tz_localize(None)


def closed_intraday_bars(frame, as_of=None):
    """Keep intraday bars whose API end timestamp is complete at scan time."""
    if frame is None or frame.empty or "datetime" not in frame:
        return frame
    cutoff = _shanghai_naive(
        pd.Timestamp.now(tz="Asia/Shanghai") if as_of is None else as_of
    )
    timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
    return frame.loc[timestamps.notna() & timestamps.le(cutoff)].sort_values(
        "datetime"
    ).reset_index(drop=True)


def calculate_intraday_metrics(frame):
    """Calculate rolling metrics from completed five-minute trading bars."""
    if frame is None or len(frame) < 13:
        return None
    recent = frame.sort_values("datetime").reset_index(drop=True)
    money = pd.to_numeric(recent["money"], errors="coerce").fillna(0.0)
    close = pd.to_numeric(recent["close"], errors="coerce")
    open_interest = pd.to_numeric(
        recent["open_interest"], errors="coerce"
    )
    turnover_15m = float(money.iloc[-3:].sum())
    previous_15m = float(money.iloc[-6:-3].sum())
    return {
        "bar_time": pd.Timestamp(recent["datetime"].iloc[-1]),
        "close": float(close.iloc[-1]),
        "turnover_5m": float(money.iloc[-1]),
        "turnover_15m": turnover_15m,
        "turnover_60m": float(money.iloc[-12:].sum()),
        "turnover_acceleration_15m_pct": (
            (turnover_15m / previous_15m - 1) * 100
            if previous_15m > 0 else float("nan")
        ),
        "price_change_15m_pct": (close.iloc[-1] / close.iloc[-4] - 1) * 100,
        "oi_change_5m": float(open_interest.iloc[-1] - open_interest.iloc[-2]),
        "oi_change_15m": float(open_interest.iloc[-1] - open_interest.iloc[-4]),
        "oi_change_60m": float(open_interest.iloc[-1] - open_interest.iloc[-13]),
    }


def generate_intraday_radar(client, as_of=None):
    """Discover domestic mains and fetch their five-minute bars in one batch."""
    metadata = client.search(category_type=1)
    contracts = select_domestic_main_contracts(client.main_contracts(), metadata)
    if not contracts:
        return pd.DataFrame()
    codes = [item["code"] for item in contracts]
    raw_frames = client.get_klines_by_count(codes, interval="5m", count=25)
    frames = {
        code: closed_intraday_bars(frame, as_of=as_of)
        for code, frame in raw_frames.items()
    }
    return build_intraday_ranking(contracts, frames)


def annotate_rank_changes(current, previous_state, top_n=10):
    """Compare the current 15-minute ranking with the prior persisted snapshot."""
    previous = (
        previous_state if previous_state.get("scope") == "intraday-rank" else {}
    )
    prior_ranks = previous.get("ranks", {})
    rows = []
    ranks = {}
    for record in current.to_dict("records"):
        code = str(record["code"])
        rank = int(record["rank_15m"])
        side = record["side"]
        prior = prior_ranks.get(code, {})
        previous_rank = prior.get("rank")
        rows.append({
            **record,
            "previous_rank": previous_rank,
            "rank_change": (
                int(previous_rank) - rank if previous_rank is not None else None
            ),
            "entered_top": bool(
                rank <= top_n
                and (previous_rank is None or int(previous_rank) > top_n)
            ),
            "exited_top": bool(
                rank > top_n
                and previous_rank is not None
                and int(previous_rank) <= top_n
            ),
            "direction_reversed": bool(
                prior.get("side") is not None and prior.get("side") != side
            ),
        })
        ranks[code] = {"rank": rank, "side": side}
    return pd.DataFrame(rows), {
        "version": 1, "scope": "intraday-rank", "ranks": ranks,
    }


def build_intraday_ranking(metadata, frames, max_lag_minutes=15):
    """Rank contracts by recent turnover and exclude stale session data."""
    by_code = {item["code"]: item for item in metadata}
    rows = []
    for code, frame in frames.items():
        if code not in by_code:
            continue
        metrics = calculate_intraday_metrics(frame)
        if metrics is None:
            continue
        item = by_code[code]
        rows.append({
            "code": code,
            "name": item.get("name", code),
            "exchange": item.get("exchange_code", ""),
            **metrics,
        })
    if not rows:
        return pd.DataFrame()
    freshest = max(row["bar_time"] for row in rows)
    cutoff = freshest - pd.Timedelta(minutes=max_lag_minutes)
    rows = [row for row in rows if row["bar_time"] >= cutoff]
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows)
    result["side"] = result["price_change_15m_pct"].map(
        lambda value: "多" if value > 0 else "空" if value < 0 else "平"
    )
    for window in (5, 15, 60):
        result[f"turnover_{window}m_yi"] = (
            result[f"turnover_{window}m"] / 100_000_000
        )
    result = result.sort_values(
        ["turnover_15m", "code"], ascending=[False, True]
    ).reset_index(drop=True)
    result["rank_15m"] = range(1, len(result) + 1)
    return result
