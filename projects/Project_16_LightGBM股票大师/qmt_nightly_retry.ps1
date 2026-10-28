# QMT 夜间闭环「登录恢复后自动补跑」（2026-10-29 立，T-20261029-002）
#
# 为什么需要它：夜间闭环的失败根因几乎全在「客户端没登上券商柜台」——策略引擎
# 报 `Proxy client not logged`，产物根本没被加载，于是 qmt_autodeploy 轮询 BUILD_TAG
# 300s 超时，报告只留一句「当日无任何输出」，看不出是券商侧还是本机。
# 2026-10-28 夜实测：登录节点 180.169.107.19:56001 先「积极拒绝」（TCP 层断），
# 后转为「TCP 通 + 柜台报文 120s 不应答 ErrorID 200003 超时」，主号/沙箱两台同刻同签名
# ⇒ 券商侧，人工点登录也一样超时，只能等它恢复。
# 本脚本把「等恢复 + 恢复即补跑」自动化：每 15 分钟探一次登录态，只有真登录了才跑闭环，
# 跑成 PASS 当天不再重复，避免半夜反复重启客户端。
#
# 边界（不放宽）：
#   1) 只处理沙箱 D:\QMT交易端模拟（模拟盘，可任意重启）；主号只由闭环只读扫描；
#      交易版 D:\国金证券QMT交易端 不出现在任何参数里，探针内置硬拒绝；
#   2) 08:50-15:10 一律不动作（防重启撞上开盘/盘中）；
#   3) 当日最多补跑 6 次，防「登录正常但部署另有问题」时无限重启客户端；
#   4) 停机开关：存在 data\schedules\qmt_retry_off 文件时直接退出；
#   5) 登录未恢复时不构建、不落盘、不重启（零副作用）。
#
# 调度：Quant_P16_QMT_NightlyRetry（Interactive/Highest + 隐藏窗口，21:30 起每 15 分钟，
#       持续 11 小时覆盖整夜）。之所以用 Interactive 而非 AGENTS 默认的 S4U：它要重启
#       GUI 客户端，S4U 会把窗口起在 session 0，人看不到登录框，与 qmt_autostart /
#       QMT_WatchLogin 两个任务的先例保持一致。
param(
    [switch]$Force   # 跳过时段闸与补跑次数上限（人工排障用）
)
$ErrorActionPreference = "Continue"
$dir      = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sandbox  = "D:\QMT交易端模拟"
$sched    = Join-Path $dir "data\schedules"
$log      = Join-Path $sched "qmt_nightly_retry.log"
$offFile  = Join-Path $sched "qmt_retry_off"
$probe    = "D:\QuantLab\scripts\qmt_login_probe.py"
$nightly  = Join-Path $dir "qmt_nightly_deploy.ps1"
$today    = Get-Date -Format "yyyyMMdd"
$mark     = Join-Path $sched ("qmt_nightly_pass_{0}.mark" -f $today)
$cntFile  = Join-Path $sched ("qmt_nightly_retry_attempts_{0}.txt" -f $today)
$py = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
if (!(Test-Path $py)) { $py = "D:\Python311\python.exe" }

function Write-Log([string]$msg) {
    # 一个事件一行：任何调用方传进多行文本（探针原始输出 / 异常 ToString）都会被
    # Add-Content 拆成多行，时间戳只在第一行 ⇒ 事后 grep 会把续行当成孤立记录漏读。
    $msg = ($msg -replace "\r?\n", " | ")
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try {
        if (!(Test-Path $sched)) { New-Item -ItemType Directory -Force -Path $sched | Out-Null }
        Add-Content -Path $log -Value $line -Encoding UTF8
    } catch { }
}

# 飞书卡片：只报「需要人知道的两件事」——补跑成功 / 次数用尽仍失败。
# WAIT（登录未恢复）不推，否则每 15 分钟刷屏。推送失败只记日志，绝不阻断主流程。
function Push-Card([string]$title, [string]$body, [string]$level) {
    $cardBody = Join-Path $sched "qmt_retry_card.txt"
    try {
        $pusher = Join-Path $dir "push_alert_card.py"
        if (!(Test-Path $pusher)) { Write-Log "SKIP 推送器不存在，不推卡"; return }
        [IO.File]::WriteAllText($cardBody, $body, (New-Object Text.UTF8Encoding($false)))
        $out = (& $py $pusher --title $title --body-file $cardBody --level $level 2>&1 | Out-String).Trim()
        Write-Log ("PUSH[{0}] {1}" -f $level, ($out -replace "\s+", " "))
    } catch {
        Write-Log ("PUSH-FAIL {0}" -f $_.Exception.Message)
    }
}

