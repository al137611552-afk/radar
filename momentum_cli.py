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
        "risk_adjusted_score": "风险调整分",
        "risk_long_rank": "风险多头排名", "risk_short_rank": "风险空头排名",
        "volatility_score": "波动风险分", "volatility_risk": "波动风险",
    }
    for horizon in horizons:
        columns.extend([
            f"return_{horizon}d", f"excess_{horizon}d", f"rank_{horizon}d",
            f"sector_return_{horizon}d", f"sector_excess_{horizon}d",
            f"sector_rank_{horizon}d",
            f"annualized_volatility_{horizon}d", f"risk_adjusted_{horizon}d",
        ])
        names.update({
            f"return_{horizon}d": f"{horizon}日收益%",
            f"excess_{horizon}d": f"{horizon}日全商品超额%",
            f"rank_{horizon}d": f"{horizon}日排名",
            f"sector_return_{horizon}d": f"{horizon}日板块收益%",
            f"sector_excess_{horizon}d": f"{horizon}日板块超额%",
            f"sector_rank_{horizon}d": f"{horizon}日板块排名",
            f"annualized_volatility_{horizon}d": f"{horizon}日年化波动%",
            f"risk_adjusted_{horizon}d": f"{horizon}日风险调整",
        })
    columns.extend([
        "momentum_score", "long_rank", "short_rank", "risk_adjusted_score",
        "risk_long_rank", "risk_short_rank", "volatility_score",
        "volatility_risk",
    ])
    display = result.loc[:, columns].rename(columns=names).copy()
    display["数据截止"] = pd.to_datetime(display["数据截止"]).dt.strftime("%Y-%m-%d")
    float_columns = display.select_dtypes(include="number").columns
    percent_columns = [
        column for column in float_columns
        if "收益%" in column or "超额%" in column or "波动%" in column
        or "风险调整" in column or column in {"综合动量分", "波动风险分"}
    ]
    display[percent_columns] = display[percent_columns].round(2)
    return display


def build_sector_display_table(result: pd.DataFrame, horizons=DEFAULT_HORIZONS):
    columns = ["sector", "constituents", "as_of"]
    names = {
        "sector": "板块", "constituents": "品种数", "as_of": "数据截止",
        "sector_momentum_score": "板块动量分",
        "sector_long_rank": "多头排名", "sector_short_rank": "空头排名",
        "sector_risk_adjusted_score": "板块风险调整分",
        "sector_risk_long_rank": "风险多头排名",
        "sector_risk_short_rank": "风险空头排名",
        "sector_volatility_score": "波动风险分",
        "sector_volatility_risk": "波动风险",
    }
    for horizon in horizons:
        columns.extend([
            f"sector_return_{horizon}d", f"sector_rank_{horizon}d",
            f"sector_mean_annualized_volatility_{horizon}d",
            f"sector_risk_adjusted_{horizon}d",
        ])
        names.update({
            f"sector_return_{horizon}d": f"{horizon}日板块收益%",
            f"sector_rank_{horizon}d": f"{horizon}日板块排名",
            f"sector_mean_annualized_volatility_{horizon}d": (
                f"{horizon}日成员平均年化波动%"
            ),
            f"sector_risk_adjusted_{horizon}d": f"{horizon}日板块风险调整",
        })
    columns.extend([
        "sector_momentum_score", "sector_long_rank", "sector_short_rank",
        "sector_risk_adjusted_score", "sector_risk_long_rank",
        "sector_risk_short_rank", "sector_volatility_score",
        "sector_volatility_risk",
    ])
    display = result.loc[:, columns].rename(columns=names).copy()
    display["数据截止"] = pd.to_datetime(display["数据截止"]).dt.strftime("%Y-%m-%d")
    float_columns = display.select_dtypes(include="number").columns
    rounded = [
        column for column in float_columns
        if "收益%" in column or "波动%" in column or "风险调整" in column
        or column in {"板块动量分", "波动风险分"}
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
    def ranked(table, rank_column, tie_column):
        ordered = table.sort_values([rank_column, tie_column])
        return ordered.head(args.top) if args.top > 0 else ordered

    original_long = ranked(display, "多头排名", "代码")
    original_short = ranked(display, "空头排名", "代码")
    risk_long = ranked(display, "风险多头排名", "代码")
    risk_short = ranked(display, "风险空头排名", "代码")
    print("原始动量多头强势榜：")
    print(original_long.to_string(index=False))
    print("\n原始动量空头弱势榜：")
    print(original_short.to_string(index=False))
    print("风险调整多头强势榜：")
    print(risk_long.to_string(index=False))
    print("\n风险调整空头弱势榜：")
    print(risk_short.to_string(index=False))
    sector_display = build_sector_display_table(sector_result, args.horizons)
    sector_original_long = ranked(sector_display, "多头排名", "板块")
    sector_original_short = ranked(sector_display, "空头排名", "板块")
    sector_risk_long = ranked(sector_display, "风险多头排名", "板块")
    sector_risk_short = ranked(sector_display, "风险空头排名", "板块")
    print("\n板块原始动量多头强势榜：")
    print(sector_original_long.to_string(index=False))
    print("\n板块原始动量空头弱势榜：")
    print(sector_original_short.to_string(index=False))
    print("\n板块风险调整多头强势榜：")
    print(sector_risk_long.to_string(index=False))
    print("\n板块风险调整空头弱势榜：")
    print(sector_risk_short.to_string(index=False))
    print(
        f"\n有效商品品种：{len(result)}；板块：{len(sector_result)}；"
        "全商品与板块基准均为当前有效成分等权平均；风险调整榜按收益/"
        "年化波动率的横截面百分位排序。"
    )
    if args.csv:
        print(f"完整结果已保存：{args.csv}")
    if args.sector_csv:
        print(f"板块结果已保存：{args.sector_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
