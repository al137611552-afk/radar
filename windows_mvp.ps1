param(
    [switch]$SkipSetup,
    [switch]$SkipScan
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not $SkipSetup) {
    if (-not (Test-Path $Python)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            py -3 -m venv .venv
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            python -m venv .venv
        } else {
            throw "未找到 Python。请安装 Python 3.11 或更高版本。"
        }
    }
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r requirements.txt
}

if (-not (Test-Path $Python)) {
    throw "未找到 .venv。请先移除 -SkipSetup 运行一次。"
}

if (-not $SkipScan) {
    if ([string]::IsNullOrWhiteSpace($env:QUOTE_API_KEY)) {
        throw '请先在当前 PowerShell 设置 $env:QUOTE_API_KEY。'
    }

    New-Item -ItemType Directory -Force -Path output | Out-Null
    Write-Host "[1/3] 生成品种与板块动量快照..." -ForegroundColor Cyan
    & $Python momentum_cli.py --top 20 --csv output/momentum_latest.csv --sector-csv output/sector_momentum_latest.csv
    if ($LASTEXITCODE -ne 0) { throw "动量扫描失败，退出码 $LASTEXITCODE" }

    Write-Host "[2/3] 生成临期期权快照..." -ForegroundColor Cyan
    & $Python option_cli.py --mode double --snapshot-csv output/options_latest.csv --top 30
    if ($LASTEXITCODE -ne 0) { throw "期权扫描失败，退出码 $LASTEXITCODE" }

    Write-Host "[3/3] 生成盘中雷达快照..." -ForegroundColor Cyan
    & $Python intraday_cli.py --top 15 --state-file output/state/intraday_rank.json --csv output/intraday_latest.csv
    if ($LASTEXITCODE -ne 0) { throw "盘中扫描失败，退出码 $LASTEXITCODE" }
}

Write-Host "启动只读面板：http://127.0.0.1:8787（Ctrl+C 停止）" -ForegroundColor Green
& $Python dashboard_cli.py
exit $LASTEXITCODE
