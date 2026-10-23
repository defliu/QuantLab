# 每日模拟盘 —— 推进到行情最新交易日（Project_23）
#
# 时间：每工作日 20:45（错开 16:30 行情刷新、20:30 V13_PaperParallel）
# 依赖：quant_daily_update（16:30 刷新 D:/astock 增量 + P16 incremental_daily.parquet）
# 只做模拟撮合与净值记录，**永不真实下单**。
#
# 交易日门禁（两层）：
#   1. schtasks DaysOfWeek 过滤周末
#   2. run_paper.py --mode step 内部以「行情库最新交易日」为准，非交易日自动 SKIP
#
# 退出码：0=成功或无新交易日 / 1=执行失败 / 2=数据源异常
# 注意：本文件含中文，必须保持 UTF-8 BOM（AGENTS.md 红线 T-20260903-015）

$ErrorActionPreference = "Continue"
# 编码统一：Python 输出按 UTF-8 解码，避免 PowerShell 5.1 默认 ANSI 造成中文乱码
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$PROJ = "D:\QuantLab\projects\Project_23_每日模拟盘"
$LOG_DIR = Join-Path $PROJ "logs"
$PY = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"

if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG = Join-Path $LOG_DIR "paper_step_$stamp.log"

# 解释器兜底（与 P16 范式一致）
if (-not (Test-Path $PY)) {
    $PY = "python"
    "[WARN] miniqmt venv 不存在，回退系统 python" | Out-File $LOG -Encoding utf8
}

Set-Location $PROJ
"=== Project_23 paper step $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $LOG -Encoding utf8 -Append

& $PY "run_paper.py" --mode step 2>&1 | Out-File $LOG -Encoding utf8 -Append
$code = $LASTEXITCODE
"=== exit $code ===" | Out-File $LOG -Encoding utf8 -Append

# 失败/异常时推送告警（推送失败只记录，不阻断）
if ($code -ne 0) {
    $tail = (Get-Content $LOG -Tail 25 -Encoding utf8) -join "`n"
    $msg = "Project_23 模拟盘推进异常 (exit $code)`n$tail"
    try {
        & $PY (Join-Path $PROJ "..\Project_16_LightGBM股票大师\push_alert_card.py") `
            --title "模拟盘推进异常" --content $msg 2>&1 | Out-File $LOG -Encoding utf8 -Append
    } catch {
        "[WARN] 告警推送失败: $_" | Out-File $LOG -Encoding utf8 -Append
    }
}

exit $code
