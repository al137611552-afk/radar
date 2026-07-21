# Watchman 期货辅助决策 MVP

当前 MVP 已实现：商品与板块多周期风险调整动量排名、原始动量对照、相对全商品/所属板块超额收益、临期期权与标的双确认扫描、商品主力成交热点、5分钟盘中雷达、自动调度和只读 Web 功能面板。系统只提供研究与辅助决策信号，不连接交易或自动下单。

## Windows 快速验收

Windows 10/11 安装 Python 3.11 或更高版本并克隆仓库后，在项目目录打开 PowerShell。Access Key 只写入当前 PowerShell 进程，不要保存到仓库：

```powershell
$env:QUOTE_API_KEY = "你的 Access Key"
powershell -ExecutionPolicy Bypass -File .\windows_mvp.ps1
```

脚本会创建 `.venv`、安装依赖，依次生成品种/板块动量、临期期权和盘中雷达快照，然后启动 `http://127.0.0.1:8787`。按 `Ctrl+C` 停止面板。首次依赖已安装后可跳过安装：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows_mvp.ps1 -SkipSetup
```

只查看已经生成的快照，不重新请求行情：

```powershell
powershell -ExecutionPolicy Bypass -File .\windows_mvp.ps1 -SkipSetup -SkipScan
```

Windows MVP 验收项和已知边界见 `MVP_ACCEPTANCE.md`。

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
- 板块基准：同板块当前有效品种 N 日收益的等权平均；品种板块超额为品种收益减去板块基准。
- 板块内排名：同板块有效品种按对应周期收益降序排名。
- 板块动量分：各板块5/20/60/120日等权收益在板块横截面中的百分位均值，范围0~100。
- N 日排名：按 N 日收益降序排名。
- 综合动量分：各周期横截面百分位的等权平均，范围 0~100。
- 多头排名：综合动量分从高到低排序，用于识别横截面强势品种。
- 空头排名：综合动量分从低到高排序，用于识别横截面弱势品种；不按收益正负硬切，确保普涨或普跌市场仍保留双向相对强弱候选。
- N 日年化波动率：最近 N 个连续、有限且有效的完整日收益率的样本标准差乘以 `sqrt(252)`，以百分数表示；N=1 时记为 0。最后 N+1 条日线只要存在缺失、无穷或非正收盘价，该品种即从本轮排名中剔除，不跨缺口拼接收益。
- N 日风险调整动量：`N 日收益% / max(N 日年化波动率%, 0.01)`；0.01% 下限用于避免零波动序列产生无穷值。
- 风险调整分：各周期风险调整动量在商品横截面中的百分位均值，范围 0~100；风险多头排名按该分数降序，风险空头排名按该分数升序。
- 波动风险分：各周期年化波动率横截面百分位均值；90 分及以上标记“高波动”，75 分及以上标记“偏高”，其余标记“常态”。这是横截面风险标签，不是绝对风险阈值。
- 板块风险调整指标：先剔除任一参与指标非有限的成员，再对剩余同一组有效成员的风险调整动量、收益和年化波动率分别等权平均，并在板块横截面中评分。该成员均值不等同于考虑协方差后的板块投资组合波动率。
- CLI 与 Web 同时展示原始动量、风险调整动量各自的多头/空头榜；CSV 与 API 同时保留两套分数和两套多空排名，避免一种视角替换另一种。
- 完整K线：交易日尚未结束时自动丢弃正在形成的日线；夜盘标记为下一交易日的部分K线也会被排除。

注意：`6666` 指数的精确编制、换月和展期规则仍需数据接口管理员确认。当前结果适合作为研究/辅助决策信号，不构成投资建议。

板块分类由 `sectors.py` 版本化维护，当前覆盖贵金属、有色金属、新能源材料、黑色、能源化工、油脂油料、谷物、软商品、畜牧、林产建材和航运。新增品种不会被静默丢弃，而会标记为“未分类”，便于数据质量检查。航运当前只有集运欧线一个有效成分，因此其板块收益等于品种收益、板块超额恒为0，不能与多成分板块作同等分散度解释。

同时导出完整品种榜和板块榜：

```bash
.venv/bin/python momentum_cli.py \
  --top 20 \
  --csv output/momentum_latest.csv \
  --sector-csv output/sector_momentum_latest.csv
