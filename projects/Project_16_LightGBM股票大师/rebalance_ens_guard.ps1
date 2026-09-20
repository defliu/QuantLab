# coding: utf-8
# ============================================================
# Project_16 融合(ENS) 每日换仓兜底（T-20260915-001 延续，2026-09-15 建）
# 背景：ENS 换仓仅 TRAE 任务 051bc2d9（09:52）+ DE 只读核验（10:12，不下单不补跑）。
#       TRAE 会话中断（V1.3 09-15 同类事故）时无 Windows 兜底能补跑。本脚本补上：
#         - 幂等：当日已换仓（桥 cmd/orders_<date>.json 已写 或 rebalance_g2_ens_<date>.json written=true）→ 跳过
#         - 兜底：未换仓 → dry-run 校验（桥存活/候选新鲜/宁缺毋滥）→ --live 写桥
#         - exit code 检查：失败不写锁 + 飞书告警（人工可重跑）
# 触发时点 10:08：TRAE 09:52 任务 ~10:02 完成，10:08 可区分已执行/未执行；避开 10:05 V13 guard、10:10 G2 DE、10:12 DE 核验。
# ENS 候选由 Quant_P16_G2Candidates_Night（20:05，含 --ens-live 副本块）预生成 → 补跑无需实时取数。
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File rebalance_ens_guard.ps1             # 今日
#   powershell ... -File rebalance_ens_guard.ps1 -Date 20260915 -CheckOnly                  # 只检测不执行
# 日志: data/schedules/rebalance_ens_guard_<时间戳>.log
# 锁  : data/schedules/rebalance_ens_guard_<date>.lock
# ============================================================
param(
    [string]$Date = "",
    [switch]$CheckOnly
)
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$dataDir = Join-Path $proj "data"
$logDir = Join-Path $dataDir "schedules"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$log = Join-Path $logDir "rebalance_ens_guard_$stamp.log"
function Log($m) { $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

$miniqmtPy = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
if (Test-Path $miniqmtPy) { $py = $miniqmtPy }
elseif (Test-Path $FALLBACK_PY) { $py = $FALLBACK_PY }
if (-not $py) { Log "!! 未找到 Python（miniqmt / TRAE SOLO），ENS 兜底任务无法运行"; exit 1 }
Log "使用 Python: $py"

if (-not $Date) { $Date = Get-Date -Format "yyyyMMdd" }
$bridgeCmd = "D:\QMT_POOL\p16_ensemble_bridge\cmd"
Push-Location $proj
try {
    & $py "is_trade_day.py" --json *> $null
    if ($LASTEXITCODE -ne 0) { Log "非 A 股交易日，跳过 [$Date]"; exit 0 }
    Log "确认 A 股交易日，检查 ENS 09:52 换仓是否已执行 [$Date]"

    $already = $false
    # 标记1：桥 cmd/orders_<date>.json 已写（TRAE 已 live 写桥，有实际买卖）
    $ordersFile = Join-Path $bridgeCmd "orders_$Date.json"
    if (Test-Path $ordersFile) { $already = $true; Log "  桥 orders_$Date.json 已存在 → ENS 已换仓，跳过" }
    # 标记2：正式 plan json 存在（非 .dryrun）→ 当日 rebalance_ens 已执行（含 NOOP 无买卖也算执行，T-20260915-001 修正）
    if (-not $already) {
        $planJson = Join-Path $dataDir "rebalance_g2_ens\rebalance_g2_ens_$Date.json"
        if (Test-Path $planJson) {
            try {
                $plan = Get-Content $planJson -Raw -Encoding UTF8 | ConvertFrom-Json
                # 正式 json 由 --live 执行写出（NOOP 时 orders 空但 json 存在，written 可能缺失）
                $already = $true; Log "  rebalance_g2_ens_$Date.json 已存在 → ENS 已执行（含 NOOP），跳过"
                if ($plan.orders -and $plan.orders.Count -gt 0) { Log "    当日计划含 $($plan.orders.Count) 条指令" }
            } catch { Log "  !! 读取 $planJson 失败: $_" }
        }
    }
    # 标记3：兜底锁
    $lock = Join-Path $logDir "rebalance_ens_guard_$Date.lock"
    if (-not $already -and (Test-Path $lock)) { $already = $true; Log "  兜底锁已存在（$lock）→ $Date 已兜底过，跳过" }
    if ($already) {
        if ($CheckOnly) { Log "[CheckOnly] 检测完成：ENS 已执行，无需兜底"; exit 0 }
        exit 0
    }
    Log "  $Date ENS 09:52 换仓未执行，启动兜底"

    # 候选就绪检查（Quant_P16_G2Candidates_Night 20:05 预生成，含 g2_ens 副本；宁缺毋滥由 rebalance_ens 内置）
    $ensSel = Join-Path $dataDir "selections\g2_ens"
    $candFiles = Get-ChildItem $ensSel -Filter "*_g2_top10.csv" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if (-not $candFiles) {
        $msg = "ENS $Date 09:52 换仓未执行，且候选目录 $ensSel 无任何 _g2_top10.csv（候选管道 20:05 未产出）。兜底无法补跑，请人工核查 Quant_P16_G2Candidates_Night。"
        Log "!! $msg"
        if (-not $CheckOnly) {
            & $py "push_alert_card.py" --title "ENS 09:52 换仓漏触发(候选缺失)" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
            New-Item -ItemType File -Path $lock -Force | Out-Null
        }
        exit 0
    }
    $latestCand = ($candFiles | Select-Object -First 1).Name
    Log "  最新候选: $latestCand"

    if ($CheckOnly) { Log "[CheckOnly] 检测完成：ENS 需兜底，将 dry-run 校验后 rebalance_ens.py --date $Date --live（本次仅检测，不执行）"; exit 0 }

    # 第一步：dry-run 校验（桥存活/候选新鲜/门控 均内置，非 0 即中止）
    Log "  >> python rebalance_ens.py --date $Date (dry-run 校验)"
    & $py "rebalance_ens.py" --date $Date 2>&1 | ForEach-Object { Log $_ }
    $rc = $LASTEXITCODE
    Log "  >> dry-run exit=$rc"
    if ($rc -ne 0) {
        $msg = "ENS $Date guard 补跑 dry-run 校验失败（exit=$rc），未写桥未写锁。请人工核查（桥存活/候选新鲜/门控）。"
        Log "!! $msg"
        & $py "push_alert_card.py" --title "ENS guard 补跑 dry-run 失败" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
        exit 0
    }

    # 第二步：--live 写桥
    Log "  >> python rebalance_ens.py --date $Date --live"
    & $py "rebalance_ens.py" --date $Date --live 2>&1 | ForEach-Object { Log $_ }
    $rc2 = $LASTEXITCODE
    Log "  >> live exit=$rc2"
    if ($rc2 -ne 0) {
        $msg = "ENS $Date guard 补跑 rebalance_ens --live 失败（exit=$rc2），未写锁供人工重试。请核对 orders_$Date.json 与桥状态。"
        Log "!! $msg"
        & $py "push_alert_card.py" --title "ENS guard 补跑 rebalance 失败" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
    } else {
        New-Item -ItemType File -Path $lock -Force | Out-Null
        Log "  ENS 补跑成功，已写桥"
    }
    Log "[guard] 完成"
} catch {
    Log "[guard] 异常: $_"
}
Pop-Location
