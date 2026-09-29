# Project_21 Phase1: run 2019-2024 signal scan as 6 parallel annual slices.
# ASCII-only on purpose (no BOM needed). Usage: .\run_scan_parallel.ps1 [-Years 2019,2020,...]
param(
    [string[]]$Years = @('2019','2020','2021','2022','2023','2024')
)

$ErrorActionPreference = 'Stop'
$proj = 'D:\QuantLab\projects\Project_21_日内做T'
$outDir = Join-Path $proj 'results'
$py = 'D:\Python311\python.exe'
$env:PYTHONPATH = $proj
$env:PYTHONIOENCODING = 'utf-8'

$pidFile = Join-Path $outDir 'scan_pids.txt'
$running = @()

"=== launch {0} ===" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Out-File -FilePath $pidFile -Append -Encoding utf8
foreach ($y in $Years) {
    $log = Join-Path $outDir ("scan_{0}.out.log" -f $y)
    $err = Join-Path $outDir ("scan_{0}.err.log" -f $y)
    $start = "{0}0101" -f $y
    $end = "{0}1231" -f $y
    $pargs = @('-u', 'research\scanner.py', '--start', $start, '--end', $end)
    $p = Start-Process -FilePath $py -ArgumentList $pargs -WorkingDirectory $proj `
        -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden -PassThru
    $running += $p
    "{0} pid={1} log={2}" -f $y, $p.Id, $log | Out-File -FilePath $pidFile -Append -Encoding utf8
    "started {0} pid={1}" -f $y, $p.Id
    Start-Sleep -Seconds 3
}

"started {0} processes" -f $running.Count
