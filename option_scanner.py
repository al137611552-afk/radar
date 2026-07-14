"""Near-expiry commodity option screening and hourly signal calculation."""

from __future__ import annotations

import pandas as pd

from ranking import COMMODITY_EXCHANGES


def _shanghai_timestamp(value):
    value = pd.Timestamp(value)
    if value.tzinfo is None:
        return value.tz_localize("Asia/Shanghai")
    return value.tz_convert("Asia/Shanghai")


def closed_hour_bars(frame, now=None):
    """Keep bars whose API end timestamp is not later than scan time."""
    if frame is None or frame.empty or "datetime" not in frame:
        return frame
    now = _shanghai_timestamp(
        pd.Timestamp.now(tz="Asia/Shanghai") if now is None else now
    ).tz_localize(None)
    timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
    return frame.loc[timestamps.notna() & timestamps.le(now)].sort_values(
        "datetime"
    ).reset_index(drop=True)


def _bars_since_recent_cross(cross, lookback):
    recent = cross.iloc[-lookback:]
    positions = [i for i, value in enumerate(recent.tolist()) if value]
    return None if not positions else len(recent) - 1 - positions[-1]


def assess_liquidity(
    frame, now=None, lookback=20, min_nonzero_bars=10, min_volume=100,
    min_open_interest=100, max_stale_hours=48,
):
    """Assess option activity using completed hourly bars only."""
    now = _shanghai_timestamp(
        pd.Timestamp.now(tz="Asia/Shanghai") if now is None else now
    ).tz_localize(None)
    recent = frame.tail(lookback)
    volumes = pd.to_numeric(recent.get("volume", 0), errors="coerce").fillna(0)
    nonzero = int(volumes.gt(0).sum())
    volume_sum = float(volumes.sum())
    if "open_interest" in recent and not recent.empty:
        open_interest = float(
            pd.to_numeric(recent["open_interest"], errors="coerce").fillna(0).iloc[-1]
        )
    else:
        open_interest = 0.0
    latest = pd.Timestamp(recent["datetime"].iloc[-1]) if not recent.empty else pd.NaT
    stale_hours = (
        float((now - latest).total_seconds() / 3600) if pd.notna(latest) else float("inf")
    )
    liquid = (
        len(recent) >= lookback
        and nonzero >= min_nonzero_bars
        and volume_sum >= min_volume
        and open_interest >= min_open_interest
        and stale_hours <= max_stale_hours
    )
    return {
        "liquid": bool(liquid),
        "nonzero_volume_bars": nonzero,
        "recent_volume": volume_sum,
        "open_interest": open_interest,
        "stale_hours": round(stale_hours, 2),
    }


def direction_confirmation(option_type, option_signal, underlying_signal):
    """Confirm rising option premium against the directional underlying trend."""
    is_call = option_type == "CALL"

    def aligned(indicator):
        option_bullish = option_signal.get(f"{indicator}_bullish")
        underlying_bullish = underlying_signal.get(f"{indicator}_bullish")
        if option_bullish is None or underlying_bullish is None:
            return False
        underlying_aligned = (
            bool(underlying_bullish) if is_call else not bool(underlying_bullish)
        )
        return bool(option_bullish) and underlying_aligned

    ma_confirmed = aligned("ma")
    macd_confirmed = aligned("macd")
    return {
        "ma_direction_confirmed": ma_confirmed,
        "macd_direction_confirmed": macd_confirmed,
        "double_confirmed": ma_confirmed or macd_confirmed,
        "direction_confirmation_count": int(ma_confirmed) + int(macd_confirmed),
    }


