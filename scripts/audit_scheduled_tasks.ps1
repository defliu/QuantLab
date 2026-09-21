# Audit all scheduled tasks for mojibake and missing -File targets (ASCII only)
$ErrorActionPreference = 'Continue'
$out = @()
$tasks = Get-ScheduledTask
$mojibakeCount = 0
$missingCount = 0
$okCount = 0

foreach ($t in $tasks) {
    $n = 0
    foreach ($a in $t.Actions) {
        $n++
        $parts = @()
        if ($a.Execute) { $parts += $a.Execute }
        if ($a.Arguments) { $parts += $a.Arguments }
        if ($a.WorkingDirectory) { $parts += $a.WorkingDirectory }
        $cmd = $parts -join ' '

        # mojibake detection: chars in U+0080..U+00FF (Latin-1 remnant of UTF-8 misread)
        $susp = $false
        foreach ($ch in $cmd.ToCharArray()) {
            $c = [int]$ch
            if ($c -ge 0x80 -and $c -le 0xFF) { $susp = $true; break }
        }
        if ($susp) {
            $mojibakeCount++
            $codes = ($cmd.ToCharArray() | ForEach-Object { 'U+{0:X4}' -f [int]$_ }) -join ' '
            $out += "[MOJIBAKE] $($t.TaskName) action#$n"
            $out += "  CMD: $cmd"
            continue
        }

        # -File target existence check (powershell tasks)
        if ($cmd -match 'powershell' -and $cmd -match '-File\s+"?([^"\s]+)"?') {
            $path = $Matches[1]
            if (-not (Test-Path -LiteralPath $path)) {
                $missingCount++
                $out += "[MISSING-FILE] $($t.TaskName) action#$n => $path"
            } else {
                $okCount++
            }
        }
    }
}

$out += "---"
$out += "Tasks scanned: $($tasks.Count); mojibake: $mojibakeCount; missing-file: $missingCount; file-ok: $okCount"
$out | ForEach-Object { Write-Output $_ }
