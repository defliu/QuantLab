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
#
# 2026-10-29: added a daytime band (daily 15:15 -> 20:00, every 30 min). When the broker
# login recovers during the day the old window (21:30 -> 08:45) made us wait until 21:10
# for the next PASS. Daytime guards (last-night PASS / real PASS within 24h / own 2-per-day
# budget) live in qmt_nightly_retry.ps1, not here.
# Rollback: .\fix_qmt_nightly_retry_trigger.ps1 -NoDaySlot
# NOTE: keep this file ASCII-only -- without a BOM PowerShell 5.1 decodes it as GBK and
# Chinese comments turn into parser errors (AGENTS UTF-8 BOM rule).
param([switch]$NoDaySlot)
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

# ---- trigger 2: daytime band, every day 15:15 -> 20:00 every 30 min ----
# Duration PT4H50M so the 20:00 slot definitely fires while 20:30 does not:
# a deploy run takes ~5-15 min, and the client self-restarts at 20:54.
$trigList = @($rep)
if (-not $NoDaySlot) {
    $day = New-ScheduledTaskTrigger -Daily -At "15:15"
    $day.Repetition = New-RepPattern "PT30M" "PT4H50M"
    $trigList += $day
}

# ---- trigger 3: bridge for the rest of the current night window ----
# Only meaningful while the night window is still open (i.e. local time < 08:45):
# the daily -At 21:30 trigger only opened yesterday evening, so if this script is
# re-run in the afternoon the bridge would create a bogus 15-minute daytime burst.
$nightEnd = (Get-Date).Date.AddHours(8).AddMinutes(45)
if ((Get-Date) -lt $nightEnd) {
    $bridgeStart = (Get-Date).AddMinutes(2)
    $bridgeMins  = [math]::Ceiling(($nightEnd - $bridgeStart).TotalMinutes)
    if ($bridgeMins -lt 15) { $bridgeMins = 15 }
    $bridge = New-ScheduledTaskTrigger -Once -At $bridgeStart
    $bridge.Repetition = New-RepPattern "PT15M" ("PT{0}M" -f $bridgeMins)
    $trigList += $bridge
} else {
    Write-Host "SKIP-BRIDGE not inside the night window (now >= 08:45), no bridge trigger built"
}

$triggers = $trigList
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