def analyze_hourly_signal(
    frame, ma_fast=5, ma_slow=20, macd_fast=12, macd_slow=26,
    macd_signal=9, cross_lookback=3,
):
    """Calculate MA/MACD state and their most recent bullish crosses."""
    if ma_fast < 1 or ma_slow <= ma_fast:
        raise ValueError("MA periods must satisfy 1 <= fast < slow")
    if macd_fast < 1 or macd_slow <= macd_fast or macd_signal < 1:
        raise ValueError("MACD periods must satisfy 1 <= fast < slow and signal >= 1")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    fast = closes.rolling(ma_fast).mean()
    slow = closes.rolling(ma_slow).mean()
    ma_cross = fast.gt(slow) & fast.shift(1).le(slow.shift(1))
    ma_bars_ago = _bars_since_recent_cross(ma_cross, cross_lookback)
    ma_cross_time = (
        None if ma_bars_ago is None
        else pd.Timestamp(frame["datetime"].iloc[-1 - ma_bars_ago])
    )

    macd_line = (
        closes.ewm(span=macd_fast, adjust=False).mean()
        - closes.ewm(span=macd_slow, adjust=False).mean()
    )
    signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
    macd_cross = macd_line.gt(signal_line) & macd_line.shift(1).le(
        signal_line.shift(1)
    )
    macd_bars_ago = _bars_since_recent_cross(macd_cross, cross_lookback)
    macd_cross_time = (
        None if macd_bars_ago is None
        else pd.Timestamp(frame["datetime"].iloc[-1 - macd_bars_ago])
    )
    return {
        "ma_fast": float(fast.iloc[-1]),
        "ma_slow": float(slow.iloc[-1]),
        "ma_bullish": bool(fast.iloc[-1] > slow.iloc[-1]),
        "ma_cross_now": ma_bars_ago == 0,
        "ma_cross_bars_ago": ma_bars_ago,
        "ma_cross_time": ma_cross_time,
        "macd_line": float(macd_line.iloc[-1]),
        "macd_signal": float(signal_line.iloc[-1]),
        "macd_hist": float(macd_line.iloc[-1] - signal_line.iloc[-1]),
        "macd_bullish": bool(macd_line.iloc[-1] > signal_line.iloc[-1]),
        "macd_cross_now": macd_bars_ago == 0,
        "macd_cross_bars_ago": macd_bars_ago,
        "macd_cross_time": macd_cross_time,
    }


