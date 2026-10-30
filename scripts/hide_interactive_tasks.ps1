# Rewrite Interactive scheduled tasks to launch through scripts\run_hidden.py
# so they stop popping console windows (Win11 delegates consoles to Windows
# Terminal, which ignores `powershell.exe -WindowStyle Hidden`).
#
# Interactive + Highest is preserved: these tasks restart the QMT GUI clients,
# and S4U would hide the login prompt in session 0.
#
# Usage: powershell -File scripts\hide_interactive_tasks.ps1 [-WhatIf]

param([switch]$WhatIf)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$pyw = "D:\Python311\pythonw.exe"
$launcher = "D:\QuantLab\scripts\run_hidden.py"
$logDir = "D:\QuantLab\data\schedules\task_hidden"

if (!(Test-Path $pyw)) { throw "pythonw not found: $pyw" }
if (!(Test-Path $launcher)) { throw "launcher not found: $launcher" }
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# TaskName -> payload command line (the child that must run invisibly)
$targets = [ordered]@{
    "QMT_WatchLogin_10min" = @(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "D:\QuantLab\scripts\qmt_watch_login.ps1")
    "QMT_Prod_LogonStart" = @(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "D:\QuantLab\scripts\qmt_autostart.ps1",
        "-ClientDir", "D:\国金QMT交易端模拟")
    "QMT_Sandbox_LogonStart" = @(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "D:\QuantLab\scripts\qmt_autostart.ps1",
        "-ClientDir", "D:\QMT交易端模拟")
    "Quant_P16_QMT_NightlyDeploy" = @(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "D:\QuantLab\projects\Project_16_LightGBM股票大师\qmt_nightly_deploy.ps1")
    "Quant_P16_QMT_NightlyRetry" = @(
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "D:\QuantLab\projects\Project_16_LightGBM股票大师\qmt_nightly_retry.ps1")
}

foreach ($name in $targets.Keys) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host ("SKIP missing task: " + $name); continue }

    $payload = $targets[$name]
    $logPath = Join-Path $logDir ($name + ".log")

    # ArgumentList is passed verbatim to CreateProcess, so each element is
    # wrapped in double quotes; run_hidden.py receives them as separate argv.
    # NOTE: pythonw.exe takes the script path as a bare first argument --
    # `-File` is a PowerShell parameter and would make pythonw exit 2.
    $quoted = @()
    foreach ($a in $payload) { $quoted += ('"' + $a + '"') }
    $argLine = '"' + $launcher + '" --log "' +
               $logPath + '" -- ' + ($quoted -join ' ')

    if ($WhatIf) {
        Write-Host ("[WHATIF] " + $name)
        Write-Host ("  new exec: " + $pyw)
        Write-Host ("  new args: " + $argLine)
        continue
    }

    $action = New-ScheduledTaskAction -Execute $pyw -Argument $argLine
    Set-ScheduledTask -TaskName $name -Action $action | Out-Null

    # Read back and verify
    $after = Get-ScheduledTask -TaskName $name
    $okExec = ($after.Actions[0].Execute -eq $pyw)
    $okArgs = ($after.Actions[0].Arguments -eq $argLine)
    $okLT = ($after.Principal.LogonType -eq "Interactive")
    $okRun = ($after.Principal.RunLevel -eq "Highest")
    Write-Host ("SET " + $name + " exec=" + $okExec + " args=" + $okArgs +
                " logonType=" + $okLT + " runLevel=" + $okRun +
                " state=" + $after.State)
    if (!($okExec -and $okArgs -and $okLT -and $okRun)) {
        throw ("verify failed for " + $name)
    }
}

Write-Host "DONE"
