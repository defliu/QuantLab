# Re-register Quant_P16_QMT_NightlyRetry with a RECURRING daily trigger.
# Defect fixed (2026-10-29): the original triggers were MSFT_TaskTimeTrigger with a
# one-shot StartBoundary and NO ScheduleByDay, so the auto-retry only covered
# 2026-10-29 and would never fire again. There was also a 00:00-00:45 hole tonight.
#
# PS 5.1 notes learned the hard way:
#   * New-ScheduledTaskTrigger -Daily does NOT accept -RepetitionInterval/-Duration
#     (Daily parameter set has no repetition params -> AmbiguousParameterSet).
#   * A -Daily trigger's .Repetition is $null; you must assign a ClientOnly
#     MSFT_TaskRepetitionPattern built WITH the properties.
$ErrorActionPreference = "Stop"
$name = "Quant_P16_QMT_NightlyRetry"
$t = Get-ScheduledTask -TaskName $name

$userId = $t.Principal.UserId
if ([string]::IsNullOrWhiteSpace($userId)) { $userId = "$env:USERDOMAIN\$env:USERNAME" }

function New-RepPattern([string]$interval, [string]$duration) {
    return New-CimInstance -ClassName MSFT_TaskRepetitionPattern `
        -Namespace Root/Microsoft/Windows/TaskScheduler -ClientOnly `
        -Property @{ Interval = $interval; Duration = $duration; StopAtDurationEnd = $true }
}

# ---- trigger 1: every day 21:30 -> 08:45 next morning, every 15 min ----
$rep = New-ScheduledTaskTrigger -Daily -At "21:30"
$rep.Repetition = New-RepPattern "PT15M" "PT11H15M"

# ---- trigger 2: bridge for the rest of tonight (daily window only opens at 21:30) ----
$bridgeStart = (Get-Date).AddMinutes(2)
$bridgeEnd   = (Get-Date).Date.AddHours(8).AddMinutes(45)
$bridgeMins  = [math]::Ceiling(($bridgeEnd - $bridgeStart).TotalMinutes)
if ($bridgeMins -lt 15) { $bridgeMins = 15 }
$bridge = New-ScheduledTaskTrigger -Once -At $bridgeStart
$bridge.Repetition = New-RepPattern "PT15M" ("PT{0}M" -f $bridgeMins)

$triggers = @($rep, $bridge)
foreach ($x in $triggers) {
    Write-Host ("BUILD class={0} start={1} interval={2} duration={3}" -f `
            $x.CimClass.CimClassName, $x.StartBoundary, $x.Repetition.Interval, $x.Repetition.Duration)
}

$st = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -MultipleInstances IgnoreNew
$pr = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest

Set-ScheduledTask -TaskName $name -Trigger $triggers -Settings $st -Principal $pr | Out-Null

# ---- readback ----
$v = Get-ScheduledTask -TaskName $name
$vi = $v | Get-ScheduledTaskInfo
foreach ($x in $v.Triggers) {
    Write-Host ("READBACK-TRIG class={0} enabled={1} start={2} interval={3} duration={4}" -f `
            $x.CimClass.CimClassName, $x.Enabled, $x.StartBoundary, $x.Repetition.Interval, $x.Repetition.Duration)
}
Write-Host ("READBACK trigCount={0} principal={1}/{2} userId={3} MUI={4} timeLimit={5} startWhenAvailable={6}" -f `
        $v.Triggers.Count, $v.Principal.LogonType, $v.Principal.RunLevel, $v.Principal.UserId, `
        $v.Settings.MultipleInstances, $v.Settings.ExecutionTimeLimit, $v.Settings.StartWhenAvailable)
Write-Host ("READBACK state={0} last={1} result={2} next={3}" -f `
        $v.State, $vi.LastRunTime, $vi.LastTaskResult, $vi.NextRunTime)
