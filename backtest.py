"""
回测引擎 (backtest)
-------------------------------------------------
把策略放到历史数据上"重放"一遍：
  - 拉取各周期历史K线
  - 在扳机周期上逐根前进，调用 strategy.evaluate
  - 收集信号，并统计信号之后 N 根的涨跌（看准不准）
  - 带去重/冷却，避免同一波重复报

用法:  .venv/bin/python backtest.py ag8888
"""

import sys
import time
import pandas as pd

import quote_api as q
import indicators as ind
import strategy as st


def load(code, interval, days):
    """按时间范围拉取最近 days 天的K线，算好指标。"""
    end = int(time.time())
    start = end - days * 86400
    df = q.get_kline_by_timerange(code, interval, start, end)
    if df.empty:
        return df
    ind.add_common(df)
    df.attrs["tf"] = interval
    return df


def run(code="ag8888", mid_tf="15m", trig_tf="5m",
        days=180, cooldown_min=30, forward=12,
        params: st.StrategyParams = None):
    p = params or st.StrategyParams()

    print(f"拉取 {code} 历史数据 ...")
    weekly = load(code, "week", days + 1500)   # 周线要多拉点历史
    daily = load(code, "day", days + 400)
    mid = load(code, mid_tf, days)
    trig = load(code, trig_tf, days)
    print(f"  周线{len(weekly)} 日线{len(daily)} {mid_tf}{len(mid)} {trig_tf}{len(trig)} 根\n")

    signals = []
    seen_setups = set()   # 同一个W底(以右脚时间标识)只报一次

    for i in range(1, len(trig)):
        sig = st.evaluate(daily, weekly, mid, trig, i, p)
        if sig is None:
            continue
        # 去重：同一个W底形态只报第一次金叉
        setup_id = sig.detail.get("low2_time")
        if setup_id in seen_setups:
            continue
        seen_setups.add(setup_id)

        # 统计信号后 forward 根的最大涨/跌（评估信号质量）
        fwd = trig["close"].iloc[i+1:i+1+forward]
        if len(fwd) > 0:
            up = (fwd.max() - sig.price) / sig.price * 100
            dn = (fwd.min() - sig.price) / sig.price * 100
        else:
            up = dn = float("nan")
        sig.detail["fwd_up%"] = round(up, 2)
        sig.detail["fwd_dn%"] = round(dn, 2)
        signals.append(sig)

    # 输出
    print(f"=== 共触发 {len(signals)} 个做多信号（{trig_tf}扳机，同一W底只报一次）===\n")
    if not signals:
        print("（这段历史里没有满足全部三层条件的信号）")
        return signals

    print(f"{'信号时间':<20}{'价格':>8}{'后续最大涨%':>12}{'后续最大跌%':>12}   说明")
    print("-" * 110)
    win = 0
    for s in signals:
        up, dn = s.detail["fwd_up%"], s.detail["fwd_dn%"]
        if up >= 0.5:   # 简单评估：信号后涨过0.5%算"对"
            win += 1
        print(f"{str(s.time):<20}{s.price:>8.0f}{up:>12}{dn:>12}   {s.reason}")

    print("-" * 110)
    print(f"\n信号后 {forward} 根内涨过 0.5% 的比例: {win}/{len(signals)} = {win/len(signals)*100:.0f}%")
    print("（这只是粗略评估，先看信号位置合不合理，参数后面再调）")
    return signals


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "ag8888"
    run(code)