def select_near_expiry_options(instruments, as_of=None, min_dte=1, max_dte=15):
    """Return listed commodity options with min_dte <= DTE < max_dte."""
    as_of = _shanghai_timestamp(
        pd.Timestamp.now(tz="Asia/Shanghai") if as_of is None else as_of
    )
    today = as_of.date()
    selected = []
    for item in instruments:
        if item.get("category_type") != 7:
            continue
        if item.get("exchange_code") not in COMMODITY_EXCHANGES:
            continue
        if not item.get("options_target_code") or item.get("options_cp_type") not in (1, 2):
            continue
        try:
            listed = pd.Timestamp(item["start_date"]).date()
            expiry = pd.Timestamp(item["expire_time"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        dte = (expiry - today).days
        if listed <= today and min_dte <= dte < max_dte:
            selected.append({**item, "dte": dte})
    return sorted(selected, key=lambda item: (item["dte"], item["code"]))


def select_nearest_strikes(
    options, underlying_prices, strikes_per_side=3, max_moneyness=0.15,
):
    """Keep the nearest liquid-research strike candidates per underlying/CP."""
    groups = {}
    for item in options:
        underlying = item.get("options_target_code")
        price = underlying_prices.get(underlying)
        strike = item.get("options_exercise_price")
        if price is None or strike is None or price <= 0:
            continue
        moneyness = float(strike) / float(price) - 1
        if abs(moneyness) > max_moneyness:
            continue
        enriched = {**item, "underlying_price": float(price),
                    "moneyness": round(moneyness, 6)}
        key = (underlying, item.get("options_cp_type"), item.get("expire_time"))
        groups.setdefault(key, []).append(enriched)
    selected = []
    for group in groups.values():
        group.sort(key=lambda item: (
            abs(item["moneyness"]), item["options_exercise_price"], item["code"]
        ))
        selected.extend(group[:strikes_per_side])
    return sorted(selected, key=lambda item: (
        item.get("dte", 0), item["options_target_code"],
        item.get("options_cp_type", 0), abs(item["moneyness"]),
        item["options_exercise_price"], item["code"],
    ))


def scan_near_expiry_options(
    client, now=None, min_dte=1, max_dte=15, kline_count=80,
    strikes_per_side=3, max_moneyness=0.15,
    ma_fast=5, ma_slow=20, macd_fast=12, macd_slow=26, macd_signal=9,
    cross_lookback=3, liquidity_lookback=20, min_nonzero_bars=10,
    min_volume=100, min_open_interest=100, max_stale_hours=48,
    include_illiquid=False,
):
    """Fetch near-expiry options in one batch and calculate hourly signals."""
    now = _shanghai_timestamp(
        pd.Timestamp.now(tz="Asia/Shanghai") if now is None else now
    )
    candidates = select_near_expiry_options(
        client.search(category_type=7), as_of=now,
        min_dte=min_dte, max_dte=max_dte,
    )
    if not candidates:
        return pd.DataFrame()

    signal_required_bars = max(ma_slow + 1, macd_slow + macd_signal)
    required_bars = max(signal_required_bars, liquidity_lookback)
    underlying_codes = sorted({item["options_target_code"] for item in candidates})
    underlying_frames = client.get_klines_by_count(
        underlying_codes, interval="1h", count=kline_count
    )
    underlying_prices = {}
    underlying_signals = {}
    underlying_signal_values = {}
    for code, frame in underlying_frames.items():
        complete = closed_hour_bars(frame, now=now)
        if complete is not None and not complete.empty:
            underlying_prices[code] = float(complete["close"].iloc[-1])
        if complete is not None and len(complete) >= signal_required_bars:
            signal = analyze_hourly_signal(
                complete, ma_fast=ma_fast, ma_slow=ma_slow,
                macd_fast=macd_fast, macd_slow=macd_slow,
                macd_signal=macd_signal, cross_lookback=cross_lookback,
            )
            underlying_signal_values[code] = signal
            underlying_signals[code] = {
                f"underlying_{key}": value for key, value in signal.items()
            } | {"underlying_bar_time": complete["datetime"].iloc[-1]}
    candidates = select_nearest_strikes(
        candidates, underlying_prices, strikes_per_side=strikes_per_side,
        max_moneyness=max_moneyness,
    )
    if not candidates:
        return pd.DataFrame()
    codes = [item["code"] for item in candidates]
    frames = client.get_klines_by_count(codes, interval="1h", count=kline_count)
    rows = []
    for item in candidates:
        bars = closed_hour_bars(frames.get(item["code"], pd.DataFrame()), now=now)
        if bars is None or len(bars) < required_bars:
            continue
        liquidity = assess_liquidity(
            bars, now=now, lookback=liquidity_lookback,
            min_nonzero_bars=min_nonzero_bars, min_volume=min_volume,
            min_open_interest=min_open_interest, max_stale_hours=max_stale_hours,
        )
        if not liquidity["liquid"] and not include_illiquid:
            continue
        signal = analyze_hourly_signal(
            bars, ma_fast=ma_fast, ma_slow=ma_slow,
            macd_fast=macd_fast, macd_slow=macd_slow,
            macd_signal=macd_signal, cross_lookback=cross_lookback,
        )
        recent_crosses = sum(
            value is not None
            for value in (signal["ma_cross_bars_ago"], signal["macd_cross_bars_ago"])
        )
        bullish_states = int(signal["ma_bullish"]) + int(signal["macd_bullish"])
        option_type = "CALL" if item.get("options_cp_type") == 1 else "PUT"
        confirmation = direction_confirmation(
            option_type, signal,
            underlying_signal_values.get(item.get("options_target_code"), {}),
        )
        signal_score = recent_crosses * 2 + bullish_states
        rows.append({
            "code": item["code"],
            "name": item.get("name", item["code"]),
            "exchange": item.get("exchange_code", ""),
            "dte": item["dte"],
            "expiry": pd.Timestamp(item["expire_time"]).date().isoformat(),
            "underlying": item.get("options_target_code", ""),
            "underlying_price": item.get("underlying_price"),
            "moneyness": item.get("moneyness"),
            "option_type": option_type,
            "strike": item.get("options_exercise_price"),
            "bar_time": bars["datetime"].iloc[-1],
            "last_price": float(bars["close"].iloc[-1]),
            "bars": len(bars),
            "signal_score": signal_score,
            "confirmation_score": (
                signal_score + confirmation["direction_confirmation_count"]
            ),
            **confirmation,
            **underlying_signals.get(item.get("options_target_code"), {}),
            **liquidity,
            **signal,
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["confirmation_score", "signal_score", "dte", "recent_volume", "code"],
        ascending=[False, False, True, False, True],
    ).reset_index(drop=True)
