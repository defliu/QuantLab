# coding: utf-8
# ============================================================
# Project_16 V1.3 每日换仓兜底（T-20260903-002，2026-09-03 建）
# 背景：V1.3 09:45「开盘实时复核」(TRAE 计划任务 15868a74) 偶发漏触发（09-03 已实锤），
#       且该任务为唯一触发源、无 Windows 兜底。本脚本作为 Windows 计划任务（10:05）兜底：
#         - 幂等：TRAE 任务已执行（rebalance_<date>.json executed_live 或该日换仓成交）→ 跳过
#         - 兜底：当日 selection_full 已生成但未 live（TRAE 复核后崩）→ 补跑 rebalance_daily --live
#         - 告警：当日 selection_full 缺失（TRAE 整链漏触发）→ 无法自动补（需实时 F2/F3/F5 采集），飞书告警
# 触发时点选 10:05：TRAE 09:45 任务 ~09:46 触发、约跑 12 分钟至 ~09:58，10:05 已能区分"已执行/未执行"，
#   且避免与 TRAE 任务并发导致重复下单。
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File rebalance_daily_guard.ps1             # 今日
#   powershell ... -File rebalance_daily_guard.ps1 -Date 20260903                             # 指定日
#   powershell ... -File rebalance_daily_guard.ps1 -CheckOnly                                 # 只检测不执行（测试）
# 日志: data/schedules/rebalance_daily_guard_<时间戳>.log
# 锁  : data/schedules/rebalance_daily_guard_<date>.lock（当天已兜底过则不再重复）
# ============================================================
param(
    [string]$Date = "",
    [switch]$CheckOnly
)
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataDir = Join-Path $proj "data"
$logDir = Join-Path $dataDir "schedules"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$log = Join-Path $logDir "rebalance_daily_guard_$stamp.log"
function Log($m) { $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

# ---- Python（rebalance_daily 需 xtquant，用 miniqmt venv 优先；回退 TRAE SOLO python）----
$miniqmtPy = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
if (Test-Path $miniqmtPy) { $py = $miniqmtPy }
elseif (Test-Path $FALLBACK_PY) { $py = $FALLBACK_PY }
if (-not $py) { Log "!! 未找到 Python（miniqmt / TRAE SOLO），兜底任务无法运行"; exit 1 }
Log "使用 Python: $py"

if (-not $Date) { $Date = Get-Date -Format "yyyyMMdd" }
$datePrefix = $Date.Substring(0,4) + "-" + $Date.Substring(4,2) + "-" + $Date.Substring(6,2)
Push-Location $proj
try {
    & $py "is_trade_day.py" --json *> $null
    if ($LASTEXITCODE -ne 0) { Log "非 A 股交易日，跳过 [$Date]"; exit 0 }
    Log "确认 A 股交易日，检查 09:45 换仓是否已执行 [$Date]"

    $already = $false
    # 标记1：rebalance_<date>.json executed_live=true（UTF-8 读，防中文乱码）
    $rebalanceJson = Join-Path $dataDir "rebalance_$Date.json"
    if (Test-Path $rebalanceJson) {
        try {
            $plan = Get-Content $rebalanceJson -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($plan.executed_live -eq $true) { $already = $true; Log "  标记已存在（rebalance_$Date.json executed_live=true）→ TRAE 09:45 已执行，跳过" }
        } catch { Log "  !! 读取 $rebalanceJson 失败: $_" }
    }
    # 标记2：$Date 当日成交记录含换仓委托（BUY 带分数 / SELL MATURE|TRIM_OVR|POOL_ADJ）
    # T-20260904-001（2026-09-05）后 SELL reason 由 PK_OUT 改为 MATURE（到期制）；
    # 保留 PK_OUT/POOL_ADJ 兼容历史成交记录，避免补跑判断误判历史。
    if (-not $already) {
        $tradeLog = Join-Path $dataDir "qmt_trade_log.csv"
        if (Test-Path $tradeLog) {
            $rows = Import-Csv $tradeLog | Where-Object { $_.time -like "$datePrefix*" -and $_.account_id -eq "67014907" }
            $reb = $rows | Where-Object { ($_.side -eq "BUY" -and $_.score -match '^\d') -or ($_.side -eq "SELL" -and $_.score -in @("MATURE","TRIM_OVR","POOL_ADJ","PK_OUT")) }
            if ($reb) { $already = $true; Log "  $Date 成交记录已含 $($reb.Count) 条换仓委托 → TRAE 09:45 已执行，跳过" }
        }
    }
    # 标记3：兜底锁（$Date 当天本脚本已跑过）
    $lock = Join-Path $logDir "rebalance_daily_guard_$Date.lock"
    if (-not $already -and (Test-Path $lock)) { $already = $true; Log "  兜底锁已存在（$lock）→ $Date 已兜底过，跳过" }
    if ($already) {
        if ($CheckOnly) { Log "[CheckOnly] 检测完成：已执行，无需兜底"; exit 0 }
        exit 0
    }
    Log "  $Date 09:45 换仓未执行，启动兜底"

    # 当日完整版清单是否就绪（兜底能否自动补跑的关键）
    $selFull = Join-Path $dataDir "selections\${Date}_selection_full.csv"
    if (-not (Test-Path $selFull)) {
        $msg = "V1.3 $Date 09:45 换仓未执行，且当日清单 ${Date}_selection_full.csv 缺失（TRAE 09:45 整链漏触发）。兜底无法自动补跑（需实时 F2/F3/F5 采集），请按 9:45 设定补跑：review_full + rebalance_daily --live。"
        Log "!! $msg"
        if (-not $CheckOnly) {
            & $py "push_alert_card.py" --title "V1.3 09:45 换仓漏触发" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
        }
        if (-not $CheckOnly) { New-Item -ItemType File -Path $lock -Force | Out-Null }
        exit 0
    }

    # 大盘风控定 T（沪深300 腾讯实时；同 9:45 任务规则）
    $tier = 2
    try {
        $hs = & $py -c "import urllib.request; d=urllib.request.urlopen(urllib.request.Request('https://qt.gtimg.cn/q=sh000300', headers={'User-Agent':'Mozilla/5.0'}), timeout=8).read().decode('gbk','ignore'); p=d.split('~'); n=float(p[3]); c=float(p[4]); print(round((n/c-1)*100,2))" 2>$null | Select-Object -Last 1
        $hsPct = [double]$hs
        if ($hsPct -le -1.5) { $tier = 0 }
        elseif ($hsPct -lt -1.0) { $tier = 1 }
        Log "  沪深300 $hsPct% → T=$tier"
    } catch { Log "  !! 沪深300 取数失败，默认 T=2" }
    if ($tier -eq 0) { Log "  T=0 空仓日，不触发兜底（--top 0 由业务处理）"; if (-not $CheckOnly) { New-Item -ItemType File -Path $lock -Force | Out-Null }; exit 0 }

    if ($CheckOnly) { Log "[CheckOnly] 检测完成：需兜底，将执行 rebalance_daily --date $Date --top $tier --live（本次仅检测，不执行）"; exit 0 }

    Log "  >> python rebalance_daily.py --date $Date --top $tier --live"
    & $py "rebalance_daily.py" --date $Date --top $tier --live 2>&1 | ForEach-Object { Log $_ }
    Log "  >> exit=$LASTEXITCODE"
    New-Item -ItemType File -Path $lock -Force | Out-Null
    Log "[guard] 完成"
} catch {
    Log "[guard] 异常: $_"
}
Pop-Location
