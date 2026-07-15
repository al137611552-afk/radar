"""Command-line entry point for commodity momentum ranking."""

from __future__ import annotations

import argparse

import pandas as pd

from atomic_io import atomic_to_csv
from quote_api import QuoteClient
from ranking import generate_ranking

DEFAULT_HORIZONS = (5, 20, 60, 120)


def build_display_table(result: pd.DataFrame, horizons=DEFAULT_HORIZONS):
    columns = ["code", "name", "as_of"]
    names = {
        "code": "代码", "name": "名称", "as_of": "数据截止",
        "momentum_score": "综合动量分",
    }
    for horizon in horizons:
        columns.extend([
            f"return_{horizon}d", f"excess_{horizon}d", f"rank_{horizon}d"
        ])
        names.update({
            f"return_{horizon}d": f"{horizon}日收益%",
            f"excess_{horizon}d": f"{horizon}日超额%",
            f"rank_{horizon}d": f"{horizon}日排名",
        })
    columns.append("momentum_score")
    display = result.loc[:, columns].rename(columns=names).copy()
    display["数据截止"] = pd.to_datetime(display["数据截止"]).dt.strftime("%Y-%m-%d")
    float_columns = display.select_dtypes(include="number").columns
    percent_columns = [
        column for column in float_columns
        if "收益%" in column or "超额%" in column or column == "综合动量分"
    ]
    display[percent_columns] = display[percent_columns].round(2)
    return display


def parse_horizons(value):
    horizons = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not horizons or any(item < 1 for item in horizons):
        raise argparse.ArgumentTypeError("周期必须是逗号分隔的正整数")
    return horizons


def main(argv=None):
    parser = argparse.ArgumentParser(description="商品收益率指数动量与超额收益排名")
    parser.add_argument("--top", type=int, default=20, help="显示前N名；0表示全部")
    parser.add_argument(
        "--horizons", type=parse_horizons, default=DEFAULT_HORIZONS,
        help="回看交易日周期，默认5,20,60,120",
    )
    parser.add_argument("--csv", help="可选：保存完整排名CSV的路径")
    args = parser.parse_args(argv)

    try:
        result = generate_ranking(QuoteClient(), horizons=args.horizons)
    except ValueError as exc:
        parser.error(str(exc))

    if result.empty:
        print("没有足够数据生成排名。")
        return 1
    if args.csv:
        atomic_to_csv(result, args.csv, index=False, encoding="utf-8-sig")
    display = build_display_table(result, args.horizons)
    if args.top > 0:
        display = display.head(args.top)
    print(display.to_string(index=False))
    print(f"\n有效商品品种：{len(result)}；超额收益基准：当前有效品种等权平均。")
    if args.csv:
        print(f"完整结果已保存：{args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
