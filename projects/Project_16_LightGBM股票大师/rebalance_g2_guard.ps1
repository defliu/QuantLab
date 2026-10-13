# coding: utf-8
# ============================================================
# Project_16 G2 每日换仓兜底（T-20260915-001 延续，2026-09-15 建）
# 背景：G2 换仓仅 TRAE 任务 30344e79（09:50）+ DE 只读核验（10:10，不下单不补跑）。
#       TRAE 会话中断（V1.3 09-15 同类事故）时无 Windows 兜底能补跑。本脚本补上：
#         - 幂等：当日已换仓（桥 cmd/orders_<date>.json 已写 或 rebalance_g2_<date>.json written=true）→ 跳过
#         - 兜底：未换仓 → dry-run 校验（桥存活/候选新鲜/宁缺毋滥）→ --live 写桥
#         - exit code 检查：失败不写锁 + 飞书告警（人工可重跑）
#         - in-flight 扫描（T-20261009-001，对齐 V1.x T-20261008-009）：发现 rebalance_g2.py 仍在跑 →
#           等待（≤20 分钟）后再判定；超时疑似挂死 → 放弃兜底 + 告警（绝不并发写桥）
# 触发时点 10:07：TRAE 09:50 任务 ~10:00 完成，10:07 可区分已执行/未执行；避开 10:05 V13 guard、10:10 DE 核验。
# G2 候选由 Quant_P16_G2Candidates_Night（20:05）预生成 top10 → 补跑无需实时取数（比 V1.3 guard_backfill 更简单）。
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File rebalance_g2_guard.ps1             # 今日
#   powershell ... -File rebalance_g2_guard.ps1 -Date 20260915 -CheckOnly                  # 只检测不执行
# 日志: data/schedules/rebalance_g2_guard_<时间戳>.log
# 锁  : data/schedules/rebalance_g2_guard_<date>.lock
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
$log = Join-Path $logDir "rebalance_g2_guard_$stamp.log"
function Log($m) { $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

$miniqmtPy = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$py = $null
if (Test-Path $miniqmtPy) { $py = $miniqmtPy }
elseif (Test-Path $FALLBACK_PY) { $py = $FALLBACK_PY }
if (-not $py) { Log "!! 未找到 Python（miniqmt / TRAE SOLO），G2 兜底任务无法运行"; exit 1 }
Log "使用 Python: $py"

if (-not $Date) { $Date = Get-Date -Format "yyyyMMdd" }
$bridgeCmd = "D:\QMT_POOL\g2_bridge\cmd"
Push-Location $proj
try {
    & $py "is_trade_day.py" --json *> $null
    if ($LASTEXITCODE -ne 0) { Log "非 A 股交易日，跳过 [$Date]"; exit 0 }
    Log "确认 A 股交易日，检查 G2 09:50 换仓是否已执行 [$Date]"

    # ---- in-flight 进程扫描（T-20261009-001，对齐 V1.x T-20261008-009）：标记检查看不见「正在跑」的换仓 ----
    # 2026-10-08 V1.x 事故形态：TRAE 09:50 任务迟到时 orders/plan 标记尚未落盘，guard 按「无标记」
    # 误判未执行 → 与 in-flight 的 TRAE run 并发写桥（重复指令）。
    # json/orders 标记都要等 run 结束才存在，唯一可靠的「正在执行」信号是进程本身。
    # 策略：发现 in-flight rebalance_g2.py 进程 → 轮询等其结束（最多 20 分钟）再复查标记；
    #       超时未结束（疑似挂死）→ 放弃自动兜底 + 飞书告警人工核查（绝不并发写桥）。
    # 注：DE 10:10 只读核验调 reconcile_g2.py，不匹配本模式；其 rebalance_g2.py dry-run（de_rebalance_guard_daily）
    #     会匹配 → 极端交错下 guard 多等其 1-2 分钟，安全无害（宁等勿并发）。
    $scanDeadline = (Get-Date).AddMinutes(20)
    while ((Get-Date) -lt $scanDeadline) {
        $inflight = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'rebalance_g2\.py' }
        if (-not $inflight) { break }
        Log "  检测到 in-flight G2 换仓进程（pid=$($inflight.ProcessId -join ',')），等待其结束后再判定是否兜底"
        Start-Sleep -Seconds 60
    }
    $stillInflight = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'rebalance_g2\.py' }
    if ($stillInflight) {
        $msg = "G2 $Date 换仓进程(pid=$($stillInflight.ProcessId -join ',')) 超过 20 分钟未结束（疑似挂死），guard 放弃自动兜底。请人工核查该进程与当日 orders，勿并发重跑。"
        Log "!! $msg"
        & $py "push_alert_card.py" --title "G2 guard: in-flight 换仓疑似挂死" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
        Pop-Location
        exit 1
    }

    $already = $false
    # 标记1：桥 cmd/orders_<date>.json 已写（TRAE 已 live 写桥，有实际买卖）
    $ordersFile = Join-Path $bridgeCmd "orders_$Date.json"
    if (Test-Path $ordersFile) { $already = $true; Log "  桥 orders_$Date.json 已存在 → G2 已换仓，跳过" }
    # 标记2：正式 plan json 存在（非 .dryrun）→ 当日 rebalance_g2 已执行（含 NOOP 无买卖也算执行，T-20260915-001 修正）
    if (-not $already) {
        $planJson = Join-Path $dataDir "rebalance_g2\rebalance_g2_$Date.json"
        if (Test-Path $planJson) {
            try {
                $plan = Get-Content $planJson -Raw -Encoding UTF8 | ConvertFrom-Json
                # 正式 json 由 --live 执行写出（NOOP 时 orders 空但 json 存在，written 可能缺失）
                $already = $true; Log "  rebalance_g2_$Date.json 已存在 → G2 已执行（含 NOOP），跳过"
                if ($plan.orders -and $plan.orders.Count -gt 0) { Log "    当日计划含 $($plan.orders.Count) 条指令" }
            } catch { Log "  !! 读取 $planJson 失败: $_" }
        }
    }
    # 标记3：兜底锁
    $lock = Join-Path $logDir "rebalance_g2_guard_$Date.lock"
    if (-not $already -and (Test-Path $lock)) { $already = $true; Log "  兜底锁已存在（$lock）→ $Date 已兜底过，跳过" }
    if ($already) {
        if ($CheckOnly) { Log "[CheckOnly] 检测完成：G2 已执行，无需兜底"; exit 0 }
        exit 0
    }
    Log "  $Date G2 09:50 换仓未执行，启动兜底"

    # 候选就绪检查（Quant_P16_G2Candidates_Night 20:05 预生成；宁缺毋滥由 rebalance_g2 内置）
    $g2Sel = Join-Path $dataDir "selections\g2"
    $candFiles = Get-ChildItem $g2Sel -Filter "*_g2_top10.csv" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if (-not $candFiles) {
        $msg = "G2 $Date 09:50 换仓未执行，且候选目录 $g2Sel 无任何 _g2_top10.csv（候选管道 20:05 未产出）。兜底无法补跑，请人工核查 Quant_P16_G2Candidates_Night。"
        Log "!! $msg"
        if (-not $CheckOnly) {
            & $py "push_alert_card.py" --title "G2 09:50 换仓漏触发(候选缺失)" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
            # 失败不写锁（保留人工重试入口），exit 1 可见
            Pop-Location
            exit 1
        }
        Pop-Location
        exit 0
    }
    $latestCand = ($candFiles | Select-Object -First 1).Name
    Log "  最新候选: $latestCand"

    if ($CheckOnly) { Log "[CheckOnly] 检测完成：G2 需兜底，将 dry-run 校验后 rebalance_g2.py --date $Date --live（本次仅检测，不执行）"; exit 0 }

    # 第一步：dry-run 校验（桥存活/候选新鲜/门控 均内置，非 0 即中止）
    Log "  >> python rebalance_g2.py --date $Date (dry-run 校验)"
    & $py "rebalance_g2.py" --date $Date 2>&1 | ForEach-Object { Log $_ }
    $rc = $LASTEXITCODE
    Log "  >> dry-run exit=$rc"
    if ($rc -ne 0) {
        $msg = "G2 $Date guard 补跑 dry-run 校验失败（exit=$rc），未写桥未写锁。请人工核查（桥存活/候选新鲜/门控）。"
        Log "!! $msg"
        & $py "push_alert_card.py" --title "G2 guard 补跑 dry-run 失败" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
        Pop-Location
        exit 1
    }

    # 第二步：--live 写桥
    Log "  >> python rebalance_g2.py --date $Date --live"
    & $py "rebalance_g2.py" --date $Date --live 2>&1 | ForEach-Object { Log $_ }
    $rc2 = $LASTEXITCODE
    Log "  >> live exit=$rc2"
    if ($rc2 -ne 0) {
        $msg = "G2 $Date guard 补跑 rebalance_g2 --live 失败（exit=$rc2），未写锁供人工重试。请核对 orders_$Date.json 与桥状态。"
        Log "!! $msg"
        & $py "push_alert_card.py" --title "G2 guard 补跑 rebalance 失败" --body $msg --level error 2>&1 | ForEach-Object { Log $_ }
        Pop-Location
        exit 1
    } else {
        New-Item -ItemType File -Path $lock -Force | Out-Null
        Log "  G2 补跑成功，已写桥"
    }
    Log "[guard] 完成"
    Pop-Location
    exit 0
} catch {
    Log "[guard] 异常: $_"
    Pop-Location
    exit 1
}
Pop-Location
exit 1
