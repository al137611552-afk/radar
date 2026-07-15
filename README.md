# Watchman 期货辅助工具（阶段 0）

当前已实现：商品多周期动量排名、临期期权与标的双确认扫描，以及国内商品主力合约的当日成交额热点雷达。

## 运行

```bash
cd /root/watchman
export QUOTE_API_KEY='你的 Access Key'
.venv/bin/python momentum_cli.py --top 20
```

保存完整 CSV：

```bash
.venv/bin/python momentum_cli.py --top 20 --csv output/momentum_latest.csv
```

自定义周期：

```bash
.venv/bin/python momentum_cli.py --horizons 5,10,20,60 --top 30
```

## 计算口径

- 标的池：SHFE、DCE、CZCE、INE、GFEX 中 `variety_type=7` 且代码严格以 `6666` 结尾的正式商品收益率指数；自动排除测试代码和 CFFEX 金融期货。
- N 日收益：`最新完整日线收盘 / N 个交易日前收盘 - 1`。
- N 日超额：品种 N 日收益减去当前有效商品池的等权平均 N 日收益。
- N 日排名：按 N 日收益降序排名。
- 综合动量分：各周期横截面百分位的等权平均，范围 0~100。
- 完整K线：交易日尚未结束时自动丢弃正在形成的日线；夜盘标记为下一交易日的部分K线也会被排除。

注意：`6666` 指数的精确编制、换月和展期规则仍需数据接口管理员确认。当前结果适合作为研究/辅助决策信号，不构成投资建议。

## 临期期权小时金叉扫描

默认筛选自然日到期天数 `1 <= DTE < 15`、平值附近、近20根小时K线成交与持仓合格的商品期权，并同时计算期权及标的期货的 MA5/MA20 与 MACD(12,26,9)。看涨期权要求标的处于多头方向，看跌期权要求标的处于空头方向。API 的小时K线时间戳按结束时间处理，尚未结束的小时线不会参与信号。

```bash
export QUOTE_API_KEY='你的 Access Key'
.venv/bin/python option_cli.py --mode double --top 30
```

模式：

- `double`：期权最近3根完整小时线内出现金叉，且与标的期货方向一致（默认）
- `recent`：期权MA或MACD在最近3根完整小时线内金叉
- `bullish`：MA或MACD当前处于多头状态
- `all`：显示所有通过流动性筛选的近平值期权

导出CSV：

```bash
.venv/bin/python option_cli.py --mode double --csv output/options_latest.csv
```

只输出首次命中、新金叉、确认变化和信号失效，避免定时扫描重复提醒：

```bash
.venv/bin/python option_cli.py \
  --mode double \
  --new-only \
  --state-file output/state/options.json \
  --csv output/options_alerts.csv
```

首次使用会把当前全部命中标记为“首次命中”；之后相同信号保持静默。状态按扫描模式隔离，JSON采用原子替换写入，运行产物不会提交到Git。

可用 `--strikes` 控制每个标的每个购沽方向保留的近平值档数，使用 `--min-volume`、`--min-open-interest` 调整流动性门槛。

## 商品期货热点雷达

热点雷达批量读取国内商品交易所当前主力合约最近两个交易日的数据，按当日成交额分别生成多头和空头排行榜，同时结合持仓量变化展示价仓四象限。系统会查询上一交易日主力映射并识别换月，避免跨合约比较持仓。可同时导出完整 CSV 和无需外部依赖的 HTML 热力图：

```bash
export QUOTE_API_KEY='你的 Access Key'
.venv/bin/python hotspot_cli.py \
  --top 10 \
  --csv output/hotspot_latest.csv \
  --html output/hotspot_latest.html
```

指标口径：

