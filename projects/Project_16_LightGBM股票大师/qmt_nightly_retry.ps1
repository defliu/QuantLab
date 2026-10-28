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
#   2) 时段闸只对**非沙箱**目标生效（2026-10-29 起沙箱免闸）：沙箱策略永远 passive 空转、
#      无真钱、无盘中证据要保，交易日白天登录一恢复就能补出 PASS，不必等到 15:10/21:10。
#      若有人把 $sandbox 改成主号，闸自动回来（见 §1）；
#   3) 当日最多补跑 6 次（夜间窗口计数），防「登录正常但部署另有问题」时无限重启客户端；
#   4) 停机开关：存在 data\schedules\qmt_retry_off 文件时直接退出；
#   5) 登录未恢复时不构建、不落盘、不重启（零副作用）；
#   6) 日间槽（08:50-20:00，2026-10-29 加、同日由 15:15 起扩到全天）另有一组专属护栏，
#      都拦在探针之前：24h 内已有真实（非演练）PASS、日间额度 2 次/日；
#      且用**独立**计数不挤占夜间的 6 次上限。
#
# 调度：Quant_P16_QMT_NightlyRetry（Interactive/Highest + 隐藏窗口）
#       ①每日 21:30 → 次日 08:45 每 15 分钟（夜间窗口，收盘后部署）；
#       ②每日 08:50 → 20:00 每 30 分钟（日间补档：登录若在交易时段恢复，几分钟内就能出 PASS，
#         不必干等收盘；20:00 收尾以避开客户端 20:54 自重启）。
#       触发器由 scripts/fix_qmt_nightly_retry_trigger.ps1 幂等重建（-NoDaySlot 可撤掉日间槽）。
#       之所以用 Interactive 而非 AGENTS 默认的 S4U：它要重启
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
# 日间补跑槽（08:50-20:00，2026-10-29 加、同日由 15:15 起扩到全天）专属的独立**小额度**计数：
# 绝不与夜间的 6 次/日 上限共用（否则日间 10 个槽会把额度吃光，把真正该重试的
# 21:30 起夜间窗口饿死）。
# 曾考虑再加一条「昨日 PASS 标记存在 ⇒ 日间槽跳过」的护栏，**已否决**：标记名字只说明
# 「哪天写的」，不说明「昨晚那一轮成没成」——昨天 16:xx 手工跑出的 PASS 会让今天的日间槽
# 误以为昨晚没事，而 latest.json 里躺着的可能正是昨晚那条 FAIL。判据只认**最近一次真实
# 闭环报告**（见下面 $recentRealPass），不认标记文件名。
$dayCnt   = Join-Path $sched ("qmt_nightly_retry_day_{0}.txt" -f $today)
$dayTried = 0
if (Test-Path $dayCnt) { [void]([int]::TryParse((Get-Content $dayCnt -Raw).Trim(), [ref]$dayTried)) }
# $hhmm 在这里算，guard 0（报告复用）与 1b（日间槽护栏）都要用它。
# 注意 "HHmm" 是**字符串**：只在「同一自然日内、不跨零点」的区间上单写界才安全（spec §10.3）。
$hhmm = Get-Date -Format "HHmm"
$isDaySlot = ($hhmm -gt "0845" -and $hhmm -lt "2100")
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

# 飞书卡片：只报「需要人知道的事」——补跑成功 / 次数用尽仍失败 / 复用已有 OK /
# 整夜未登录的收口提醒（08:30-08:45 那最后两槽，每日至多一张）。
# WAIT 中途不推，否则每 15 分钟刷屏。推送失败只记日志，绝不阻断主流程。
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
# $recentRealPass：24h 内是否有一次**真实**（非演练）deploy=PASS。夜间不看它（<4h 已由上面
# 的复用护栏拦掉，整夜本来就该重试）；日间槽看它——夜里已经成了就不必在收盘后再重启一次客户端。
$recentRealPass = $false
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
        $recentRealPass = (($ageH -lt 24) -and ($d0Deploy -eq "PASS") -and (-not $d0Dry))
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

