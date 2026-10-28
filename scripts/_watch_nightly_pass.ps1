# ASCII-only watcher: exit as soon as tonight's real nightly-loop PASS lands.
# Condition = data\schedules\qmt_nightly_pass_<yyyyMMdd>.mark exists (written by
# qmt_nightly_retry.ps1 only after deploy.result=PASS with a non-empty BUILD_TAG,
# non-dry-run, fresh report), or the 21:10 scheduled task produced a PASS report.
param(
    [int]$MaxHours = 8,
    [int]$EverySeconds = 30
)
$sched = "D:\QuantLab\projects\" + (Get-ChildItem "D:\QuantLab\projects" -Directory |
    Where-Object { $_.Name -like "Project_16_*" } | Select-Object -First 1).Name + "\data\schedules"
$deadline = (Get-Date).AddHours($MaxHours)
$start = Get-Date
Write-Output ("WATCH-START {0} sched={1}" -f $start.ToString("HH:mm:ss"), $sched)
while ((Get-Date) -lt $deadline) {
    $mark = Get-ChildItem -Path $sched -Filter "qmt_nightly_pass_*.mark" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $start } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($mark) {
        Write-Output ("PASS-DETECTED {0} file={1} content={2}" -f `
            (Get-Date -Format "HH:mm:ss"), $mark.Name,
            ((Get-Content $mark.FullName -Raw -Encoding UTF8).Trim()))
        exit 0
    }
    Start-Sleep -Seconds $EverySeconds
}
Write-Output ("WATCH-TIMEOUT {0} no PASS mark within {1}h" -f (Get-Date -Format "HH:mm:ss"), $MaxHours)
exit 3