```

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

自动调度会拆分保存三类期权产物：

- `output/options_candidates_latest.csv`：完整可分析候选池，可包含 `0 / 0` 的无信号合约；
- `output/options_latest.csv`：当前仍然有效的双确认信号，供Dashboard读取；
- `output/options_alerts.csv`：首次命中、新金叉、确认变化或失效等增量告警。

Dashboard中的“期权分 / 确认分”分别对应期权自身技术信号分和加入标的方向确认后的总分。

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

## 交易时段感知自动调度器

调度器会按上海时区运行三项任务，并以“任务 + 逻辑时点”在SQLite中去重：

- `intraday`：日盘及夜盘每根完整5分钟线后运行；
- `options`：交易时段内每个完整时钟小时后运行；
- `momentum`：工作日15:00日盘收盘后运行。

日盘自动处理 `10:15-10:30` 盘间休息、`11:30-13:30` 午休；夜盘按周一至周四晚间及周二至周五凌晨处理。不同品种夜盘收盘时间不一致时，由盘中雷达已有的K线新鲜度过滤继续剔除休市品种。周末自动跳过；法定节假日和节前无夜盘日由 `config/holidays.txt` 维护，夜盘会检查下一交易日是否休市。

单次检查（适合冒烟验证或外部cron）：

```bash
export QUOTE_API_KEY='你的 Access Key'
.venv/bin/python scheduler_cli.py \
  --holidays-file config/holidays.txt \
  run --once
```

前台持续运行：

```bash
.venv/bin/python scheduler_cli.py \
  --holidays-file config/holidays.txt \
  run --poll-seconds 30 --max-attempts 3 --timeout 300 --stale-after 360
```

查看最近运行、最近成功时间、失败原因及盘中历史库覆盖情况：

```bash
.venv/bin/python scheduler_cli.py status
```

手工补跑指定逻辑时点；已成功的时点默认不会重复执行，只有显式 `--force` 才会再次运行并保留新attempt记录：

```bash
.venv/bin/python scheduler_cli.py backfill intraday 2026-07-15T14:55:00+08:00
.venv/bin/python scheduler_cli.py backfill momentum 2026-07-15T15:00:00+08:00 --force
```

运行状态默认保存到 `output/scheduler/runs.db`，任务日志分别流式追加到 `output/logs/`。SQLite使用WAL和事务级claim防止两个调度器重复执行；失败的旧逻辑时点会在后续轮询中优先重试，然后再执行当前时点，达到最大attempt后停止；陈旧锁阈值默认比任务超时多60秒，也可用 `--stale-after` 显式设置，但必须严格大于 `--timeout`，避免正常长任务被并行实例重复claim。单任务超时时会终止整个子进程组，不会阻断其他到期任务。

生产常驻服务模板位于 `deploy/watchman-scheduler.service`。模板默认将只读程序部署到 `/opt/watchman`，使用专用 `watchman` 用户，并通过systemd沙箱仅开放 `/opt/watchman/output` 写权限；不要直接以root运行行情解析任务。安装前需要：

1. 创建不可登录的 `watchman` 系统用户，将项目和虚拟环境部署到 `/opt/watchman`，由root持有程序文件，并让 `watchman` 可读取、仅可写 `output/`；
2. 在 `/etc/watchman/watchman.env` 中配置 `QUOTE_API_KEY=...`，目录和文件仅允许root读取；
3. 按交易所正式日历维护 `/opt/watchman/config/holidays.txt`；
4. 将服务模板复制到systemd目录，执行daemon-reload、enable和start；
5. 用 `/opt/watchman/.venv/bin/python /opt/watchman/scheduler_cli.py status` 和 `/opt/watchman/output/logs/` 验证首轮执行。

## HTML功能面板

项目提供零新增依赖的只读Web面板，聚合盘中成交额、期权信号、品种动量、板块动量、数据新鲜度与自动任务状态。面板每30秒自动刷新，支持手工刷新、功能页切换、代码/名称/板块/交易所搜索和桌面/移动端响应式布局。

```bash
cd /root/watchman
.venv/bin/python dashboard_cli.py
```

浏览器访问 `http://127.0.0.1:8787`。默认仅监听本机，不向浏览器提供API密钥，也不接受任意命令或任务执行请求；所有非空任务错误详情都会在API输出前替换为固定提示，原始错误仅保留在服务端。需要从个人电脑访问远程服务器时，建议使用SSH端口转发：

```bash
ssh -L 8787:127.0.0.1:8787 user@server
```

然后仍访问 `http://127.0.0.1:8787`。不要在没有反向代理认证与访问控制的情况下直接使用 `--host 0.0.0.0` 暴露到公网。

生产只读服务模板位于 `deploy/watchman-dashboard.service`，与调度器使用同一个低权限 `watchman` 用户，且不需要 `QUOTE_API_KEY`。面板读取：

- `output/intraday_latest.csv`
- `output/options_latest.csv`
- `output/momentum_latest.csv`
- `output/sector_momentum_latest.csv`
- `output/scheduler/runs.db`

上述文件尚未生成时，面板会显示等待状态而不是启动失败。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试不访问真实 API，也不需要 Access Key；真实冒烟运行需要通过环境变量注入密钥。
