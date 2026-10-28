# QMT 登录守护：券商登录服务恢复后自动拉起两台客户端
# 2026-10-28 建：miniQMT 已停用；登录服务 180.169.107.19:56001 断线时客户端会退，
# 本脚本每 10 分钟探一次端口，通了就把没在跑的客户端拉起来（带 reboot 参数）。
#
# 规则：
#   1) 只探 56001（登录/代理），不通就直接退出，不碰客户端
#   2) 只按 -ClientDirs 白名单处理（默认主号 + 沙箱），绝不触碰交易版
#   3) 已有活实例（Threads>0）则跳过，不重复启动
#   4) 启动后 90 秒交回 scripts/qmt_login_probe.py 判定（TCP 通 ≠ 已登录，别用日志正则猜）
#   5) 停机开关：存在 data\schedules\qmt_watch_off 文件时什么都不做（人工维护用）
#   6) 部署互斥：存在新鲜的 data\schedules\qmt_deploy.lock（<40 分钟）时本轮不拉起客户端，
#      避让夜间闭环自己的「关闭 → reboot 重启」，防止两个写者抢同一个客户端实例

param(
    [string[]]$ClientDirs = @("D:\国金QMT交易端模拟", "D:\QMT交易端模拟"),
    [string]$ProbeHost = "180.169.107.19",
    [int]$ProbePort = 56001
)

$root = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$logDir = Join-Path $root "data\schedules"
$log = Join-Path $logDir "qmt_watch_login.log"
$offFile = Join-Path $logDir "qmt_watch_off"

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try {
        if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
        Add-Content -Path $log -Value $line -Encoding UTF8
    } catch { }
}

if (Test-Path $offFile) { Write-Log "SKIP 存在停机开关 $offFile"; exit 0 }

# ---- 0) 部署互斥：夜间闭环正在「关客户端 → 重启」时绝不抢跑 ----
# 闭环中途有几十秒的无进程窗口，本守护若此时按「客户端不在跑」抢先 Start-Process，
# 就会和闭环自己的 reboot 重启撞车（单实例交接 → PID 抖动 → 部署误判失败）。
# 锁由 qmt_nightly_deploy.ps1 建/删；脚本被强杀会留残锁，故按 40 分钟龄期自动失效。
$lock = Join-Path $logDir "qmt_deploy.lock"
if (Test-Path $lock) {
    $lockAge = ((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes
    if ($lockAge -lt 40) {
        Write-Log ("SKIP 夜间闭环部署中（锁建于 {0:N0} 分钟前），本轮不拉起客户端" -f $lockAge)
        exit 0
    }
    Write-Log ("STALE 部署锁已 {0:N0} 分钟未更新（闭环可能被强杀），忽略锁继续" -f $lockAge)
}

# ---- 1) 探登录服务 ----
$ok = $false
try {
    $c = New-Object System.Net.Sockets.TcpClient
    $ar = $c.BeginConnect($ProbeHost, $ProbePort, $null, $null)
    if ($ar.AsyncWaitHandle.WaitOne(3000, $false) -and $c.Connected) { $ok = $true }
    $c.Close()
} catch { $ok = $false }

if (-not $ok) {
    # 兜底：早盘窗口（08:40-09:20）即使端口探不通也把客户端拉起来，
    # 否则服务若在盘中才恢复，整段行情仍会空着。拉起后若仍停在登录框，下一轮会记录结论。
    $hm = (Get-Date).ToString("HHmm")
    if ($hm -ge "0840" -and $hm -le "0920") {
        Write-Log ("WINDOW 登录服务不可达，但处于早盘兜底窗口（{0}），继续尝试拉起" -f $hm)
    } else {
        Write-Log ("WAIT 登录服务 {0}:{1} 不可达，本轮不动客户端" -f $ProbeHost, $ProbePort)
        exit 0
    }
}
Write-Log ("UP 登录服务 {0}:{1} 可达，检查客户端" -f $ProbeHost, $ProbePort)

# ---- 2) 逐台检查/拉起 ----
$started = @()
foreach ($dir in $ClientDirs) {
    $exe = Join-Path $dir "bin.x64\XtItClient.exe"
    if (!(Test-Path $exe)) { Write-Log "SKIP 找不到 exe: $exe"; continue }

    $alive = $false
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='XtItClient.exe'" -ErrorAction Stop |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) }
        foreach ($p in $procs) {
            try {
                $h = Get-Process -Id $p.ProcessId -ErrorAction Stop
                if ($h.Threads.Count -gt 0) { $alive = $true }
            } catch { }
        }
    } catch { }

    if ($alive) { Write-Log "SKIP 已在运行 [$dir]"; continue }

    try {
        $np = Start-Process -FilePath $exe -ArgumentList "reboot" -WorkingDirectory (Split-Path $exe) -PassThru
        Write-Log ("START [{0}] pid={1} reboot 模式" -f $dir, $np.Id)
        $started += $dir
    } catch {
        Write-Log ("FAIL 启动失败 [{0}]: {1}" -f $dir, $_.Exception.Message)
    }
}

if ($started.Count -eq 0) { exit 0 }

# ---- 3) 90 秒后回读登录结果 ----
# 判定一律交给 scripts/qmt_login_probe.py，不在这里用正则猜日志：
# 「Proxy client not logged」只说明还没登上，分不清是 TCP 断、柜台不应答还是缺验证码，
# 2026-10-28 夜就是被这句误导成「等人工点登录」，实际券商侧 200003 超时、点了也一样超时。
Start-Sleep -Seconds 90
$probe = "D:\QuantLab\scripts\qmt_login_probe.py"
$pyExe = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"
if (!(Test-Path $pyExe)) { $pyExe = "D:\Python311\python.exe" }
foreach ($dir in $started) {
    $hit = "无日志"
    if ((Test-Path $probe) -and (Test-Path $pyExe)) {
        $env:PYTHONIOENCODING = "utf-8"
        $oldEnc = $null
        try { $oldEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
        $pv = (& $pyExe $probe --verdict --client $dir 2>&1 | Out-String).Trim()
        $mState = [regex]::Match($pv, "STATE=(.+)$")
        $state = if ($mState.Success) { $mState.Groups[1].Value } else { ($pv -replace "`r?`n", " | ") }
        try { if ($oldEnc) { [Console]::OutputEncoding = $oldEnc } } catch { }
        if ($pv -match "LOGGED=1") { $hit = "login success" } else { $hit = ("未登录：{0}" -f $state) }
    } else {
        $m = Join-Path $dir ("userdata\log\XtClient_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
        if (Test-Path $m) {
            $tail = Get-Content -Path $m -Tail 400 -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($tail -match "login success") { $hit = "login success" }
            elseif ($tail -match "Proxy client not logged") { $hit = "未登录（探针不可用，日志兜底）" }
        }
    }
    Write-Log ("RESULT [{0}] {1}" -f $dir, $hit)
}
exit 0
