"""CLI for five-minute commodity futures turnover acceleration radar."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from intraday_radar import annotate_rank_changes, generate_intraday_radar
from quote_api import QuoteClient
from signal_state import load_state, save_state


def _rank_label(row):
    if row.get("entered_top", False):
        return "新进"
    change = row.get("rank_change")
    if pd.isna(change) or int(change) == 0:
        return "—"
    return f"↑{int(change)}" if change > 0 else f"↓{abs(int(change))}"


def _event_label(row):
    events = []
    if row.get("entered_top", False):
        events.append("新进TOP")
    if row.get("exited_top", False):
        events.append("退出TOP")
    if row.get("direction_reversed", False):
        events.append("方向反转")
    return "/".join(events) or "—"


def build_display_table(result):
    """Build compact Chinese intraday turnover and rank-change output."""
    display = pd.DataFrame({
        "排名": result["rank_15m"],
        "排名变化": result.apply(_rank_label, axis=1),
        "事件": result.apply(_event_label, axis=1),
        "代码": result["code"],
        "名称": result["name"],
        "方向": result["side"],
        "15分涨跌%": result["price_change_15m_pct"],
        "5分额(亿)": result["turnover_5m_yi"],
        "15分额(亿)": result["turnover_15m_yi"],
        "60分额(亿)": result["turnover_60m_yi"],
        "15分加速%": result["turnover_acceleration_15m_pct"],
        "持仓5分": result["oi_change_5m"],
        "持仓15分": result["oi_change_15m"],
        "持仓60分": result["oi_change_60m"],
        "截止": pd.to_datetime(result["bar_time"]).dt.strftime("%m-%d %H:%M"),
    })
    for column in (
        "15分涨跌%", "5分额(亿)", "15分额(亿)", "60分额(亿)",
        "15分加速%",
    ):
        display[column] = pd.to_numeric(display[column], errors="coerce").round(2)
    return display


def apply_rank_state(result, state_path, top_n=10):
    previous = load_state(state_path)
    annotated, state = annotate_rank_changes(result, previous, top_n=top_n)
    save_state(state_path, state)
    return annotated


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="国内商品主力5/15/60分钟成交额增速与排名变化雷达"
    )
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--changes-only", action="store_true")
    parser.add_argument(
        "--state-file", type=Path,
        default=Path("output/state/intraday_rank.json"),
    )
    parser.add_argument("--csv", type=Path)
    return parser.parse_args(argv)


def main(argv=None, client=None):
    args = parse_args(argv)
    result = generate_intraday_radar(client or QuoteClient())
    if result.empty:
        print("当前没有足够的完整5分钟行情")
        return 1
    result = apply_rank_state(result, args.state_file, top_n=args.top)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.csv, index=False, encoding="utf-8-sig")
    selected = result
    if args.changes_only:
        meaningful_move = result["rank_change"].abs().fillna(0).ge(3)
        selected = result.loc[
            result["entered_top"] | result["exited_top"]
            | result["direction_reversed"] | meaningful_move
        ]
    else:
        selected = result.head(args.top)
    latest = pd.Timestamp(result["bar_time"].max()).strftime("%Y-%m-%d %H:%M")
    print(
        f"盘中热点雷达｜完整5分钟线截止 {latest}｜有效主力 {len(result)} 个"
    )
    if selected.empty:
        print("本轮没有显著排名或方向变化。")
    else:
        print(build_display_table(selected).to_string(index=False))
    print("\n口径：成交额按完整5分钟K线聚合；加速率为近15分钟相对前15分钟；排名变化相对上一次状态文件。")
    if args.csv:
        print(f"CSV已保存：{args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
