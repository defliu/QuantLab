# p16_schedule_stagger.ps1  (ASCII only - safe without BOM on PS 5.1)
# Purpose : stagger P16 paper-forward pipeline away from heavy tasks (OOM fix, 2026-10-19)
#   paper_forward_daily : 16:45 -> 16:50   ('daily' mode ends ~16:47:35)
#   retrain             : 17:00 -> 17:30   (leave ~40 min for paper-forward)
# Safety  : DRY-RUN by default; -Apply to write. Exports current task XML to %TEMP% first.
#           Skips any task whose trigger count != 1 (avoid clobbering multi-trigger tasks).
#           Uses ScheduledTask cmdlets only (no schtasks/XML string concat).
# Usage   : powershell -NoProfile -ExecutionPolicy Bypass -File p16_schedule_stagger.ps1
#           powershell -NoProfile -ExecutionPolicy Bypass -File p16_schedule_stagger.ps1 -Apply

param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $env:TEMP "p16_task_backup_$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null

function Find-TaskByArg([string]$pattern) {
    Get-ScheduledTask | Where-Object {
        $a = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' '
        $a -like $pattern
    }
}

Write-Host "== P16 schedule stagger ==" -ForegroundColor Cyan
Write-Host ("Mode: " + $(if ($Apply) { 'APPLY' } else { 'DRY-RUN (add -Apply to write)' }))
Write-Host ("Backup dir: " + $backup)

$targets = @(
    @{ Key = 'paper_forward'; Match = '*paper_forward_daily.ps1*'; NewTime = '16:50' },
    @{ Key = 'retrain';       Match = '*retrain*';               NewTime = '17:30' }
)

foreach ($t in $targets) {
    $hits = @(Find-TaskByArg $t.Match)
    if ($hits.Count -eq 0) { Write-Host ("[" + $t.Key + "] NOT FOUND (pattern " + $t.Match + ")") -ForegroundColor Yellow; continue }
    if ($hits.Count -gt 1) {
        Write-Host ("[" + $t.Key + "] MULTIPLE matches -> skip, resolve manually:") -ForegroundColor Yellow
        $hits | ForEach-Object { Write-Host ("   " + $_.TaskName) }
        continue
    }
    $task = $hits[0]
    Write-Host ("[" + $t.Key + "] task=" + $task.TaskName + "  triggers=" + $task.Triggers.Count) -ForegroundColor Green
    $task.Triggers | ForEach-Object {
        Write-Host ("    start=" + $_.StartBoundary + "  days=" + $_.DaysOfWeek + "  type=" + $_.CimClass.CimClassName)
    }
    if ($task.Triggers.Count -ne 1) {
        Write-Host ("    !! trigger count != 1 -> SKIP (would drop other schedules)") -ForegroundColor Yellow
        continue
    }
    Export-ScheduledTask -TaskName $task.TaskName | Out-File (Join-Path $backup ($task.TaskName + '.xml')) -Encoding utf8
    if (-not $Apply) { Write-Host ("    [DRY-RUN] would set -> " + $t.NewTime); continue }
    Set-ScheduledTask -TaskName $task.TaskName -Trigger (
        New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $t.NewTime) | Out-Null
    $now = (Get-ScheduledTask -TaskName $task.TaskName).Triggers | ForEach-Object { $_.StartBoundary }
    Write-Host ("    [APPLIED] readback start=" + ($now -join ',')) -ForegroundColor Green
}

Write-Host "Done. Revert: Register-ScheduledTask -Xml (Get-Content <backup>.xml -Raw) -TaskName <name> -Force"
