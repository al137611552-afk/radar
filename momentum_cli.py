"""Command-line entry point for commodity momentum ranking."""

from __future__ import annotations

import argparse

import pandas as pd

from atomic_io import atomic_to_csv
from quote_api import QuoteClient
from ranking import build_sector_ranking, generate_ranking

DEFAULT_HORIZONS = (5, 20, 60, 120)


def build_display_table(result: pd.DataFrame, horizons=DEFAULT_HORIZONS):
    columns = ["code", "name", "sector", "as_of"]
    names = {
        "code": "代码", "name": "名称", "sector": "板块", "as_of": "数据截止",
        "momentum_score": "综合动量分",
        "long_rank": "多头排名", "short_rank": "空头排名",
    }
    for horizon in horizons:
        columns.extend([
            f"return_{horizon}d", f"excess_{horizon}d", f"rank_{horizon}d",
            f"sector_return_{horizon}d", f"sector_excess_{horizon}d",
            f"sector_rank_{horizon}d",
        ])
        names.update({
            f"return_{horizon}d": f"{horizon}日收益%",
            f"excess_{horizon}d": f"{horizon}日全商品超额%",
            f"rank_{horizon}d": f"{horizon}日排名",
            f"sector_return_{horizon}d": f"{horizon}日板块收益%",
            f"sector_excess_{horizon}d": f"{horizon}日板块超额%",
            f"sector_rank_{horizon}d": f"{horizon}日板块排名",
        })
    columns.extend(["momentum_score", "long_rank", "short_rank"])
    display = result.loc[:, columns].rename(columns=names).copy()
    display["数据截止"] = pd.to_datetime(display["数据截止"]).dt.strftime("%Y-%m-%d")
    float_columns = display.select_dtypes(include="number").columns
    percent_columns = [
        column for column in float_columns
        if "收益%" in column or "超额%" in column or column == "综合动量分"
    ]
    display[percent_columns] = display[percent_columns].round(2)
    return display


def build_sector_display_table(result: pd.DataFrame, horizons=DEFAULT_HORIZONS):
    columns = ["sector", "constituents", "as_of"]
    names = {
        "sector": "板块", "constituents": "品种数", "as_of": "数据截止",
        "sector_momentum_score": "板块动量分",
        "sector_long_rank": "多头排名", "sector_short_rank": "空头排名",
    }
    for horizon in horizons:
        columns.extend([
            f"sector_return_{horizon}d", f"sector_rank_{horizon}d"
        ])
        names.update({
            f"sector_return_{horizon}d": f"{horizon}日板块收益%",
            f"sector_rank_{horizon}d": f"{horizon}日板块排名",
        })
    columns.extend([
        "sector_momentum_score", "sector_long_rank", "sector_short_rank"
    ])
    display = result.loc[:, columns].rename(columns=names).copy()
    display["数据截止"] = pd.to_datetime(display["数据截止"]).dt.strftime("%Y-%m-%d")
    float_columns = display.select_dtypes(include="number").columns
    rounded = [
        column for column in float_columns
        if "收益%" in column or column == "板块动量分"
    ]
    display[rounded] = display[rounded].round(2)
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
    parser.add_argument("--sector-csv", help="可选：保存板块动量榜CSV的路径")
    args = parser.parse_args(argv)

    try:
        result = generate_ranking(QuoteClient(), horizons=args.horizons)
    except ValueError as exc:
        parser.error(str(exc))

    if result.empty:
        print("没有足够数据生成排名。")
        return 1
    sector_result = build_sector_ranking(result, args.horizons)
    if args.csv:
        atomic_to_csv(result, args.csv, index=False, encoding="utf-8-sig")
    if args.sector_csv:
        atomic_to_csv(
            sector_result, args.sector_csv, index=False, encoding="utf-8-sig"
        )
    display = build_display_table(result, args.horizons)
    long_display = display.sort_values(["多头排名", "代码"])
    short_display = display.sort_values(["空头排名", "代码"])
    if args.top > 0:
        long_display = long_display.head(args.top)
        short_display = short_display.head(args.top)
    print("多头强势榜：")
    print(long_display.to_string(index=False))
    print("\n空头弱势榜：")
    print(short_display.to_string(index=False))
    sector_display = build_sector_display_table(sector_result, args.horizons)
    print("\n板块多头强势榜：")
    print(
        sector_display.sort_values(["多头排名", "板块"]).to_string(index=False)
    )
    print("\n板块空头弱势榜：")
    print(
        sector_display.sort_values(["空头排名", "板块"]).to_string(index=False)
    )
    print(
        f"\n有效商品品种：{len(result)}；板块：{len(sector_result)}；"
        "全商品与板块基准均为当前有效成分等权平均。"
    )
    if args.csv:
        print(f"完整结果已保存：{args.csv}")
    if args.sector_csv:
        print(f"板块结果已保存：{args.sector_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
