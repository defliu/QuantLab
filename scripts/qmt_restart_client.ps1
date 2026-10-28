# QMT 客户端手动重启助手（2026-10-29 立，调试用）
#
# 为什么需要：以前「调试时想重启客户端」只能手工杀进程或临时拼一行命令，
# 既容易误杀另一台客户端，也容易和夜间闭环自己的重启撞车（两边各跑一遍
# 关闭→reboot → PID 交接抖动 → 记成假失败）。这个脚本把三件事固定下来：
#   1) 只按 **ExecutablePath 前缀**认进程，绝不按进程名杀（同机三台客户端同名）；
#   2) 先查部署锁 qmt_deploy.lock：闭环正在跑就 SKIP（写锁的人是闭环，不是这里）；
#   3) 每台客户端一律带 `reboot` 参数启动（不带参数会停在 Proxy client not logged，
#      一个策略都不跑 —— 10-28 实测）。
#
# 口径（2026-10-29 诚哥拍板：主号/沙箱都是模拟账号）：
#   * 沙箱 D:\QMT交易端模拟（70180771）：纯验证目标、策略 passive 空转、无真钱、
#     无盘中证据要保 ⇒ **任何时间可重启**，不受交易时段限制；
#   * 主号 D:\国金QMT交易端模拟（67014907）：同样无真钱，但它**跑着 P16 V1.3 / G2**
#     并承担模拟盘复跑与前向验证的证据链 ⇒ 09:00-15:05 重启要显式加 -Force，
#     并且会在日志里记 WARN（当天该策略的运行连续性会被打断，核对时需注明）；
#   * 交易版 D:\国金证券QMT交易端：**永远不在本脚本的选项里**，路径里出现该串即硬拒绝。
#
# 用法：
#   .\qmt_restart_client.ps1 -Target 沙箱 -Reason "验证新产物加载"
#   .\qmt_restart_client.ps1 -Target 主号 -Force -Reason "盘中排障"
#
# 退出码：0 已重启 / 2 被锁跳过（闭环在跑）/ 3 路径被拒 / 4 客户端没在跑 / 5 重启失败
param(
    [Parameter(Mandatory=$true)][ValidateSet("沙箱", "主号")][string]$Target,
    [string]$Reason = "",
    [switch]$Force,
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Continue"
$dir     = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sched   = Join-Path $dir "data\schedules"
$log     = Join-Path $sched "qmt_restart_client.log"
$lock    = Join-Path $sched "qmt_deploy.lock"
$FORBID  = "国金证券QMT交易端"

$map = @{
    "沙箱" = "D:\QMT交易端模拟"
    "主号" = "D:\国金QMT交易端模拟"
}
$clientDir = $map[$Target]

function Write-Log([string]$msg) {
    $msg = ($msg -replace "\r?\n", " | ")
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try {
        if (!(Test-Path $sched)) { New-Item -ItemType Directory -Force -Path $sched | Out-Null }
        Add-Content -Path $log -Value $line -Encoding UTF8
    } catch { }
    Write-Host $line
}

# ---- 0) 硬拒绝交易版（真钱账户，任何自动化都不许碰）----
if ($clientDir.Contains($FORBID)) {
    Write-Log ("REFUSE 目标路径属于交易版（{0}），本脚本拒绝操作" -f $clientDir)
    exit 3
}

# ---- 1) 部署锁：闭环在跑就不动手 ----
if (Test-Path $lock) {
    $lockAge = ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes
    $lockPid = 0
    try {
        $txt = Get-Content $lock -Raw -Encoding UTF8
        if ($txt -match "PID=(\d+)") { $lockPid = [int]$Matches[1] }
    } catch { }
    $holder = if ($lockPid -gt 0) { Get-Process -Id $lockPid -ErrorAction SilentlyContinue } else { $null }
    if (($lockAge -lt 40) -and ($null -ne $holder)) {
        Write-Log ("SKIP 部署锁被占用（PID={0} 锁龄 {1:N0} 分钟）⇒ 闭环正在跑，等它结束再重启" -f $lockPid, $lockAge)
        exit 2
    }
    Write-Log ("LOCK-STALE 残锁（PID={0} 存活={1} 锁龄 {2:N0} 分钟）⇒ 继续" -f $lockPid, ($null -ne $holder), $lockAge)
}

# ---- 2) 交易时段：只对主号要求 -Force ----
$hhmm = Get-Date -Format "HHmm"
if ($Target -eq "主号" -and $hhmm -ge "0900" -and $hhmm -le "1505" -and (-not $Force)) {
    Write-Log ("REFUSE 主号在交易时段（{0}）重启需 -Force：它会打断 P16 V1.3 / G2 当日的运行连续性，" -f $hhmm)
    Write-Log "       仅供模拟盘验证的证据链需要注明中断。沙箱无此限制（-Target 沙箱）。"
    exit 2
}

# ---- 3) 按 ExecutablePath 前缀找进程（绝不按名字杀）----
$prefix = $clientDir.TrimEnd('\') + '\'
$procs = @(Get-Process XtItClient -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
})
if ($procs.Count -eq 0) {
    Write-Log ("NOTRUNNING {0}（{1}）没有在跑的 XtItClient；请用 qmt_autostart.ps1 或手工拉起" -f $Target, $clientDir)
    exit 4
}
foreach ($p in $procs) {
    Write-Log ("STOP {0} PID={1} path={2}" -f $Target, $p.Id, $p.Path)
}

# ---- 4) 优雅关闭 → 强杀 → 带 reboot 重启 ----
foreach ($p in $procs) {
    try { [void]$p.CloseMainWindow() } catch { }
}
$deadline = (Get-Date).AddSeconds($WaitSeconds)
foreach ($p in $procs) {
    while (-not $p.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    if (-not $p.HasExited) {
        Write-Log ("KILL PID={0}（{1}s 内未优雅退出）" -f $p.Id, $WaitSeconds)
        try { Stop-Process -Id $p.Id -Force } catch { }
    }
}
Start-Sleep -Seconds 2

$exe = Join-Path $clientDir "bin.x64\XtItClient.exe"
if (!(Test-Path $exe)) {
    Write-Log ("FAIL 启动器不存在：{0}" -f $exe)
    exit 5
}
try {
    Start-Process -FilePath $exe -ArgumentList "reboot" -WorkingDirectory (Join-Path $clientDir "bin.x64") | Out-Null
} catch {
    Write-Log ("FAIL Start-Process 抛错：{0}" -f $_.Exception.Message)
    exit 5
}

# ---- 5) 确认新进程起来了（按路径前缀复核，防串到另一台）----
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    $now = @(Get-Process XtItClient -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    if ($now.Count -gt 0) { $ok = $true; $newPid = $now[0].Id; break }
}
if ($ok) {
    Write-Log ("OK 已重启 {0} 新PID={1} 参数=reboot 理由={2}" -f $Target, $newPid, $Reason)
    exit 0
}
Write-Log ("FAIL 重启后 20s 内没看到 {0} 的 XtItClient 进程" -f $Target)
exit 5
