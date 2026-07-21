# Watchman MVP 验收说明

## MVP 边界

本版本完成“行情扫描 → 指标排名/筛选 → CSV 快照 → Web 查看”的只读闭环：

1. 国内商品 `6666` 收益率指数的 5/20/60/120 日动量排名；
2. 品种相对全商品等权基准和所属板块等权基准的超额收益；
3. 板块内排名及独立板块动量榜；
4. `1 <= DTE < 15` 的临期商品期权小时 MA/MACD 信号及标的方向确认；
5. 商品主力日频热点、5分钟盘中成交额雷达和 SQLite 历史快照；
6. 交易时段感知的自动调度、失败重试和运行状态；
7. 本机只读 Web 面板，展示盘中、期权、品种动量、板块动量及任务状态。

MVP 不包含自动下单、账户接入、IV/Greeks、买卖盘口、系统化回测、多用户权限或公网部署。

## Windows 验收步骤

1. 安装 Python 3.11+，执行 `python --version` 或 `py -3 --version` 确认。
2. 在项目目录 PowerShell 中设置当前进程环境变量：`$env:QUOTE_API_KEY = "..."`。
3. 执行 `powershell -ExecutionPolicy Bypass -File .\windows_mvp.ps1`。
4. 确认三个扫描步骤均为退出码 0，且浏览器可打开 `http://127.0.0.1:8787`。
5. 在“市场总览”确认盘中合约、期权信号、动量品种和动量板块计数有数据。
6. 在“动量排名”确认可看到板块及“20日板块超额”。
7. 在“板块动量”确认板块、成分数、动量分及四个周期收益均可显示。
8. 使用搜索框输入品种代码、中文名或板块名，确认列表会过滤。
9. 点击“刷新”，确认页面无报错；按 `Ctrl+C` 停止服务。

## 预期产物

- `output/momentum_latest.csv`
- `output/sector_momentum_latest.csv`
- `output/options_candidates_latest.csv`
- `output/options_latest.csv`
- `output/intraday_latest.csv`
- `output/history/radar.db`

这些均为本地运行产物，已被 Git 忽略。

## 已知边界

- Quote API 使用自签名证书；客户端仅对指定 API 请求禁用证书校验。
- `6666` 指数的精确换月和展期规则仍需数据接口管理员确认。
- Quote API 尚未提供已确认可用的盘口、隐含波动率和 Greeks；期权流动性主要依据 K 线成交量、持仓量和新鲜度。
- 非交易时段运行盘中或期权扫描时，结果可能为空或沿用最近完整 K 线；应核对页面“数据截止”和新鲜度。
- Web 服务默认只监听 `127.0.0.1`，不要直接暴露到公网。
