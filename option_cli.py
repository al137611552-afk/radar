"""CLI for near-expiry commodity option hourly MA/MACD scanning."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from atomic_io import atomic_to_csv
from option_history_store import save_option_snapshot
from option_scanner import scan_near_expiry_options
from quote_api import QuoteClient
from signal_state import diff_signals, load_state, save_state


SIGNAL_FINGERPRINT_FIELDS = (
    "ma_cross_time", "macd_cross_time", "double_confirmed",
    "ma_direction_confirmed", "macd_direction_confirmed",
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _signal_label(bullish, bars_ago):
    if pd.notna(bars_ago):
        bars_ago = int(bars_ago)
        return "刚金叉" if bars_ago == 0 else f"{bars_ago}根前金叉"
    return "多头" if bool(bullish) else "空头"


def build_display_table(result: pd.DataFrame) -> pd.DataFrame:
    """Build the compact Chinese terminal view."""
    display = pd.DataFrame({
        "代码": result["code"],
        "DTE": result["dte"],
        "类型": result["option_type"].map({"CALL": "购", "PUT": "沽"}).fillna(
            result["option_type"]
        ),
        "行权价": result["strike"],
        "虚实值%": (result["moneyness"] * 100).round(2),
        "最新价": result["last_price"],
        "小时线截止": pd.to_datetime(result["bar_time"]).dt.strftime("%m-%d %H:%M"),
        "近20小时量": result["recent_volume"],
        "持仓量": result["open_interest"],
        "期权MA": [
            _signal_label(bullish, bars)
            for bullish, bars in zip(
                result["ma_bullish"], result["ma_cross_bars_ago"]
            )
        ],
        "期权MACD": [
            _signal_label(bullish, bars)
            for bullish, bars in zip(
                result["macd_bullish"], result["macd_cross_bars_ago"]
            )
        ],
        "标的MA": [
            _signal_label(bullish, bars)
            for bullish, bars in zip(
                result["underlying_ma_bullish"],
                result["underlying_ma_cross_bars_ago"],
            )
        ],
        "标的MACD": [
            _signal_label(bullish, bars)
            for bullish, bars in zip(
                result["underlying_macd_bullish"],
                result["underlying_macd_cross_bars_ago"],
            )
        ],
        "双确认": result["double_confirmed"].map({True: "是", False: "否"}),
        "确认分": result["confirmation_score"],
    })
    if "alert_type" in result:
        display.insert(0, "告警", result["alert_type"])
    for column in ("最新价", "近20小时量", "持仓量"):
        display[column] = pd.to_numeric(display[column], errors="coerce").round(2)
    return display


def filter_signal_mode(result: pd.DataFrame, mode: str) -> pd.DataFrame:
    if result.empty or mode == "all":
        return result
    if mode == "double":
        recent = result["ma_cross_bars_ago"].notna() | result[
            "macd_cross_bars_ago"
        ].notna()
        mask = result["double_confirmed"] & recent
    elif mode == "recent":
        mask = result["ma_cross_bars_ago"].notna() | result[
            "macd_cross_bars_ago"
        ].notna()
    elif mode == "bullish":
        mask = result["ma_bullish"] | result["macd_bullish"]
    else:
        raise ValueError(f"unsupported mode: {mode}")
    return result.loc[mask].reset_index(drop=True)


def filter_incremental_signals(result, mode, state_path):
    previous = load_state(state_path)
    alerts, state = diff_signals(
        result, previous, fingerprint_fields=SIGNAL_FINGERPRINT_FIELDS,
        scope=f"option:{mode}",
    )
    save_state(state_path, state)
    return alerts


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="扫描1至14天到期、平值附近且流动性合格的商品期权小时金叉"
    )
    parser.add_argument(
        "--mode", choices=("double", "recent", "bullish", "all"),
        default="double",
        help="double=期权与标的方向双确认，recent=近3根金叉，bullish=多头，all=全部",
    )
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--min-dte", type=int, default=1)
    parser.add_argument("--max-dte", type=int, default=15,
                        help="到期天数上限（不含）")
    parser.add_argument("--strikes", type=int, default=3,
                        help="每个标的、每个购沽方向保留的近平值档数")
    parser.add_argument("--max-moneyness", type=float, default=0.15)
    parser.add_argument("--min-volume", type=float, default=100)
    parser.add_argument("--min-open-interest", type=float, default=100)
    parser.add_argument(
        "--new-only", action="store_true",
        help="只显示首次命中、新金叉、确认变化和信号失效",
    )
    parser.add_argument(
        "--state-file", type=Path,
        default=Path("output/state/options.json"),
        help="增量告警状态文件",
    )
    parser.add_argument(
        "--snapshot-csv", type=Path,
        help="保存未经过模式和增量过滤的完整期权候选池",
    )
    parser.add_argument(
        "--filtered-csv", type=Path,
        help="保存经过模式过滤、但未经过增量过滤的当前有效信号",
    )
    parser.add_argument("--csv", type=Path)
    parser.add_argument(
        "--history-db", type=Path,
        default=Path("output/history/options.db"),
        help="保存完整候选池的SQLite小时快照",
    )
    parser.add_argument(
        "--no-history", action="store_true",
        help="本轮不写入SQLite历史快照",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logical_slot = os.environ.get("WATCHMAN_LOGICAL_SLOT")
    scan_kwargs = {
        "min_dte": args.min_dte,
        "max_dte": args.max_dte,
        "strikes_per_side": args.strikes,
        "max_moneyness": args.max_moneyness,
        "min_volume": args.min_volume,
        "min_open_interest": args.min_open_interest,
    }
    if logical_slot:
        scan_kwargs["now"] = pd.Timestamp(logical_slot)
    snapshot = scan_near_expiry_options(QuoteClient(), **scan_kwargs)
    if args.snapshot_csv:
        atomic_to_csv(snapshot, args.snapshot_csv, index=False, encoding="utf-8-sig")
    history_count = 0
    if not args.no_history:
        if not logical_slot and snapshot.empty:
            logical_slot = datetime.now(SHANGHAI).replace(
                minute=0, second=0, microsecond=0
            )
        history_count = save_option_snapshot(
            args.history_db,
            snapshot,
            scan_time=logical_slot,
        )
    filtered = filter_signal_mode(snapshot, args.mode)
    matched_count = len(filtered)
    if args.filtered_csv:
        atomic_to_csv(filtered, args.filtered_csv, index=False, encoding="utf-8-sig")
    if args.new_only:
        filtered = filter_incremental_signals(
            filtered, args.mode, args.state_file
        )
    if args.csv:
        atomic_to_csv(filtered, args.csv, index=False, encoding="utf-8-sig")
    summary = (
        f"临期期权可分析 {len(snapshot)} 个；模式 {args.mode} 命中 {matched_count} 个"
    )
    if args.new_only:
        summary += f"；新增或变化 {len(filtered)} 个"
    print(summary)
    if not args.no_history:
        print(f"历史快照已保存：{args.history_db}（{history_count}条）")
    if filtered.empty:
        print(
            "当前没有新增或变化的信号。" if args.new_only
            else "当前没有符合条件的期权。"
        )
        return 0
    print(build_display_table(filtered.head(args.top)).to_string(index=False))
    if args.csv:
        print(f"CSV已保存：{args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
