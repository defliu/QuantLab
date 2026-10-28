# Register/rebuild the two QMT signal-hub scheduled tasks (2026-10-29).
#
#   Quant_P16_SignalHub_Start   daily 09:15  -> qmt_signal_hub.ps1 -Mode Start
#   Quant_P16_SignalHub_Stop    daily 15:10  -> qmt_signal_hub.ps1 -Mode Stop
#
# Why two tasks: a scheduled task has ONE action, so start/stop need separate tasks
# (a single "auto by clock" action would hide which direction ran on failure).
# LogonType S4U: the hub is a plain console process, no GUI involved -> S4U is correct
# and avoids an always-logged-on-desktop requirement.
#
# Idempotent: re-running overwrites both tasks. Unregister with -Unregister.
# NOTE: keep this file ASCII-only (no BOM) -- PS 5.1 decodes BOM-less files as GBK.
param([switch]$Unregister)
$ErrorActionPreference = "Stop"

$ps1 = "D:\QuantLab\scripts\qmt_signal_hub.ps1"
$defs = @(
    @{ Name = "Quant_P16_SignalHub_Start"; At = "09:15"; Mode = "Start" },
    @{ Name = "Quant_P16_SignalHub_Stop";  At = "15:10"; Mode = "Stop"  }
)

if ($Unregister) {
    foreach ($d in $defs) {
        try { Unregister-ScheduledTask -TaskName $d.Name -Confirm:$false -ErrorAction Stop
              Write-Host ("UNREGISTERED " + $d.Name) } catch { Write-Host ("SKIP " + $d.Name) }
    }
    exit 0
}

if (!(Test-Path $ps1)) { throw ("missing script: " + $ps1) }
$userId = "$env:USERDOMAIN\$env:USERNAME"
if ([string]::IsNullOrWhiteSpace($env:USERDOMAIN)) { $userId = "Administrator" }

foreach ($d in $defs) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode {1}' -f $ps1, $d.Mode)
    $trigger = New-ScheduledTaskTrigger -Daily -At $d.At
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

    Register-ScheduledTask -TaskName $d.Name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null

    $t = Get-ScheduledTask -TaskName $d.Name
    $i = Get-ScheduledTaskInfo -TaskName $d.Name
    Write-Host ("READBACK {0} state={1} logon={2}/{3} at={4} args={5} next={6}" -f `
        $t.TaskName, $t.State, $t.Principal.LogonType, $t.Principal.RunLevel,
        $t.Triggers[0].StartBoundary, $t.Actions[0].Arguments, $i.NextRunTime)
}
