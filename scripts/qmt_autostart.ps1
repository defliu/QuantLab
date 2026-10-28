# QMT 客户端自启（开机 / 登录两类触发共用）
# 2026-10-28 建：2.1.33.0 客户端被强杀后会卡死，只能靠重启 OS + 自启恢复
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File qmt_autostart.ps1 -ClientDir "D:\国金QMT交易端模拟"
#
# 规则：
#   1) 只认 ExecutablePath 落在 -ClientDir 下的进程（不误判另一台客户端）
#   2) 已有活着（Threads.Count > 0）的实例则直接退出，避免开机自启与登录自启各起一个
#   3) 启动必须带 reboot 参数（不带会停在 Proxy client not logged，一个策略都不跑）
#   4) 不杀任何进程；本脚本只负责“没在跑就拉起来”

param(
    [Parameter(Mandatory = $true)][string]$ClientDir
)

$exe = Join-Path $ClientDir "bin.x64\XtItClient.exe"
$logDir = "D:\QuantLab\projects\Project_16_LightGBM股票大师\data\schedules"
$log = Join-Path $logDir "qmt_autostart.log"

function Write-Log([string]$msg) {
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $ClientDir, $msg
    try {
        if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
        Add-Content -Path $log -Value $line -Encoding UTF8
    } catch { }
}

if (!(Test-Path $exe)) {
    Write-Log "SKIP 找不到 exe: $exe"
    exit 2
}

# ---- 是否已有活着实例 ----
$alivePids = @()
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='XtItClient.exe'" -ErrorAction Stop |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($ClientDir, [StringComparison]::OrdinalIgnoreCase) }
    foreach ($p in $procs) {
        try {
            $h = Get-Process -Id $p.ProcessId -ErrorAction Stop
            if ($h.Threads.Count -gt 0) { $alivePids += ("{0}(sess{1})" -f $p.ProcessId, $p.SessionId) }
        } catch { }
    }
} catch {
    Write-Log "WARN 枚举进程失败（继续尝试启动）: $($_.Exception.Message)"
}

if ($alivePids.Count -gt 0) {
    Write-Log "SKIP 已在运行 $($alivePids -join ',')"
    exit 0
}

# ---- 拉起（reboot 模式）----
try {
    $p = Start-Process -FilePath $exe -ArgumentList "reboot" -WorkingDirectory (Split-Path $exe) -PassThru
    Write-Log "START pid=$($p.Id) reboot 模式"
    exit 0
} catch {
    Write-Log "FAIL 启动失败: $($_.Exception.Message)"
    exit 3
}
