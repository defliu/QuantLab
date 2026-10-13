# start_feishu_listen.ps1 - daemon launcher for scripts/feishu_bridge/listen.py
# Usage: on-logon auto-start + every-5min self-heal via Task Scheduler.
# Idempotent: exits immediately if an alive process exists (see data/feishu_bridge/listen.pid).
#
# 注意：本文件含中文，必须保持 UTF-8 BOM，否则 PowerShell 5.1 按 GBK 解码会解析崩溃
#       （项目规则 T-20260903-015）。每次编辑后务必校验 BOM 仍在。
$ErrorActionPreference = "SilentlyContinue"
$root = "D:\QuantLab"
$dataDir = Join-Path $root "data\feishu_bridge"
$pidFile = Join-Path $dataDir "listen.pid"
$logOut = Join-Path $dataDir "listen.out.log"
$logErr = Join-Path $dataDir "listen.err.log"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

# already running -> exit (harmless for repeated scheduler triggers)
if (Test-Path $pidFile) {
    try {
        $oldPid = [int](Get-Content $pidFile -Raw)
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) { exit 0 }
    } catch { }
}

# pick python (prefer managed runtime, fall back to PATH but skip WindowsApps stub)
$py = "C:\Users\Administrator\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($py -like "*WindowsApps*") { $py = $null }
}
if (-not $py -or -not (Test-Path $py)) { return }

$p = Start-Process -FilePath $py `
    -ArgumentList @("scripts\feishu_bridge\listen.py") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError $logErr `
    -PassThru
$p.Id | Out-File $pidFile -Encoding ascii
Add-Content -Path $logOut -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  start_feishu_listen: started pid=$($p.Id)" -Encoding UTF8
