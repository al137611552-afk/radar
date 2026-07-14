"""
指标与形态识别 (indicators)
-------------------------------------------------
全部是确定性计算，不依赖大模型。每个函数只用"已收盘"的数据，
绝不使用未来信息（无未来函数），这样回测才可信。
"""

import pandas as pd
import numpy as np


def add_ma(df, period, kind="ma"):
    """给 df 增加一列均线。kind: 'ma'=简单均线, 'ema'=指数均线。"""
    col = f"{kind}{period}"
    if kind == "ema":
        df[col] = df["close"].ewm(span=period, adjust=False).mean()
    else:
        df[col] = df["close"].rolling(period).mean()
    return df


def add_common(df):
    """一次性加上本策略要用的所有指标。"""
    add_ma(df, 20, "ema")   # 多空线
    add_ma(df, 5, "ma")     # 金叉快线
    add_ma(df, 10, "ma")    # 金叉慢线
    return df


def golden_cross_at(df, i):
    """第 i 根K线是否发生 MA5 上穿 MA10 的金叉（在 i 这根收盘确认）。"""
    if i < 1:
        return False
    a_prev, b_prev = df["ma5"].iloc[i-1], df["ma10"].iloc[i-1]
    a_now, b_now = df["ma5"].iloc[i], df["ma10"].iloc[i]
    if pd.isna(a_prev) or pd.isna(b_prev) or pd.isna(a_now) or pd.isna(b_now):
        return False
    # 上一根 快线<=慢线，这一根 快线>慢线 = 金叉
    return a_prev <= b_prev and a_now > b_now


def find_w_bottom(df, end_i, lookback=60, low_tol=0.015, min_gap=5, max_gap=40):
    """
    在 df 的 [end_i-lookback, end_i] 窗口里找 W 底。
    只用 end_i 及之前的数据（无未来函数）。

    W 底定义（第1版，可调）：
      - 两个低点 L1、L2，L2 不低于 L1（不创新低），两者价差 <= low_tol(默认1.5%)
      - 两低点之间有一个中间高点 = 颈线 neckline
      - 两低点间隔在 [min_gap, max_gap] 根之间

    返回 dict(low1_i, low2_i, neck_i, neckline) 或 None。
    """
    if end_i < min_gap + 2:
        return None
    lo = max(1, end_i - lookback)
    seg = df.iloc[lo:end_i + 1].reset_index()  # 'index' 列保留原始下标
    if len(seg) < min_gap + 3:
        return None

    lows = seg["low"].values
    n = len(lows)

    # 找局部低点：比左右各2根都低
    pivots = []
    for k in range(2, n - 2):
        if lows[k] <= lows[k-1] and lows[k] <= lows[k-2] \
           and lows[k] <= lows[k+1] and lows[k] <= lows[k+2]:
            pivots.append(k)

    if len(pivots) < 2:
        return None

    # 取最近的两个低点组合，找符合条件的 W
    best = None
    for a in range(len(pivots)):
        for b in range(a + 1, len(pivots)):
            i1, i2 = pivots[a], pivots[b]
            gap = i2 - i1
            if gap < min_gap or gap > max_gap:
                continue
            l1, l2 = lows[i1], lows[i2]
            if l2 < l1 * (1 - low_tol):        # 第二低点明显创新低，不算W
                continue
            if abs(l2 - l1) / l1 > low_tol:    # 两低点差太大
                continue
            # 中间高点（颈线）—— 必须落在两个低点之间
            mid = seg["high"].iloc[i1:i2 + 1]
            neck = float(mid.max())
            neck_pos = int(mid.idxmax())          # seg 内的位置下标
            # 颈线要明显高于两个低点，否则只是横盘
            if neck < max(l1, l2) * 1.005:
                continue
            cand = {
                "low1_i": int(seg["index"].iloc[i1]),
                "low2_i": int(seg["index"].iloc[i2]),
                "neck_i": int(seg["index"].iloc[neck_pos]),
                "neckline": neck,
                "low1": float(l1), "low2": float(l2),
            }
            # 偏好"第二低点更靠近现在"的最新W底
            if best is None or cand["low2_i"] > best["low2_i"]:
                best = cand
    return best
