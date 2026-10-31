# Register (or update) the 21f shadow arm task that runs AFTER the Tushare main-library
# refresh (17:40), so the shadow records real "forward" samples instead of backfill.
#
# ASCII-only on purpose: a .ps1 containing Chinese must carry exactly one UTF-8 BOM.
# Project path comes from $PSScriptRoot, so no Chinese literal is embedded here.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_shadow_after_main_task.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_shadow_after_main_task.ps1 -At 17:50
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_shadow_after_main_task.ps1 -Unregister
param(
    [switch]$Unregister,
    [string]$At = "17:50"
)

$ErrorActionPreference = "Stop"
$taskName = "Quant_P16_Shadow21f_1750"
$proj = $PSScriptRoot
$pythonw = "D:\Python311\pythonw.exe"
$launcher = "D:\QuantLab\scripts\run_hidden.py"
$script = Join-Path $proj "p16_shadow_after_main.py"
$log = Join-Path $proj "data\schedules\p16_shadow_after_main.log"
# The script needs pandas/numpy -> use the miniqmt venv python (has them).
$venvPy = "C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "unregistered: $taskName"
    exit 0
}

foreach ($p in @($pythonw, $launcher, $script, $venvPy)) {
    if (!(Test-Path $p)) { throw "missing: $p" }
}

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('"{0}" --log "{1}" -- "{2}" "{3}"' -f $launcher, $log, $venvPy, $script) `
    -WorkingDirectory $proj

# 17:50 Mon-Fri: Tushare daily refresh runs 17:40, so main library already has today's bar.
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $At

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName
Write-Output ("registered: {0}" -f $t.TaskName)
Write-Output ("  exec : {0}" -f $t.Actions[0].Execute)
Write-Output ("  args : {0}" -f $t.Actions[0].Arguments)
Write-Output ("  logon: {0} / {1}" -f $t.Principal.LogonType, $t.Principal.RunLevel)
Write-Output ("  trig : {0} {1}" -f $t.Triggers[0].StartBoundary, $t.Triggers[0].DaysOfWeek)
