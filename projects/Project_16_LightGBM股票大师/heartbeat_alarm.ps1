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
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
if (Test-Path $FALLBACK_PY) { $py = $FALLBACK_PY }
if (-not $py) {
    $p = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($p) {
        try {
            $v = & $p -c "import sys; print('OK')" 2>$null | Select-Object -Last 1
            if ($v -eq 'OK') { $py = $p }
        } catch { }
    }
}
if (-not $py) { Write-Host "!! 未找到可用 Python，心跳告警无法运行"; exit 1 }
Add-Content -Path $log -Value ("{0}  heartbeat_alarm 使用 Python: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $py) -Encoding UTF8
& $py (Join-Path $proj "check_bridge_heartbeat.py") 2>&1 | ForEach-Object { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $_) -Encoding UTF8 }
exit $LASTEXITCODE
