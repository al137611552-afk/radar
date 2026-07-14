"""
三层策略引擎 (strategy)
-------------------------------------------------
把"方向 -> 环境 -> 扳机"的漏斗逻辑固化成代码。

对应你定义的形态（第1版，做多）：
  第一层 定方向(日线+周线):
      日线收盘 > 日线EMA20  且  周线收盘 > 周线EMA20
      且 近 N 根日线里有过回踩：日线最低价碰到 EMA20 的 ±1%
  第二层 看够格(15m 或 1h):
      出现 W 底，且 价格还没突破颈线(启动前)
  第三层 抓扳机(5m，回测用5m):
      MA5 上穿 MA10 金叉

关键：评估某个扳机时刻 T 时，
  - 方向层只用 T 之前【已完整收盘】的日线/周线
  - 环境层只用 datetime < T 的 15m/1h K线
  - 扳机层用当前这根 5m（它已收盘）
绝不使用 T 之后的信息。
"""

from dataclasses import dataclass, field
import pandas as pd
import indicators as ind


@dataclass
class StrategyParams:
    ema_period: int = 20          # 多空线
    pullback_tol: float = 0.01    # 回踩到EMA的±1%
    pullback_window: int = 3      # 近N根日线内有过回踩即可
    w_lookback: int = 120         # W底回看根数
    w_low_tol: float = 0.015      # 两低点容差1.5%
    w_min_gap: int = 5
    w_max_gap: int = 40
    neck_buffer: float = 0.0      # 价格<颈线*(1+buffer)才算"启动前"；0=还没破颈线
    w_right_recent: int = 20      # W底右脚必须在最近N根内成型（确保是"刚启动"）
    w_above_low2: float = 0.002   # 价格须高于右脚至少0.2%（确认已回升、非接飞刀）


@dataclass
class Signal:
    time: pd.Timestamp
    price: float
    direction: str
    reason: str
    detail: dict = field(default_factory=dict)


def _last_closed_idx(df, t):
    """返回 df 中 datetime < t 的最后一根的位置下标；没有则 -1。"""
    # df 已按时间升序
    pos = df["datetime"].searchsorted(t, side="left") - 1
    return int(pos)


def check_direction(daily, weekly, t, p: StrategyParams):
    """方向层。返回 (ok, detail)。"""
    di = _last_closed_idx(daily, t)
    wi = _last_closed_idx(weekly, t)
    if di < p.pullback_window or wi < 1:
        return False, {}

    d_close = daily["close"].iloc[di]
    d_ema = daily["ema20"].iloc[di]
    w_close = weekly["close"].iloc[wi]
    w_ema = weekly["ema20"].iloc[wi]
    if pd.isna(d_ema) or pd.isna(w_ema):
        return False, {}

    # 多头排列：日线、周线都站上EMA20
    if not (d_close > d_ema and w_close > w_ema):
        return False, {}

    # 近 N 根日线里有过回踩（最低价碰到EMA20 ±tol）
    pulled = False
    for k in range(di - p.pullback_window + 1, di + 1):
        lo = daily["low"].iloc[k]
        ema = daily["ema20"].iloc[k]
        if pd.isna(ema):
            continue
        if abs(lo - ema) / ema <= p.pullback_tol:
            pulled = True
            break
    if not pulled:
        return False, {}

    return True, {
        "d_close": float(d_close), "d_ema20": float(d_ema),
        "w_close": float(w_close), "w_ema20": float(w_ema),
    }


def check_setup(mid, t, p: StrategyParams):
    """环境层(15m或1h)。在 datetime<t 的最后一根上找W底+未破颈线。"""
    idx = _last_closed_idx(mid, t)
    if idx < p.w_min_gap + 2:
        return False, {}
    w = ind.find_w_bottom(mid, idx, lookback=p.w_lookback,
                          low_tol=p.w_low_tol,
                          min_gap=p.w_min_gap, max_gap=p.w_max_gap)
    if not w:
        return False, {}
    price = mid["close"].iloc[idx]

    # (a) 右脚必须新：W底刚成型，避免拿很久以前的旧W底
    if idx - w["low2_i"] > p.w_right_recent:
        return False, {}
    # (b) 已回升：价格高于右脚一定幅度，确认从底部turn up，而不是还在下跌(接飞刀)
    if price < w["low2"] * (1 + p.w_above_low2):
        return False, {}
    # (c) 启动前：价格还没突破颈线
    if price > w["neckline"] * (1 + p.neck_buffer):
        return False, {}

    w["setup_tf_time"] = mid["datetime"].iloc[idx]
    w["setup_price"] = float(price)
    w["low2_time"] = mid["datetime"].iloc[w["low2_i"]]
    return True, w


def evaluate(daily, weekly, mid, trig, i, p: StrategyParams):
    """
    在扳机周期(trig)的第 i 根K线上做完整三层判断。
    daily/weekly/mid/trig 都需已 add_common() 算好指标。
    返回 Signal 或 None。
    """
    t = trig["datetime"].iloc[i]

    # 第三层先判（最便宜）：当前这根是否金叉
    if not ind.golden_cross_at(trig, i):
        return None

    # 第一层：方向
    ok_dir, dir_detail = check_direction(daily, weekly, t, p)
    if not ok_dir:
        return None

    # 第二层：环境
    ok_setup, setup_detail = check_setup(mid, t, p)
    if not ok_setup:
        return None

    price = float(trig["close"].iloc[i])
    reason = (f"周/日多头({dir_detail['d_close']:.0f}>EMA{dir_detail['d_ema20']:.0f}) | "
              f"{mid.attrs.get('tf','中周期')}W底未破颈线{setup_detail['neckline']:.0f} | "
              f"{trig.attrs.get('tf','小周期')}MA5上穿MA10")
    return Signal(time=t, price=price, direction="long", reason=reason,
                  detail={**dir_detail, **setup_detail})
