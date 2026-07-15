"""CLI for SQLite-backed intraday hotspot persistence analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from history_store import (
    load_intraday_history,
    load_rank_trajectory,
    summarize_hotspot_persistence,
)


def build_persistence_table(summary):
    display = pd.DataFrame({
        "排名": summary["latest_rank"],
        "代码": summary["code"],
        "名称": summary["name"],
        "方向": summary["side"],
        "状态": summary["persistence_status"],
        "排名改善": summary["rank_improvement"],
        "连续TOP": summary["top_streak"],
        "入榜次数": summary["top_appearances"],
        "15分额(亿)": summary["turnover_15m"] / 100_000_000,
        "历史增长%": summary["turnover_growth_pct"],
        "当前加速%": summary["turnover_acceleration_15m_pct"],
    })
    for column in ("15分额(亿)", "历史增长%", "当前加速%"):
        display[column] = pd.to_numeric(display[column], errors="coerce").round(2)
    return display


def build_trajectory_table(trajectory):
    display = pd.DataFrame({
        "扫描时间": pd.to_datetime(trajectory["scan_time"]).dt.strftime("%m-%d %H:%M"),
        "K线截止": pd.to_datetime(trajectory["bar_time"]).dt.strftime("%m-%d %H:%M"),
        "排名": trajectory["rank_15m"],
        "方向": trajectory["side"],
        "15分涨跌%": trajectory["price_change_15m_pct"],
        "15分额(亿)": trajectory["turnover_15m"] / 100_000_000,
        "15分加速%": trajectory["turnover_acceleration_15m_pct"],
    })
    for column in ("15分涨跌%", "15分额(亿)", "15分加速%"):
        display[column] = pd.to_numeric(display[column], errors="coerce").round(2)
    return display


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="盘中热点SQLite历史与持续性分析")
    parser.add_argument(
        "--db", type=Path, default=Path("output/history/radar.db")
    )
    parser.add_argument("--top", type=_positive_int, default=15)
    parser.add_argument("--snapshots", type=_positive_int, default=12)
    parser.add_argument("--pulse-threshold", type=float, default=100.0)
    parser.add_argument("--code", help="查看单个合约的排名轨迹")
    parser.add_argument("--limit", type=_positive_int, default=20)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.code:
        trajectory = load_rank_trajectory(args.db, args.code, limit=args.limit)
        if trajectory.empty:
            print(f"没有找到 {args.code} 的历史快照：{args.db}")
            return 1
        print(f"{args.code} 盘中排名轨迹｜最近 {len(trajectory)} 次扫描")
        print(build_trajectory_table(trajectory).to_string(index=False))
        return 0

    history = load_intraday_history(args.db)
    if history.empty:
        print(f"历史数据库为空或不存在：{args.db}")
        return 1
    summary = summarize_hotspot_persistence(
        history,
        top_n=args.top,
        lookback_snapshots=args.snapshots,
        pulse_threshold_pct=args.pulse_threshold,
    )
    selected = summary.loc[summary["latest_rank"].le(args.top)]
    scan_count = min(args.snapshots, history["scan_time"].nunique())
    print(
        f"盘中热点持续性｜最近 {scan_count} 次扫描｜当前Top {args.top}"
    )
    print(build_persistence_table(selected).to_string(index=False))
    print(
        "\n分类：连续入榜至少3次为持续热点；排名改善至少2名且成交额增长为持续升温；"
        "首次/短期入榜且当前加速超过阈值为脉冲热点。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
