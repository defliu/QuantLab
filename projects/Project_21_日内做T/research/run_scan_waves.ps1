# Project_21 Phase1: wave driver - keep up to 2 scanner.py processes running
# until every year 2019..2024 has produced its output parquet. ASCII-only (no BOM needed).
# Usage: Start-Process powershell -ArgumentList '-File','run_scan_waves.ps1'
param(
    [string[]]$Years = @('2019','2020','2021','2022','2023','2024'),
    [int]$MaxConcurrent = 2,
    [int]$MaxAttemptsPerYear = 3,
    [int]$MinFreeGB = 15
)

$ErrorActionPreference = 'Continue'
$proj = 'D:\QuantLab\projects\Project_21_日内做T'
$outDir = Join-Path $proj 'results'
$py = 'D:\Python311\python.exe'
$wlog = Join-Path $outDir 'scan_waves.log'
$env:PYTHONPATH = $proj
$env:PYTHONIOENCODING = 'utf-8'

function Log($msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    $line | Out-File -FilePath $wlog -Append -Encoding utf8
    Write-Host $line
}

function RunningScannerCount {
    $n = 0
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.CommandLine -like '*scanner.py*') { $n++ } }
    return $n
}

# is a scanner for this year already running? (prevents duplicate launch of the same slice)
function YearRunning($y) {
    $found = $false
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object {
            if ($_.CommandLine -like '*scanner.py*' -and $_.CommandLine -like ('*--start {0}0101*' -f $y)) { $found = $true }
        }
    return $found
}

function FreeGB {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round($os.FreePhysicalMemory / 1MB, 2)
}

function OutPath($y) { Join-Path $outDir ("signal_scan_{0}0101_{0}1231.parquet" -f $y) }

$attempts = @{}
foreach ($y in $Years) { $attempts[$y] = 0 }

Log ("waves start: years=[{0}] max_concurrent={1}" -f ($Years -join ','), $MaxConcurrent)

$done = $false
while (-not $done) {
    $pending = @()
    foreach ($y in $Years) {
        if (Test-Path (OutPath $y)) { Log ("{0}: output exists -> skip" -f $y) }
        else { $pending += $y }
    }
    if ($pending.Count -eq 0) { $done = $true; break }

    $running = RunningScannerCount
    $free = FreeGB
    $slot = $MaxConcurrent - $running
    # each scanner peaks ~6GB (reference cache + one batch), so require headroom
    # of 7GB per slot before launching anything
    $needGB = ($slot * 7) + 1
    if ($slot -gt 0 -and $free -ge $needGB) {
        $launched = 0
        foreach ($y in $pending) {
            if ($launched -ge $slot) { break }
            if (YearRunning $y) { Log ("{0}: already running -> not relaunching" -f $y); continue }
            if ($attempts[$y] -ge $MaxAttemptsPerYear) {
                Log ("{0}: attempts exhausted ({1}) -> give up" -f $y, $attempts[$y])
                continue
            }
            $start = "{0}0101" -f $y
            $end = "{0}1231" -f $y
            $pargs = @('-u', 'research\scanner.py', '--start', $start, '--end', $end)
            $log = Join-Path $outDir ("scan_{0}.out.log" -f $y)
            $err = Join-Path $outDir ("scan_{0}.err.log" -f $y)
            $p = Start-Process -FilePath $py -ArgumentList $pargs -WorkingDirectory $proj `
                -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden -PassThru
            $attempts[$y] = [int]$attempts[$y] + 1
            Log ("{0}: launched pid={1} attempt={2} free={3}GB" -f $y, $p.Id, $attempts[$y], $free)
            $launched++
            Start-Sleep -Seconds 5
        }
    }

    # reaped check: any year whose process is gone, no output, not retried yet
    $running = RunningScannerCount
    if ($running -eq 0) {
        foreach ($y in $pending) {
            if (-not (Test-Path (OutPath $y)) -and $attempts[$y] -gt 0) {
                Log ("{0}: no output after attempt {1} -> retry" -f $y, $attempts[$y])
            }
        }
    }

    $missing = @()
    foreach ($y in $Years) { if (-not (Test-Path (OutPath $y))) { $missing += $y } }
    if ($missing.Count -eq 0) { $done = $true; break }

    # stop when every missing year has burned all attempts
    $stuck = $true
    foreach ($y in $missing) { if ($attempts[$y] -lt $MaxAttemptsPerYear) { $stuck = $false } }
    if ($stuck -and $running -eq 0) { Log ("stuck: missing=[{0}] and no attempts left" -f ($missing -join ',')); $done = $true; break }

    Start-Sleep -Seconds 30
}

Log "waves end"
