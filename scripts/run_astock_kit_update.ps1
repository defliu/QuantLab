# astock_kit 在线补充数据每日更新包装脚本 (Windows 计划任务入口)
# 工作日下午盘后 17:30 执行, 落地 data/astock_kit_data/<YYYYMMDD>/
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$py = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
$script = "D:\QuantLab\scripts\update_astock_kit.py"

if (-not (Test-Path $py)) { Write-Error "miniqmt python 不存在: $py"; exit 2 }
if (-not (Test-Path $script)) { Write-Error "更新脚本不存在: $script"; exit 2 }

& $py $script
exit $LASTEXITCODE
