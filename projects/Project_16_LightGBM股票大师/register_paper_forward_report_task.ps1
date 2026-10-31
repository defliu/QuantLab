# Register (or update) the daily P16 paper-forward Feishu push task.
#
# ASCII-only on purpose: a .ps1 containing Chinese must carry exactly one
# UTF-8 BOM, and PowerShell 5.1 mis-decodes it otherwise. The project path is
# taken from $PSScriptRoot, so no Chinese literal is embedded here.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_paper_forward_report_task.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_paper_forward_report_task.ps1 -At 17:20
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_paper_forward_report_task.ps1 -Unregister
param(
    [switch]$Unregister,
    [string]$At = "17:05"
)

$ErrorActionPreference = "Stop"
$taskName = "Quant_P16_PaperForward_Report"
$proj = $PSScriptRoot
$python = "D:\Python311\python.exe"
$pythonw = "D:\Python311\pythonw.exe"
$launcher = "D:\QuantLab\scripts\run_hidden.py"
$script = Join-Path $proj "p16_paper_forward_report.py"
$log = Join-Path $proj "data\schedules\p16_paper_forward_task.log"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output "unregistered: $taskName"
    exit 0
}

foreach ($p in @($python, $pythonw, $launcher, $script)) {
    if (!(Test-Path $p)) { throw "missing: $p" }
}

# pythonw + run_hidden.py => no console window even if the task ever runs
# interactively; exit code and stdout are still preserved.
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('"{0}" --log "{1}" -- "{2}" "{3}" --wait-min 20' -f $launcher, $log, $python, $script) `
    -WorkingDirectory $proj

# 17:05 Mon-Fri: paper_forward_daily.ps1 starts 16:55 and usually finishes
# by 17:02; the report script itself waits up to 20 minutes for today's file.
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