# ---- 1) 时段闸（只对被部署的那台客户端生效）----
# 沙箱免闸：它是纯验证目标（passive 空转 / 无真钱 / 无盘中证据要保），2026-10-29 起
# 全天可重启 ⇒ 登录在交易时段恢复时能立刻补出 PASS。若 $sandbox 被改成主号等承载
# 在跑策略的客户端，闸自动回来（不在白名单里就仍按 08:50-15:10 拦）。
# 路径必须先归一再比：调用方/测试常传 D:/xxx 或带尾分隔符，原样 -eq 会静默失配，
# 把沙箱当成主号去拦（AGENTS「外部路径输入必须先归一」条）。
$gateExempt = ((($sandbox -replace '/', '\').TrimEnd('\').ToLower()) -eq 'd:\qmt交易端模拟')
if (-not $gateExempt -and $hhmm -ge "0850" -and $hhmm -le "1510") {
    if (-not $Force) { Write-Log ("SKIP 时段闸（{0}）不重启客户端（目标非沙箱：{1}）" -f $hhmm, $sandbox); exit 0 }
    Write-Log ("FORCE 越过时段闸（{0}）" -f $hhmm)
}

# ---- 1b) 日间槽专属护栏（2026-10-29 加，同日扩到 08:50-20:00）----
# 为什么日间也要有槽：补跑窗口原先只有 21:30→次日 08:45，登录若在**白天**恢复（券商维护
# 常在日间收尾）就得干等到 21:10 才出 PASS。现在沙箱免时段闸，日间槽从 08:50 覆盖到 20:00，
# 登录一恢复几分钟内就能出结果。日间槽仍比夜间槽更需要节制（每次都是一次真重启 + 一轮
# 构建，且客户端重启会打断 70180771 那台内置策略的当日日志连续性），所以护栏先跑在探针之前：
#   ①24h 内有过一次**真实** deploy=PASS（非演练）⇒ 没什么要修的，跳过。这条同时也是
#     「昨晚那轮成了」的判据（夜里的 PASS 报告到白天第一个槽仍在 24h 内）。刻意**不**用
#     「昨日 PASS 标记文件是否存在」来判：标记名只说明哪天写的、不说明成没成，会把
#     「昨天手工 PASS 过 + 昨晚真跑 FAIL 了」这种最需要白天补跑的情形误拦掉。
#   ②日间独立额度 2 次/日 ⇒ 用**单独**的计数文件，不占用夜间的 6 次上限（若与夜间共用额度，
#     白天几轮就会把夜里真正该重试的次数先吃光，等于把夜间链饿死）。
# 20:00 是最后一个日间槽：一次闭环约 5-15 分钟，留足余量避开客户端 20:54 的自重启。
if ($isDaySlot -and -not $Force) {
    if ($recentRealPass) {
        Write-Log "SKIP 日间槽：24h 内已有真实 deploy=PASS 报告，当日不再重复部署（额度留给真出问题的那晚）"
        exit 0
    }
    if ($dayTried -ge 2) {
        Write-Log ("SKIP 日间槽：今日 {0} 次额度已用尽（夜间 6 次上限不受影响），等今晚 21:30 起的补跑窗口" -f $dayTried)
        exit 0
    }
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
# EXIT= 是探针给的分级签名（0 已登录 / 2 进程在跑但未登录 / 4 进程不在 / 5 禁触路径）。
# 收口卡必须按签名说不同的话：2 是券商侧、本地无解；4 是**本机问题**（客户端没起来），
# 把后者也说成「券商侧、无人可修」会把排障方向带偏一整天。
$mExit = [regex]::Match($probeOut, "EXIT=(\d+)")
$pExit = if ($mExit.Success) { [int]$mExit.Groups[1].Value } else { -1 }
if (-not $mState.Success) { Write-Log ("WARN 探针输出异常：" + ($probeOut -replace "`r?`n", " | ")) }
try { if ($oldEnc) { [Console]::OutputEncoding = $oldEnc } } catch { }

if (-not $logged) {
    Write-Log ("WAIT 沙箱未登录：{0}" -f $state)
    # 收口提醒（2026-10-29 加）：WAIT 按设计不推卡（否则每 15 分钟刷屏），但整夜维护窗口
    # 一路 WAIT 到补跑窗口关闭（08:45）时也**一张卡都没有** ⇒ 没人知道"这一晚到底跑没跑、
    # 为什么没有 PASS"。所以在窗口关闭前最后一次触发时推一张汇总卡，每日至多一张。
    # 窗口是 21:30→次日 08:45，而 "HHmm" 是字符串比较："2130" -ge "0830" 也为真，
    # 单写下限会让它在 21:30 第一槽就推卡。必须同时封住上限，只留 08:30 / 08:45 最后两槽。
    if ($hhmm -ge "0830" -and $hhmm -lt "0900") {
        $wakeMark = Join-Path $sched ("qmt_retry_wakecard_{0}.mark" -f $today)
        if (!(Test-Path $wakeMark)) {
            # 按探针签名分级的结论句，别把本机问题写成券商问题
            $cause = switch ($pExit) {
                2 { "EXIT=2（TCP 已通、柜台登录报文不应答）属券商模拟环境侧，本机已无可修项；且穷举两台模拟客户端配置共 26 个服务端地址仅 4 个可达、登录节点只有 180.169.107.19:56001 一个（无备用可切）。若需尽快恢复可联系国金客服确认维护窗口。" }
                4 { "EXIT=4（沙箱客户端进程不在跑）⇒ 这是**本机问题**，不是券商侧：请查 QMT_Sandbox_LogonStart/BootStart 任务与 bin.x64 完整性（日志出现 initPythonEnv failed = 安装不完整，见 AGENTS 客户端启动失败判据），必要时手工启动 XtItClient.exe reboot。" }
                5 { "EXIT=5（探针拒绝访问的路径）⇒ 脚本被指向了非模拟客户端目录，属调用参数错误，需人工核对任务定义。" }
                default { "EXIT=$pExit（探针未给明确签名），请读 data\schedules\qmt_nightly_retry.log 尾部人工判定。" }
            }
            Set-Content -Path $wakeMark -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding UTF8
            Push-Card "[QMT] 夜间闭环整夜未登录，窗口 08:45 关闭" ("沙箱（70180771）整夜登录未恢复，补跑窗口即将关闭，本轮期**零部署动作**（不 build/不落盘/不重启），因此没有 PASS 也没有 FAIL 报告。`n最后一次探针：{0}`n今晚 21:10 主任务 + 21:30 起每 15 分钟补跑会自动继续，登录一恢复即自动补出 PASS 并推卡。`n{1}" -f $state, $cause) "warn"
        }
    }
    exit 0
}
$runNo = $attempts + 1
if ($isDaySlot) { $runNo = $dayTried + 1 }
Write-Log ("UP 沙箱已登录（{0}），补跑夜间闭环（{1}槽 第 {2} 次）" -f $state, $(if ($isDaySlot) { "日间" } else { "夜间" }), $runNo)

# ---- 4) 补跑闭环 ----
$runStart = Get-Date
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nightly | Out-Null
$rc = $LASTEXITCODE
# 计数按槽位分开写：日间只加日间额度，夜间只加夜间上限（两者共用一个计数会让
# 白天的 10 个槽把夜里真正需要重试的 6 次机会先吃光）。
if ($isDaySlot) {
    $dayTried += 1
    Set-Content -Path $dayCnt -Value $dayTried -Encoding UTF8
} else {
    $attempts += 1
    Set-Content -Path $cntFile -Value $attempts -Encoding UTF8
}

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
    Push-Card "[QMT] 夜间闭环补跑 PASS" ("沙箱登录已恢复，夜间闭环补跑成功。`nBUILD_TAG={0}`ndeploy={1}（退出码 {2}）`n第 {3} 次补跑（{4}槽）`n今日不再重试（已落 {5}）。{6}" -f $tag, $deployResult, $rc, $runNo, $(if ($isDaySlot) { "日间" } else { "夜间" }), (Split-Path $mark -Leaf), $extra) "info"
    exit 0
}
# 闭环自己也被登录前置闸拦过一次（探针判已登录后账号又掉线）⇒ 它整轮跳过、不写报告，
# 于是 latest.json 不是本轮的。这是「仍未登录」，不是闭环坏了：记 SKIP 不记 FAIL，
# 免得推「已登录但闭环 FAIL」这种会把排障方向带偏的卡。
if (($rc -eq 0) -and (-not $fresh)) {
    Write-Log "SKIP 闭环本轮被自身的登录前置闸拦下（latest.json 非本轮新写），未产生报告；沙箱应仍未登录，15 分钟后再试"
    exit 0
}
Write-Log ("FAIL 补跑未过 deploy={0} verdict={1} 期望 BUILD_TAG={2}（exit={3} 报告本轮新写={4} 演练={5}）" -f $deployResult, $verdict, $tag, $rc, $fresh, $dryRun)
# ★ 卡片必须按 deploy.result 分级给结论（与收口卡同一条规则：EXIT/结论不是中性信息，
#   它是「往哪个方向查」的指令）。原先无论什么原因都写「需查产物/模型注册」，
#   而 qmt_nightly.py 的 deploy.result 本身就带原因（拒绝（交易时段）/ 构建产物 / 未出 TAG /
#   重启失败），拿它分级就能把 4 类完全不同的故障分开指路。
$why = switch -Regex ($deployResult) {
    "拒绝"      { "[原因] 被闭环自带的交易时段闸（09:00-15:05）拒绝重启 ⇒ 目标不是沙箱（沙箱已免闸），请核对 $sandbox 是否被改动。不是产物也不是模型注册问题。" }
    "构建|产物"  { "[原因] 构建或产物校验没过（GBK 编码 / 首行 # coding=gbk / BUILD_TAG / 静态扫描）。请读 data\schedules\qmt_nightly.log 的构建段与 [DEPLOY] 行。" }
    "未出|TAG"   { "[原因] 已落盘重启但日志回读不到期望 BUILD_TAG ⇒ 优先怀疑模型未注册（configFormula not exist）或该客户端版本不加载明文 python\*.py。读 results\qmt_nightly\latest.md 的「客户端扫描」一节；必要时按 spec §10.11 的回滚预案在 2.1.19 对照组复现。" }
    "重启失败"   { "[原因] 客户端进程起不来（缺文件/安装不完整）。日志出现 initPythonEnv failed 即按 AGENTS「客户端启动失败判据」补 bin.x64。" }
    "未执行"     { "[原因] 闭环报告里 deploy 段是「未执行」⇒ 本轮在部署前就中断（构建腿之外的异常），读 latest.md 全文定位。" }
    default      { "[原因] 未归类的失败签名（deploy={0}），请读 results\qmt_nightly\latest.md。" -f $deployResult }
}
Push-Card "[QMT] 夜间闭环补跑 FAIL" ("沙箱已登录但闭环仍 FAIL（{0}槽 第 {1} 次补跑）。`ndeploy={2} verdict={3}`n期望 BUILD_TAG={4}，deploy 退出码={5}`n{6}`n注意：已登录 ⇒ 不再是券商登录问题；但具体往哪查以上面【原因】为准，别一律当成产物/模型注册。临时停用请建 data\schedules\qmt_retry_off。" -f $(if ($isDaySlot) { "日间" } else { "夜间" }), $runNo, $deployResult, $verdict, $tag, $rc, $why) "error"
exit 1
