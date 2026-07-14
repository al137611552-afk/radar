"""CLI for near-expiry commodity option hourly MA/MACD scanning."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from option_scanner import scan_near_expiry_options
from quote_api import QuoteClient


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
        "MA状态": [
            _signal_label(bullish, bars)
            for bullish, bars in zip(
                result["ma_bullish"], result["ma_cross_bars_ago"]
            )
        ],
        "MACD状态": [
            _signal_label(bullish, bars)
            for bullish, bars in zip(
                result["macd_bullish"], result["macd_cross_bars_ago"]
            )
        ],
        "信号分": result["signal_score"],
    })
    for column in ("最新价", "近20小时量", "持仓量"):
        display[column] = pd.to_numeric(display[column], errors="coerce").round(2)
    return display


def filter_signal_mode(result: pd.DataFrame, mode: str) -> pd.DataFrame:
    if result.empty or mode == "all":
        return result
    if mode == "recent":
        mask = result["ma_cross_bars_ago"].notna() | result[
            "macd_cross_bars_ago"
        ].notna()
    elif mode == "bullish":
        mask = result["ma_bullish"] | result["macd_bullish"]
    else:
        raise ValueError(f"unsupported mode: {mode}")
    return result.loc[mask].reset_index(drop=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="扫描1至14天到期、平值附近且流动性合格的商品期权小时金叉"
    )
    parser.add_argument("--mode", choices=("recent", "bullish", "all"),
                        default="recent", help="recent=近3根金叉，bullish=多头，all=全部")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--min-dte", type=int, default=1)
    parser.add_argument("--max-dte", type=int, default=15,
                        help="到期天数上限（不含）")
    parser.add_argument("--strikes", type=int, default=3,
                        help="每个标的、每个购沽方向保留的近平值档数")
    parser.add_argument("--max-moneyness", type=float, default=0.15)
    parser.add_argument("--min-volume", type=float, default=100)
    parser.add_argument("--min-open-interest", type=float, default=100)
    parser.add_argument("--csv", type=Path)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = scan_near_expiry_options(
        QuoteClient(), min_dte=args.min_dte, max_dte=args.max_dte,
        strikes_per_side=args.strikes, max_moneyness=args.max_moneyness,
        min_volume=args.min_volume, min_open_interest=args.min_open_interest,
    )
    filtered = filter_signal_mode(result, args.mode)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        filtered.to_csv(args.csv, index=False, encoding="utf-8-sig")
    print(
        f"临期期权可分析 {len(result)} 个；模式 {args.mode} 命中 {len(filtered)} 个"
    )
    if filtered.empty:
        print("当前没有符合条件的期权。")
        return 0
    print(build_display_table(filtered.head(args.top)).to_string(index=False))
    if args.csv:
        print(f"CSV已保存：{args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
