# start_card_webhook.ps1 - daemon launcher for card_webhook.py
# Usage: on-logon auto-start + every-5min self-heal via Task Scheduler.
# Idempotent: exits immediately if an alive process exists (see data/card_webhook.pid).
param([int]$Port = 9001)
$ErrorActionPreference = "SilentlyContinue"
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataDir = Join-Path $proj "data"
$pidFile = Join-Path $dataDir "card_webhook.pid"
$logOut = Join-Path $dataDir "card_webhook.log"
$logErr = Join-Path $dataDir "card_webhook.err.log"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

# already running -> exit (harmless for repeated scheduler triggers)
if (Test-Path $pidFile) {
    try {
        $oldPid = [int](Get-Content $pidFile -Raw)
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) { exit 0 }
    } catch { }
}

# pick python (std-lib only for card_webhook; prefer bundled known-good, skip WindowsApps stub)
$py = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($py -like "*WindowsApps*") { $py = $null }
}
if (-not $py -or -not (Test-Path $py)) { return }

$p = Start-Process -FilePath $py `
    -ArgumentList @("card_webhook.py", "--port", "$Port") `
    -WorkingDirectory $proj `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError $logErr `
    -PassThru
$p.Id | Out-File $pidFile -Encoding ascii
Add-Content -Path $logOut -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  start_card_webhook: started pid=$($p.Id) port=$Port" -Encoding UTF8
