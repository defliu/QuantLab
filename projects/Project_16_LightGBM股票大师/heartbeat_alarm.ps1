# coding: utf-8
# 桥心跳超时告警（2026-09-12 注册 Quant_P16_Heartbeat_Alarm，DE 复查 T-20260912-002 回提）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File heartbeat_alarm.ps1
# 调度: schtasks 每 30 分钟（脚本自身过滤交易日 + 09:30-15:10 盘中窗口）
param()
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $proj "data\schedules"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "heartbeat_alarm_$stamp.log"
# T-20261012-001：Python 选择修正（与 run_scheduled.ps1 / nightly_check_task.ps1 同批）。
# 原逻辑是「Test-Path $FALLBACK_PY 成立就直接采用、不做任何可执行测试」，而本机
# $FALLBACK_PY（TRAE SOLO 内置）已损坏（init_fs_encoding / No module named 'encodings'）
# → 每次运行都选中它并在执行 check_bridge_heartbeat.py 时崩溃（2026-10-12 10:00 日志实证），
# 且因 $py 已被赋值，下方 Get-Command python 分支永不触发，心跳告警静默失效。
# 修正：候选（miniqmt venv 首选 → PATH python → FALLBACK_PY）逐个做真实可执行测试，
# 测过才采用；全测不过才 exit 1。
$MINIQMT_PY = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
$cands = @()
if (Test-Path $MINIQMT_PY) { $cands += $MINIQMT_PY }
$p = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($p) { $cands += $p }
if (Test-Path $FALLBACK_PY) { $cands += $FALLBACK_PY }
foreach ($c in $cands) {
    try {
        $v = & $c -c "import numpy, lightgbm; print('OK')" 2>$null | Select-Object -Last 1
        if ($v -eq 'OK') { $py = $c; break }
    } catch { }
}
if (-not $py) { Write-Host "!! 未找到可用 Python，心跳告警无法运行"; exit 1 }
Add-Content -Path $log -Value ("{0}  heartbeat_alarm 使用 Python: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $py) -Encoding UTF8
& $py (Join-Path $proj "check_bridge_heartbeat.py") 2>&1 | ForEach-Object { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $_) -Encoding UTF8 }
exit $LASTEXITCODE
