# coding: utf-8
# ============================================================
# Project_16 fill-vs-open 每日观测包装（2026-10-01 建，ENS 执行时点实验数据链）
# 调度：Quant_P16_FillVsOpen_Track（每日 20:45，S4U）
# 内容：交易日检查 → research/fill_vs_open_track.py 全量重算（只读）→ 失败推告警
# 产物：results/fill_vs_open_track.csv + fill_vs_open_track_latest.md
# 判定口径：specs/ENS执行时点实验预注册_20261001.md（本任务只观测不判定）
# 日志：data/schedules/fill_vs_open_track_<时间戳>.log
# ============================================================
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$logDir = Join-Path $proj "data\schedules"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "fill_vs_open_track_$stamp.log"
function Log($m) { $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

$miniqmtPy = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
if (-not (Test-Path $miniqmtPy)) { Log "!! 未找到 miniqmt Python，观测任务无法运行"; exit 1 }

Push-Location $proj
try {
    & $miniqmtPy "is_trade_day.py" --json *> $null
    if ($LASTEXITCODE -ne 0) { Log "非 A 股交易日，跳过"; Pop-Location; exit 0 }
    Log "交易日，开始 fill-vs-open 全量重算"
    & $miniqmtPy "research\fill_vs_open_track.py" 2>&1 | ForEach-Object { Log $_ }
    $rc = $LASTEXITCODE
    Log "exit=$rc"
    if ($rc -ne 0) {
        & $miniqmtPy "push_alert_card.py" --title "fill-vs-open 观测任务失败" --body "exit=$rc，日志 $log，请人工核查（只读任务，不影响交易）" --level warn 2>&1 | ForEach-Object { Log $_ }
        Pop-Location; exit 1
    }
    Log "[OK] 观测完成"
    Pop-Location
    exit 0
} catch {
    Log "异常: $_"
    & $miniqmtPy "push_alert_card.py" --title "fill-vs-open 观测任务异常" --body "$_（只读任务，不影响交易）" --level warn 2>&1 | ForEach-Object { Log $_ }
    Pop-Location
    exit 1
}
