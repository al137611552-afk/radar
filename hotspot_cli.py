"""CLI and standalone HTML heatmap for commodity futures hotspots."""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

import pandas as pd

from hotspot_radar import generate_hotspot_radar
from quote_api import QuoteClient


def build_side_table(result: pd.DataFrame, side: str, top=10) -> pd.DataFrame:
    selected = result.loc[result["side"] == side].sort_values(
        ["turnover_yi", "code"], ascending=[False, True]
    ).head(top)
    display = selected[[
        "side_rank", "code", "name", "turnover_yi", "change_pct", "oi_change",
        "oi_change_pct", "position_action", "turnover_share_pct",
    ]].rename(columns={
        "side_rank": "排名", "code": "代码", "name": "名称",
        "turnover_yi": "成交额(亿)", "change_pct": "涨跌%",
        "oi_change": "持仓变化", "oi_change_pct": "持仓变化%",
        "position_action": "资金结构", "turnover_share_pct": "市场占比%",
    }).reset_index(drop=True)
    for column in ("成交额(亿)", "涨跌%", "持仓变化%", "市场占比%"):
        display[column] = pd.to_numeric(display[column], errors="coerce").round(2)
    for column in ("持仓变化", "持仓变化%"):
        display[column] = display[column].where(display[column].notna(), "—")
    return display


def _heat_tiles(rows, css_class):
    if rows.empty:
        return '<div class="empty">暂无数据</div>'
    median = max(float(rows["turnover_yi"].median()), 0.01)
    tiles = []
    for _, row in rows.iterrows():
        weight = min(5.0, max(1.0, float(row["turnover_yi"]) / median))
        intensity = min(0.92, 0.34 + abs(float(row["change_pct"])) / 8)
        oi_text = (
            "不比较" if pd.isna(row["oi_change"])
            else f'{float(row["oi_change"]):+,.0f}'
        )
        tiles.append(
            f'<div class="tile {css_class}" style="flex-grow:{weight:.2f};--intensity:{intensity:.2f}">'
            f'<div class="tile-head"><b>{escape(str(row["name"]))}</b>'
            f'<span>{escape(str(row["code"]))}</span></div>'
            f'<div class="change">{float(row["change_pct"]):+.2f}%</div>'
            f'<div class="money">{float(row["turnover_yi"]):,.2f} 亿</div>'
            f'<div class="meta">{escape(str(row["position_action"]))} · '
            f'持仓 {oi_text}</div></div>'
        )
    return "".join(tiles)


def render_heatmap_html(result: pd.DataFrame, title="商品期货热点雷达") -> str:
    """Render a dependency-free heatmap sized by turnover and colored by direction."""
    bullish = result.loc[result["side"] == "多"].sort_values("turnover_yi", ascending=False)
    bearish = result.loc[result["side"] == "空"].sort_values("turnover_yi", ascending=False)
    trade_date = ""
    if "trade_date" in result and not result.empty:
        trade_date = escape(str(result["trade_date"].max()))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#090d14;color:#e8edf5;font-family:Inter,"PingFang SC",sans-serif}}
main{{max-width:1500px;margin:auto;padding:28px}}header{{display:flex;justify-content:space-between;align-items:end;margin-bottom:22px}}
h1{{margin:0;font-size:30px}}.sub{{color:#8994a8;font-size:13px}}.legend{{display:flex;gap:14px;color:#aeb8c8;font-size:13px}}
.dot{{width:9px;height:9px;display:inline-block;border-radius:50%;margin-right:5px}}.red{{background:#ef4444}}.green{{background:#16a34a}}
.panel{{background:#101722;border:1px solid #202b3b;border-radius:16px;padding:17px;margin:16px 0;box-shadow:0 12px 32px #0005}}
.panel h2{{font-size:17px;margin:0 0 13px}}.heat{{display:flex;flex-wrap:wrap;gap:8px;min-height:120px}}
.tile{{flex-basis:180px;min-width:160px;min-height:128px;border-radius:11px;padding:14px;border:1px solid #ffffff18;display:flex;flex-direction:column;justify-content:space-between}}
.tile.bull{{background:rgba(220,38,38,var(--intensity))}}.tile.bear{{background:rgba(22,139,65,var(--intensity))}}
.tile-head{{display:flex;justify-content:space-between;gap:10px}}.tile-head span{{font-size:11px;opacity:.75}}.change{{font-size:27px;font-weight:750}}
.money{{font-size:15px;font-weight:650}}.meta{{font-size:11px;opacity:.78}}.empty{{color:#657187;padding:30px}}
.note{{color:#69768b;font-size:12px;line-height:1.7;margin-top:18px}}
@media(max-width:650px){{main{{padding:15px}}header{{display:block}}.legend{{margin-top:10px}}.tile{{min-width:140px;flex-basis:140px}}}}
</style></head><body><main>
<header><div><h1>{escape(title)}</h1><div class="sub">交易日 {trade_date} · 按成交额排序，颜色深浅代表涨跌幅</div></div>
<div class="legend"><span><i class="dot red"></i>多头热点</span><span><i class="dot green"></i>空头热点</span></div></header>
<section class="panel"><h2>多头成交额热点</h2><div class="heat">{_heat_tiles(bullish, "bull")}</div></section>
<section class="panel"><h2>空头成交额热点</h2><div class="heat">{_heat_tiles(bearish, "bear")}</div></section>
<div class="note">说明：多/空按上一交易日结算价优先、收盘价回退的涨跌方向划分，不代表逐笔主动买卖量；“资金结构”结合价格变化与持仓量变化。主力切换时不比较跨合约持仓变化。</div>
</main></body></html>'''


def _write_text(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(
        description="国内商品期货当日成交额多空热点雷达"
    )
    parser.add_argument("--top", type=int, default=10, help="多空榜各显示数量")
    parser.add_argument("--csv", help="完整排名CSV输出路径")
    parser.add_argument("--html", help="独立HTML热力图输出路径")
    args = parser.parse_args(argv)

    result = generate_hotspot_radar(client or QuoteClient())
    if result.empty:
        print("当前没有可用的商品期货热点数据")
        return 1

    trade_date = result["trade_date"].max()
    print(f"商品期货热点雷达｜交易日 {trade_date}｜主力合约 {len(result)} 个")
    print("\n=== 多头成交额TOP ===")
    bullish = build_side_table(result, "多", args.top)
    print(bullish.to_string(index=False) if not bullish.empty else "暂无多头热点")
    print("\n=== 空头成交额TOP ===")
    bearish = build_side_table(result, "空", args.top)
    print(bearish.to_string(index=False) if not bearish.empty else "暂无空头热点")
    print("\n口径：多空按上一交易日结算价优先、收盘价回退的涨跌划分；主力切换时不比较跨合约持仓变化；非逐笔主动买卖量。")

    if args.csv:
        target = Path(args.csv)
        target.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(target, index=False, encoding="utf-8-sig")
        print(f"CSV已保存：{target}")
    if args.html:
        _write_text(args.html, render_heatmap_html(result))
        print(f"HTML热力图已保存：{Path(args.html)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
