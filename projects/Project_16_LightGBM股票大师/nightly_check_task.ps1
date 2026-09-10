# coding: utf-8
# Project_16 夜间检修计划任务包装（T-20260910-006，每日 01:00）
# 背景：nightly_check.py 体检脚本（A-G 模块：数据/面板/模型/数据源/QMT/环境/报告）
#       自 2026-09-03 建立以来无计划任务调度，纯手动/agent 会话跑。
# 挂载后：每日 01:00 自动体检 + --fix 自动修复（面板重刷/增量重下/熔断恢复），
#         FAIL 时 exit 1（LastTaskResult 可观察），报告落 data/cache/nightly_check_<date>.md。
# 注：--push-alert 未启用（推送由检修指令按报告执行飞书推送，脚本内当前无实际推送实现）。
# 注：不设 $ErrorActionPreference = "Stop"——PS5.1 下 Native 命令(python) stderr 经重定向会触发
# ErrorRecord，Stop 策略会静默终止脚本（17:17 首跑失败的根因，T-20260910-006）。run_scheduled.ps1 同口径。
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$log = Join-Path $proj "data\schedules\nightly_check_task.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

# Python 探测（与 run_scheduled.ps1 Get-GoodPy 同口径：需带 numpy/lightgbm）
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
$cands = @()
$p = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($p) { $cands += $p }
if (Test-Path $FALLBACK_PY) { $cands += $FALLBACK_PY }
foreach ($c in $cands) {
    try {
        $v = & $c -c "import numpy, lightgbm; print('OK')" 2>$null | Select-Object -Last 1
        if ($v -eq 'OK') { $py = $c; break }
    } catch { }
}
if (-not $py) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] !! 未找到带 numpy/lightgbm 的 Python" -Encoding UTF8
    exit 1
}

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== 夜间检修开始（fix 开启）====" -Encoding UTF8
& $py -u (Join-Path $proj "scripts\nightly_check.py") --fix *>> $log
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] nightly_check exit=$rc（非 0 = 有 FAIL 项）" -Encoding UTF8
exit $rc
