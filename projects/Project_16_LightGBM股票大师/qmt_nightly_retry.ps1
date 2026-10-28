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
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try {
        if (!(Test-Path $sched)) { New-Item -ItemType Directory -Force -Path $sched | Out-Null }
        Add-Content -Path $log -Value $line -Encoding UTF8
    } catch { }
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
        if (($ageH -lt 4) -and ($d0.verdict -eq "OK")) {
            Set-Content -Path $mark -Value ("{0} 复用已有 OK 报告 BUILD_TAG={1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $d0.deploy.tag) -Encoding UTF8
            Write-Log ("SKIP 最近 {0:N1}h 内已有 verdict=OK 报告（BUILD_TAG={1}），不重复补跑" -f $ageH, $d0.deploy.tag)
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
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $nightly | Out-Null
$rc = $LASTEXITCODE
$attempts += 1
Set-Content -Path $cntFile -Value $attempts -Encoding UTF8

$verdict = "无报告"
$tag = ""
$deployResult = ""
$reportJson = Join-Path $dir "results\qmt_nightly\latest.json"
if (Test-Path $reportJson) {
    try {
        $d = Get-Content $reportJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $verdict = [string]$d.verdict
        if ($d.deploy) {
            $tag = [string]$d.deploy.tag
            $deployResult = [string]$d.deploy.result
        }
    } catch { Write-Log "WARN latest.json 解析失败" }
}

if ($verdict -eq "OK") {
    Set-Content -Path $mark -Value ("{0} BUILD_TAG={1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $tag) -Encoding UTF8
    Write-Log ("PASS 夜间闭环补跑成功 verdict=OK deploy={0} BUILD_TAG={1}，当日不再重试" -f $deployResult, $tag)
    exit 0
}
Write-Log ("FAIL 补跑未过 verdict={0} deploy={1} 期望 BUILD_TAG={2}（exit={3}）" -f $verdict, $deployResult, $tag, $rc)
exit 1
