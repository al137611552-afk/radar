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


def mark_main_rollovers(contracts, current_mains, previous_mains):
    """Annotate contracts whose variety main changed since the prior trade date."""
    main_key_by_code = {code: key for key, code in current_mains.items()}
    marked = []
    for contract in contracts:
        item = dict(contract)
        main_key = main_key_by_code.get(item["code"])
        previous_code = previous_mains.get(main_key) if main_key is not None else None
        item.update({
            "main_key": main_key,
            "previous_main_contract": previous_code,
            "main_switched": bool(previous_code and previous_code != item["code"]),
        })
        marked.append(item)
    return marked


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
    """Discover mains, fetch daily bars, detect rollover, and rank."""
    metadata = client.search(category_type=1)
    current_mains = client.main_contracts()
    contracts = select_domestic_main_contracts(current_mains, metadata)
    if not contracts:
        return pd.DataFrame()
    codes = [item["code"] for item in contracts]
    frames = client.get_klines_by_count(codes, interval="day", count=2)
    latest_dates = [
        pd.Timestamp(frame.iloc[-1]["datetime"]).date()
        for frame in frames.values()
        if frame is not None and not frame.empty
    ]
    if latest_dates:
        market_trade_date = max(latest_dates)
        previous_dates = [
            pd.Timestamp(frame.iloc[-2]["datetime"]).date()
            for frame in frames.values()
            if frame is not None and len(frame) >= 2
            and pd.Timestamp(frame.iloc[-1]["datetime"]).date() == market_trade_date
        ]
        if previous_dates:
            previous_date = max(previous_dates).isoformat()
            previous_mains = client.main_contracts_by_date(previous_date)
            contracts = mark_main_rollovers(
                contracts, current_mains, previous_mains
            )
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
        reference_price = float(previous["close"])
        reference_price_type = "close"
        settlement = previous.get("settlement")
        if settlement is not None and pd.notna(settlement) and float(settlement) > 0:
            reference_price = float(settlement)
            reference_price_type = "settlement"
        close = float(current["close"])
        if reference_price <= 0:
            continue
        change_pct = (close / reference_price - 1) * 100
        contract = by_code[code]
        main_switched = bool(contract.get("main_switched", False))
        previous_oi = float(previous.get("open_interest", 0) or 0)
        open_interest = float(current.get("open_interest", 0) or 0)
        if main_switched:
            oi_change = float("nan")
            oi_change_pct = float("nan")
            position_action = "主力切换"
        else:
            oi_change = open_interest - previous_oi
            oi_change_pct = oi_change / previous_oi * 100 if previous_oi else 0.0
            position_action = _position_action(change_pct, oi_change)
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
            "reference_price": reference_price,
            "reference_price_type": reference_price_type,
            "change_pct": change_pct,
            "volume": float(current.get("volume", 0) or 0),
            "turnover": turnover,
            "turnover_yi": turnover / 100_000_000,
            "open_interest": open_interest,
            "oi_change": oi_change,
            "oi_change_pct": oi_change_pct,
            "main_switched": main_switched,
            "previous_main_contract": contract.get("previous_main_contract"),
            "side": side,
            "position_action": position_action,
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
