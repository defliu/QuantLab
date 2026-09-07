# coding: utf-8
# ============================================================
# P16 F2 主数据源（Tushare moneyflow）每日 19:30 维护任务（2026-09-03 建）
# 步骤：
#   1) 读 D:/QuantLab/config/data_source_keys.json 的 sources.tushare.token；
#      若为"待填"/空 → 仅输出"Tushare token 未配置，跳过刷新"并结束
#   2) token 已填 → 运行 D:/QuantLab/scripts/refresh_tushare_moneyflow.py 增量刷新
#      （依赖 tushare/pandas/pyarrow，缺失时脚本自动 pip 安装到当前 python；勿手动干预 parquet）
#   3) 运行后读 moneyflow.parquet 的 index trade_date 最大值，报告：刷新是否成功 / 最新日期 /
#      相对今日滞后天数 / 总行数
#   4) 最新日期滞后 >2 天（非 T-1）→ 额外输出告警（刷新可能异常）
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File refresh_tushare_moneyflow_daily.ps1
#   powershell ... -File refresh_tushare_moneyflow_daily.ps1 -CheckOnly   # 只做 token 检查+报告，不刷新
# 日志: data/schedules/refresh_tushare_moneyflow_<时间戳>.log
# ============================================================
param([switch]$CheckOnly)
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$logDir = Join-Path $proj "data\schedules"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$log = Join-Path $logDir "refresh_tushare_moneyflow_$stamp.log"
function Log($m) { $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

$miniqmtPy = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
if (Test-Path $miniqmtPy) { $py = $miniqmtPy }
elseif (Test-Path $FALLBACK_PY) { $py = $FALLBACK_PY }
if (-not $py) { Log "!! 未找到 Python（miniqmt / TRAE SOLO），刷新任务无法运行"; exit 1 }
Log "使用 Python: $py"

$KEY = "D:/QuantLab/config/data_source_keys.json"
$SCRIPT = "D:/QuantLab/scripts/refresh_tushare_moneyflow.py"
$PARQUET = "D:/astock/moneyflow/moneyflow.parquet"

# ---- 步骤1：token 检查 ----
$tk = ""
try {
    if (Test-Path $KEY) {
        $cfg = Get-Content $KEY -Raw -Encoding UTF8 | ConvertFrom-Json
        $tk = $cfg.sources.tushare.token
    }
} catch { Log "  !! 读取 $KEY 失败: $_" }
if (-not $tk -or $tk -eq "待填") {
    Log "Tushare token 未配置，跳过刷新"
    exit 0
}
Log "Tushare token 已配置，开始 moneyflow 增量刷新"

# ---- 步骤2：增量刷新 ----
$refreshExit = 0
if ($CheckOnly) {
    Log "[CheckOnly] 跳过刷新执行（仅检查）"
} else {
    Log "  >> python $SCRIPT"
    & $py $SCRIPT 2>&1 | ForEach-Object { Log $_ }
    $refreshExit = $LASTEXITCODE
    Log "  刷新脚本退出码=$refreshExit"
}

# ---- 步骤3：读 parquet 报告 ----
$report = ""
try {
    $r = & $py -c "import pandas as pd, datetime; df=pd.read_parquet('$PARQUET', columns=['net_mf_amount']); lat=df.index.get_level_values('trade_date').max(); lag=(pd.Timestamp.now().normalize()-lat).days; print('%s|%s|%s' % (lat.date(), lag, len(df)))" 2>$null | Select-Object -Last 1
    $report = "$r"
} catch { Log "  !! 读取 $PARQUET 失败: $_" }

$latestDate = "?"
$lagDays = -1
$rows = -1
if ($report -match '^([\d-]+)\|(\d+)\|(\d+)$') {
    $latestDate = $Matches[1]; $lagDays = [int]$Matches[2]; $rows = [long]$Matches[3]
}
if ($refreshExit -eq 0 -and $latestDate -ne "?") {
    Log "【结果】刷新成功（退出码0）；最新日期=$latestDate；相对今日滞后=$lagDays 天；总行数=$rows"
} else {
    Log "【结果】刷新可能未完成：退出码=$refreshExit 最新日期=$latestDate 滞后=$lagDays 天 总行数=$rows"
}

# ---- 步骤4：滞后 >2 天告警 ----
if ($latestDate -ne "?" -and $lagDays -gt 2) {
    Log "!! 告警：moneyflow 最新日期 $latestDate 滞后 $lagDays 天（>2，非 T-1），刷新可能异常，请排查 Tushare 额度/网络/脚本"
}
Log "[refresh] 完成"