if (Test-Path $offFile) { Write-Log ("SKIP 停机开关存在 {0}" -f $offFile); exit 0 }
if (Test-Path $mark) { exit 0 }   # 今天已经 PASS，安静退出

# ---- 0) 若最近一份闭环报告已是 OK（比如 21:10 定时任务自己跑成了），直接补记标记收工，
#         避免本任务在 21:30 再重启一次客户端做无谓的重复部署。
$reportJson = Join-Path $dir "results\qmt_nightly\latest.json"
if (Test-Path $reportJson) {
    try {
        $ageH = ((Get-Date) - (Get-Item $reportJson).LastWriteTime).TotalHours
        $d0 = Get-Content $reportJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $d0Deploy = if ($d0.deploy) { [string]$d0.deploy.result } else { "" }
        $d0Tag    = if ($d0.deploy) { [string]$d0.deploy.tag } else { "" }
        # dry_run 字段是 2026-10-29 新加的：演练报告（-Rehearsal / --no-restart）从未真正
        # 「落盘 → 重启 → 回读 BUILD_TAG」，deploy.result 也可能是 PASS，绝不能当成功复用。
        # 旧报告没有该字段 → $null → [bool]$null = False，判定不受影响。
        $d0Dry    = if ($d0.deploy) { [bool]$d0.deploy.dry_run } else { $false }
        # 同样只认 deploy=PASS（=回读到 BUILD_TAG），verdict=ATTENTION 也算这一轮已经成了，
        # 再补跑一次只会白重启客户端。
        if ($d0Dry) {
            Write-Log ("SKIP-REPORT latest.json 是演练报告（dry_run=true，BUILD_TAG={0}），不作 PASS 复用，继续走补跑判定" -f $d0Tag)
        }
        if (($ageH -lt 4) -and ($d0Deploy -eq "PASS") -and (-not $d0Dry)) {
            Set-Content -Path $mark -Value ("{0} 复用已有成功报告 BUILD_TAG={1} verdict={2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $d0Tag, [string]$d0.verdict) -Encoding UTF8
            Write-Log ("SKIP 最近 {0:N1}h 内已有 deploy=PASS 报告（BUILD_TAG={1} verdict={2}），不重复补跑" -f $ageH, $d0Tag, [string]$d0.verdict)
            Push-Card "[QMT] 夜间闭环已 PASS（复用已有报告）" ("沙箱夜间闭环已有一轮 deploy=PASS、BUILD_TAG 已核对，无需补跑。`nBUILD_TAG={0}`n总判定={1}`n报告时间距今 {2:N1} 小时（由 21:10 定时任务或人工跑出）。`n今日补跑任务已静默收工。" -f $d0Tag, [string]$d0.verdict, $ageH) "info"
            exit 0
        }
    } catch { Write-Log "WARN latest.json 预读失败（继续走补跑判定）" }
}

# ---- 1) 时段闸 ----
$hhmm = Get-Date -Format "HHmm"
if ($hhmm -ge "0850" -and $hhmm -le "1510") {
    if (-not $Force) { Write-Log ("SKIP 时段闸（{0}）不重启客户端" -f $hhmm); exit 0 }
    Write-Log ("FORCE 越过时段闸（{0}）" -f $hhmm)
}

# ---- 2) 补跑次数上限 ----
$attempts = 0
if (Test-Path $cntFile) { [void]([int]::TryParse((Get-Content $cntFile -Raw).Trim(), [ref]$attempts)) }
if ($attempts -ge 6 -and -not $Force) {
    Write-Log ("SKIP 当日已补跑 {0} 次仍未 PASS，停手待人工（读 {1} 尾部 + results\qmt_nightly\latest.json）" -f $attempts, $log)
    Push-Card "[QMT] 夜间闭环补跑 6 次仍未 PASS" ("当日已补跑 {0} 次仍没拿到 deploy=PASS 的报告，任务已停手（不再重启沙箱）。`n请先读 data\schedules\qmt_nightly_retry.log 尾部 + results\qmt_nightly\latest.json 的 deploy.result：`n  - 若 rc=4 且探针判已登录 ⇒ 部署侧新问题（模型未注册 / 产物非法）；`n  - 若一直 WAIT 未登录 ⇒ 券商柜台仍未恢复，属外部问题。`n人工排障可加 -Force 越过时段闸与次数上限；临时停用请建 data\schedules\qmt_retry_off。" -f $attempts) "error"
    exit 0
}

# ---- 3) 登录探针（只读）----
# 说明：不让 PowerShell 解 JSON——PS 5.1 按 [Console]::OutputEncoding 解码子进程输出，
# UTF-8 的中文按 GBK 解出乱码，乱码里可能混进引号/反斜杠，ConvertFrom-Json 直接崩
# （00:32 实测如此）。改用探针的 --verdict 单行键值，只正则 LOGGED=0/1。
$env:PYTHONIOENCODING = "utf-8"
$oldEnc = $null
try { $oldEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$probeOut = (& $py $probe --verdict --client $sandbox 2>&1 | Out-String).Trim()
$logged = ($probeOut -match "LOGGED=1")
$mState = [regex]::Match($probeOut, "STATE=(.+)$")
$state = if ($mState.Success) { $mState.Groups[1].Value } else { $probeOut }
if (-not $mState.Success) { Write-Log ("WARN 探针输出异常：" + ($probeOut -replace "`r?`n", " | ")) }
try { if ($oldEnc) { [Console]::OutputEncoding = $oldEnc } } catch { }

if (-not $logged) {
    Write-Log ("WAIT 沙箱未登录：{0}" -f $state)
    exit 0
}
Write-Log ("UP 沙箱已登录（{0}），补跑夜间闭环（第 {1} 次）" -f $state, ($attempts + 1))

# ---- 4) 补跑闭环 ----
$runStart = Get-Date
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nightly | Out-Null
$rc = $LASTEXITCODE
$attempts += 1
Set-Content -Path $cntFile -Value $attempts -Encoding UTF8

$verdict = "无报告"
$tag = ""
$deployResult = ""
$dryRun = $false
$fresh = $false
$reportJson = Join-Path $dir "results\qmt_nightly\latest.json"
if (Test-Path $reportJson) {
    # 「本轮报告」硬条件：latest.json 必须是在调起闭环之后才被写出来的。
    # 否则闭环中途崩了（没落报告）时，这里会拿上一轮的旧 latest.json 误判成本轮结果。
    $fresh = (Get-Item $reportJson).LastWriteTime -gt $runStart.AddSeconds(-10)
    try {
        $d = Get-Content $reportJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $verdict = [string]$d.verdict
        if ($d.deploy) {
            $tag = [string]$d.deploy.tag
            $deployResult = [string]$d.deploy.result
            $dryRun = [bool]$d.deploy.dry_run
        }
    } catch { Write-Log "WARN latest.json 解析失败" }
}

# PASS 判据取 deploy.result=PASS（即「本次 deploy_start 之后回读到期望 BUILD_TAG」），
# 不取 verdict=OK。原因：qmt_nightly 的 verdict 是三值 OK / ATTENTION / FAIL，
# ATTENTION = 部署与 TAG 都核对通过、只是日志体检里出现了非致命异常签名（rc 仍为 0）。
# 若以 verdict=OK 为准，一次「部署成功但体检有关注项」会被误判成 FAIL ⇒ 白重启 6 次客户端，
# 还会推一张假 FAIL 卡。部署到底成没成，只认 BUILD_TAG。
# 另两条硬条件：报告必须是本轮写出的（$fresh）、且不是演练（$dryRun=false）。
if (($deployResult -eq "PASS") -and ($tag -ne "") -and ($fresh) -and (-not $dryRun)) {
    Set-Content -Path $mark -Value ("{0} BUILD_TAG={1} verdict={2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $tag, $verdict) -Encoding UTF8
    Write-Log ("PASS 夜间闭环补跑成功 deploy=PASS BUILD_TAG={0} 总判定={1}，当日不再重试" -f $tag, $verdict)
    $extra = if ($verdict -eq "OK") { "" } else { "`n[注意] 总判定=$verdict（部署与 BUILD_TAG 已核对通过，但日志体检有关注项）`n请读 results\qmt_nightly\latest.md 的「客户端扫描」一节。" }
    Push-Card "[QMT] 夜间闭环补跑 PASS" ("沙箱登录已恢复，夜间闭环补跑成功。`nBUILD_TAG={0}`ndeploy={1}（退出码 {2}）`n第 {3} 次补跑`n今日不再重试（已落 {4}）。{5}" -f $tag, $deployResult, $rc, $attempts, (Split-Path $mark -Leaf), $extra) "info"
    exit 0
}
Write-Log ("FAIL 补跑未过 deploy={0} verdict={1} 期望 BUILD_TAG={2}（exit={3} 报告本轮新写={4} 演练={5}）" -f $deployResult, $verdict, $tag, $rc, $fresh, $dryRun)
Push-Card "[QMT] 夜间闭环补跑 FAIL" ("沙箱已登录但闭环仍 FAIL。`ndeploy={0} verdict={1}`n期望 BUILD_TAG={2}，deploy 退出码={3}`n第 {4} 次补跑（上限 6 次）。`n注意：已登录仍出不了 TAG ⇒ 不再是券商登录问题，需查产物/模型注册（读 data\schedules\qmt_nightly.log 尾部 + results\qmt_nightly\latest.md）。" -f $deployResult, $verdict, $tag, $rc, $attempts) "error"
exit 1