- 标的池：SHFE、DCE、CZCE、INE、GFEX 当前主力商品期货；排除金融期货和境外合约。
- 成交额：优先使用 Quote API 日K线原生 `money` 字段；字段缺失时才按 `收盘价 × 成交量 × 合约乘数` 估算。
- 涨跌基准：优先使用上一交易日结算价；接口未提供结算价时回退到上一收盘价。CSV 的 `reference_price_type` 会明确记录实际口径。
- 多头热点：相对涨跌基准上涨的品种，按当日成交额降序。
- 空头热点：相对涨跌基准下跌的品种，按当日成交额降序。
- 夜盘扫描时，仅纳入已产生当前交易日K线的品种，避免把无夜盘品种的上一交易日成交额混入实时榜单。
- 价涨仓增：多头增仓；价跌仓增：空头增仓；价涨仓减：空头减仓；价跌仓减：多头减仓。
- 主力换月：`main_switched=true` 时标记“主力切换”，保留当前持仓量，但不计算跨合约持仓变化及价仓四象限。
- HTML 中红色代表上涨、绿色代表下跌，颜色深浅随涨跌幅变化，卡片按成交额排序。

重要：行情接口只提供总成交额和总持仓量，没有逐笔主动买卖方向或席位净多/净空数据。因此雷达中的“多/空”是价格方向与价仓结构代理，不应理解为真实净多资金或真实净空资金。

## 盘中成交额增速与排名变化

盘中雷达一次批量读取国内商品主力的5分钟K线，仅使用扫描时刻已经结束的K线，计算近5、15、60个交易分钟的成交额和持仓变化，并按近15分钟成交额排名：

```bash
export QUOTE_API_KEY='你的 Access Key'
.venv/bin/python intraday_cli.py \
  --top 15 \
  --state-file output/state/intraday_rank.json \
  --csv output/intraday_latest.csv
```

再次扫描时，状态文件用于显示排名上升、下降、新进Top N、退出Top N及15分钟价格方向反转。只查看显著变化：

```bash
.venv/bin/python intraday_cli.py \
  --top 15 \
  --changes-only \
  --state-file output/state/intraday_rank.json
```

指标口径：

- `5分额/15分额/60分额`：最近1、3、12根完整5分钟K线成交额之和。
- `15分加速%`：最近15分钟成交额相对之前15分钟的变化率。
- `15分涨跌%`：最新收盘价相对3根K线前收盘价的变化。
- `持仓5分/15分/60分`：最新持仓量相对1、3、12根K线前的变化。
- 仅保留与全市场最新完整K线相差不超过15分钟的品种，避免休市或无夜盘品种混入实时榜单。
- 首次运行会将当前Top N标记为“新进”；后续相同榜单保持静默，状态文件不会提交到Git。

### SQLite历史快照与热点持续性

盘中雷达默认把每次完整横截面写入 `output/history/radar.db`。快照以“最新完整5分钟K线时间 + 合约代码”为唯一键；同一根K线重复扫描会更新原记录，不会产生重复数据。若某次只想查看实时榜而不落库，可使用 `--no-history`：

```bash
.venv/bin/python intraday_cli.py \
  --top 15 \
  --history-db output/history/radar.db

.venv/bin/python intraday_cli.py --top 15 --no-history
```

分析最近12次快照中的热点持续性：

```bash
.venv/bin/python history_cli.py \
  --db output/history/radar.db \
  --top 15 \
  --snapshots 12
```

查看单个主力合约最近20次扫描的排名轨迹：

```bash
.venv/bin/python history_cli.py \
  --db output/history/radar.db \
  --code lc2609 \
  --limit 20
```

持续性分类口径：

- `持续升温`：连续至少3次位于Top N，排名改善至少2名，且区间15分钟成交额增长为正。
- `持续热点`：连续至少3次位于Top N，但不满足持续升温条件。
- `脉冲热点`：短期/首次入榜，当前15分钟成交额加速率达到阈值（默认100%）。
- `新晋热点`：当前进入Top N，但暂不满足以上条件。
- `热点降温`：最新排名已跌出Top N；该状态保留在底层分析结果中，默认Top N表不展示。

历史数据库、WAL文件和状态文件都位于已忽略的运行产物目录，不会提交到Git。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试不访问真实 API，也不需要 Access Key；真实冒烟运行需要通过环境变量注入密钥。
