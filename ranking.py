"""Cross-sectional momentum and excess-return ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

import numpy as np
import pandas as pd

from sectors import commodity_sector


COMMODITY_EXCHANGES = frozenset({"SHFE", "DCE", "CZCE", "INE", "GFEX"})
RETURN_INDEX_CODE = re.compile(r"^[A-Za-z]+6666$")
TRADING_DAYS_PER_YEAR = 252
VOLATILITY_FLOOR_PCT = 0.01


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
        closes = pd.to_numeric(source["close"], errors="coerce")
        if len(closes) < required:
            continue
        closes = closes.iloc[-required:]
        close_values = closes.to_numpy(dtype=float)
        if not np.isfinite(close_values).all() or (close_values <= 0).any():
            continue
        info = metadata.get(code, {})
        row = {
            "code": code,
            "name": info.get("name", code),
            "exchange": info.get("exchange_code", ""),
            "sector": commodity_sector(code),
            "as_of": source["datetime"].iloc[-1] if "datetime" in source else pd.NaT,
        }
        valid = True
        with np.errstate(all="ignore"):
            for horizon in horizons:
                horizon_return = float(
                    (closes.iloc[-1] / closes.iloc[-horizon - 1] - 1) * 100
                )
                daily_returns = closes.pct_change(fill_method=None).iloc[-horizon:]
                annualized_volatility = (
                    float(
                        daily_returns.std(ddof=1)
                        * (TRADING_DAYS_PER_YEAR ** 0.5)
                        * 100
                    )
                    if len(daily_returns) > 1
                    else 0.0
                )
                risk_adjusted = horizon_return / max(
                    annualized_volatility, VOLATILITY_FLOOR_PCT
                )
                if not np.isfinite(
                    [horizon_return, annualized_volatility, risk_adjusted]
                ).all():
                    valid = False
                    break
                row[f"return_{horizon}d"] = horizon_return
                row[f"annualized_volatility_{horizon}d"] = annualized_volatility
                row[f"risk_adjusted_{horizon}d"] = risk_adjusted
        if valid:
            rows.append(row)

    return_columns = ["code", "name", "exchange", "sector", "as_of"]
    for horizon in horizons:
        return_columns.extend([
            f"return_{horizon}d", f"excess_{horizon}d", f"rank_{horizon}d",
            f"sector_return_{horizon}d", f"sector_excess_{horizon}d",
            f"sector_rank_{horizon}d",
            f"annualized_volatility_{horizon}d", f"risk_adjusted_{horizon}d",
        ])
    return_columns.extend([
        "momentum_score", "long_rank", "short_rank",
        "risk_adjusted_score", "risk_long_rank", "risk_short_rank",
        "volatility_score", "volatility_risk",
    ])
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
        sector_return_col = f"sector_return_{horizon}d"
        sector_excess_col = f"sector_excess_{horizon}d"
        sector_rank_col = f"sector_rank_{horizon}d"
        grouped = result.groupby("sector", sort=False)[return_col]
        result[sector_return_col] = grouped.transform("mean")
        result[sector_excess_col] = result[return_col] - result[sector_return_col]
        result[sector_rank_col] = grouped.rank(
            method="min", ascending=False
        ).astype(int)
        pct_col = f"_pct_{horizon}d"
        result[pct_col] = result[return_col].rank(pct=True)
        percentile_columns.append(pct_col)

    result["momentum_score"] = result[percentile_columns].mean(axis=1) * 100
    result["long_rank"] = result["momentum_score"].rank(
        method="min", ascending=False
    ).astype(int)
    result["short_rank"] = result["momentum_score"].rank(
        method="min", ascending=True
    ).astype(int)
    risk_percentile_columns = []
    for horizon in horizons:
        pct_col = f"_risk_pct_{horizon}d"
        result[pct_col] = result[f"risk_adjusted_{horizon}d"].rank(pct=True)
        risk_percentile_columns.append(pct_col)
    result["risk_adjusted_score"] = (
        result[risk_percentile_columns].mean(axis=1) * 100
    )
    result["risk_long_rank"] = result["risk_adjusted_score"].rank(
        method="min", ascending=False
    ).astype(int)
    result["risk_short_rank"] = result["risk_adjusted_score"].rank(
        method="min", ascending=True
    ).astype(int)
    volatility_percentile_columns = []
    for horizon in horizons:
        pct_col = f"_volatility_pct_{horizon}d"
        result[pct_col] = result[f"annualized_volatility_{horizon}d"].rank(
            pct=True
        )
        volatility_percentile_columns.append(pct_col)
    result["volatility_score"] = (
        result[volatility_percentile_columns].mean(axis=1) * 100
    )
    result["volatility_risk"] = result["volatility_score"].map(
        lambda score: "高波动" if score >= 90 else "偏高" if score >= 75 else "常态"
    )
    result = result.sort_values(
        ["momentum_score", f"return_{horizons[0]}d", "code"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return result[return_columns]


def build_sector_ranking(
    product_ranking: pd.DataFrame,
    horizons: Sequence[int] = (5, 20, 60, 120),
) -> pd.DataFrame:
    """Aggregate product returns into an equal-weight sector momentum leaderboard."""
    horizons = tuple(dict.fromkeys(int(value) for value in horizons))
    if not horizons or any(value < 1 for value in horizons):
        raise ValueError("horizons must contain positive integers")
    columns = ["sector", "constituents", "as_of"]
    for horizon in horizons:
        columns.extend([
            f"sector_return_{horizon}d", f"sector_rank_{horizon}d",
            f"sector_mean_annualized_volatility_{horizon}d",
            f"sector_risk_adjusted_{horizon}d",
        ])
    columns.extend([
        "sector_momentum_score", "sector_long_rank", "sector_short_rank",
        "sector_risk_adjusted_score", "sector_risk_long_rank",
        "sector_risk_short_rank", "sector_volatility_score",
        "sector_volatility_risk",
    ])
    if product_ranking.empty:
        return pd.DataFrame(columns=columns)

    required = {"sector", "code", "as_of"} | {
        f"return_{horizon}d" for horizon in horizons
    } | {
        f"annualized_volatility_{horizon}d" for horizon in horizons
    } | {
        f"risk_adjusted_{horizon}d" for horizon in horizons
    }
    missing = sorted(required - set(product_ranking.columns))
    if missing:
        raise ValueError(f"product ranking missing columns: {', '.join(missing)}")

    numeric_columns = [
        column for column in required if column not in {"sector", "code", "as_of"}
    ]
    valid_products = product_ranking.copy()
    valid_products.loc[:, numeric_columns] = valid_products.loc[
        :, numeric_columns
    ].apply(pd.to_numeric, errors="coerce")
    finite_mask = np.isfinite(
        valid_products.loc[:, numeric_columns].to_numpy(dtype=float)
    ).all(axis=1)
    identity_mask = (
        valid_products["sector"].notna()
        & valid_products["code"].notna()
        & valid_products["as_of"].notna()
    )
    valid_products = valid_products.loc[finite_mask & identity_mask].copy()
    if valid_products.empty:
        return pd.DataFrame(columns=columns)

    aggregations = {
        "constituents": ("code", "nunique"),
        "as_of": ("as_of", "max"),
    }
    for horizon in horizons:
        aggregations[f"sector_return_{horizon}d"] = (
            f"return_{horizon}d", "mean"
        )
        aggregations[f"sector_mean_annualized_volatility_{horizon}d"] = (
            f"annualized_volatility_{horizon}d", "mean"
        )
        aggregations[f"sector_risk_adjusted_{horizon}d"] = (
            f"risk_adjusted_{horizon}d", "mean"
        )
    result = pd.DataFrame(
        valid_products.groupby("sector", as_index=False).agg(**aggregations)
    )
    percentile_columns = []
    for horizon in horizons:
        return_col = f"sector_return_{horizon}d"
        rank_col = f"sector_rank_{horizon}d"
        values = pd.Series(result.loc[:, return_col])
        result.loc[:, rank_col] = values.rank(
            method="min", ascending=False
        ).astype(int)
        pct_col = f"_pct_{horizon}d"
        result.loc[:, pct_col] = values.rank(pct=True)
        percentile_columns.append(pct_col)
    result.loc[:, "sector_momentum_score"] = (
        result.loc[:, percentile_columns].mean(axis=1) * 100
    )
    result.loc[:, "sector_long_rank"] = result[
        "sector_momentum_score"
    ].rank(method="min", ascending=False).astype(int)
    result.loc[:, "sector_short_rank"] = result[
        "sector_momentum_score"
    ].rank(method="min", ascending=True).astype(int)
    risk_percentile_columns = []
    volatility_percentile_columns = []
    for horizon in horizons:
        risk_pct_col = f"_sector_risk_pct_{horizon}d"
        result.loc[:, risk_pct_col] = result[
            f"sector_risk_adjusted_{horizon}d"
        ].rank(pct=True)
        risk_percentile_columns.append(risk_pct_col)
        volatility_pct_col = f"_sector_volatility_pct_{horizon}d"
        result.loc[:, volatility_pct_col] = result[
            f"sector_mean_annualized_volatility_{horizon}d"
        ].rank(pct=True)
        volatility_percentile_columns.append(volatility_pct_col)
    result.loc[:, "sector_risk_adjusted_score"] = (
        result.loc[:, risk_percentile_columns].mean(axis=1) * 100
    )
    result.loc[:, "sector_risk_long_rank"] = result[
        "sector_risk_adjusted_score"
    ].rank(method="min", ascending=False).astype(int)
    result.loc[:, "sector_risk_short_rank"] = result[
        "sector_risk_adjusted_score"
    ].rank(method="min", ascending=True).astype(int)
    result.loc[:, "sector_volatility_score"] = (
        result.loc[:, volatility_percentile_columns].mean(axis=1) * 100
    )
    result.loc[:, "sector_volatility_risk"] = result[
        "sector_volatility_score"
    ].map(
        lambda score: "高波动" if score >= 90 else "偏高" if score >= 75 else "常态"
    )
    result = result.sort_values(
        by=["sector_momentum_score", f"sector_return_{horizons[0]}d", "sector"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return result.loc[:, columns].copy()
