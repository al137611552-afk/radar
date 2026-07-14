"""Domestic commodity futures turnover hotspot radar."""

from __future__ import annotations

import pandas as pd

from ranking import COMMODITY_EXCHANGES


def select_domestic_main_contracts(main_contracts, metadata):
    """Resolve current main mappings to domestic commodity contract metadata."""
    by_code = {item.get("code"): item for item in metadata}
    selected = []
    for code in set(main_contracts.values()):
        item = by_code.get(code)
        if item and item.get("exchange_code") in COMMODITY_EXCHANGES:
            selected.append(item)
    return sorted(selected, key=lambda item: item["code"])


def _position_action(change_pct, oi_change):
    if change_pct > 0 and oi_change > 0:
        return "多头增仓"
    if change_pct < 0 and oi_change > 0:
        return "空头增仓"
    if change_pct > 0 and oi_change < 0:
        return "空头减仓"
    if change_pct < 0 and oi_change < 0:
        return "多头减仓"
    return "方向不明"


def generate_hotspot_radar(client):
    """Discover domestic mains, fetch two daily bars in one batch, and rank."""
    metadata = client.search(category_type=1)
    contracts = select_domestic_main_contracts(client.main_contracts(), metadata)
    if not contracts:
        return pd.DataFrame()
    codes = [item["code"] for item in contracts]
    frames = client.get_klines_by_count(codes, interval="day", count=2)
    return build_hotspot_ranking(contracts, frames)


def build_hotspot_ranking(metadata, frames):
    """Rank current trading-day turnover and classify price/OI quadrants."""
    by_code = {item["code"]: item for item in metadata}
    latest_dates = [
        pd.Timestamp(frame.iloc[-1]["datetime"]).date()
        for code, frame in frames.items()
        if code in by_code and frame is not None and not frame.empty
    ]
    if not latest_dates:
        return pd.DataFrame()
    market_trade_date = max(latest_dates)
    rows = []
    for code, frame in frames.items():
        if frame is None or len(frame) < 2 or code not in by_code:
            continue
        previous, current = frame.iloc[-2], frame.iloc[-1]
        if pd.Timestamp(current["datetime"]).date() != market_trade_date:
            continue
        previous_close = float(previous["close"])
        close = float(current["close"])
        if previous_close <= 0:
            continue
        change_pct = (close / previous_close - 1) * 100
        previous_oi = float(previous.get("open_interest", 0) or 0)
        open_interest = float(current.get("open_interest", 0) or 0)
        oi_change = open_interest - previous_oi
        oi_change_pct = oi_change / previous_oi * 100 if previous_oi else 0.0
        money = current.get("money")
        if pd.isna(money):
            multiplier = float(by_code[code].get("multiplier", 1) or 1)
            money = close * float(current.get("volume", 0) or 0) * multiplier
        turnover = float(money or 0)
        side = "多" if change_pct > 0 else "空" if change_pct < 0 else "平"
        rows.append({
            "code": code,
            "name": by_code[code].get("name", code),
            "exchange": by_code[code].get("exchange_code", ""),
            "trade_date": pd.Timestamp(current["datetime"]).date().isoformat(),
            "close": close,
            "change_pct": change_pct,
            "volume": float(current.get("volume", 0) or 0),
            "turnover": turnover,
            "turnover_yi": turnover / 100_000_000,
            "open_interest": open_interest,
            "oi_change": oi_change,
            "oi_change_pct": oi_change_pct,
            "side": side,
            "position_action": _position_action(change_pct, oi_change),
            "signed_turnover_yi": turnover / 100_000_000 * (1 if side == "多" else -1 if side == "空" else 0),
        })
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).sort_values(
        ["turnover", "code"], ascending=[False, True]
    ).reset_index(drop=True)
    result["overall_rank"] = range(1, len(result) + 1)
    result["side_rank"] = (
        result.groupby("side")["turnover"].rank(method="first", ascending=False).astype(int)
    )
    total = result["turnover"].sum()
    result["turnover_share_pct"] = result["turnover"] / total * 100 if total else 0.0
    return result
