# Check UTF-8 BOM for all .ps1 files referenced by scheduled tasks (ASCII only)
$ErrorActionPreference = 'Continue'
$checked = 0
$noBomChinese = @()
$seen = @{}
foreach ($t in (Get-ScheduledTask)) {
    foreach ($a in $t.Actions) {
        $cmd = @($a.Execute, $a.Arguments) -join ' '
        if ($cmd -match 'powershell' -and $cmd -match '-File\s+"?([^"\s]+\.ps1)"?') {
            $path = $Matches[1]
            if ($seen.ContainsKey($path)) { continue }
            $seen[$path] = $true
            if (-not (Test-Path -LiteralPath $path)) { continue }
            $checked++
            $bytes = [System.IO.File]::ReadAllBytes($path)
            $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
            # detect non-ASCII content (Chinese etc.)
            $hasNonAscii = $false
            for ($i = if ($hasBom) {3} else {0}; $i -lt $bytes.Length; $i++) {
                if ($bytes[$i] -gt 0x7F) { $hasNonAscii = $true; break }
            }
            if ($hasNonAscii -and -not $hasBom) {
                $noBomChinese += "[NO-BOM+NON-ASCII] $path"
            }
        }
    }
}
Write-Output "Unique .ps1 files checked: $checked"
if ($noBomChinese.Count -gt 0) { $noBomChinese | ForEach-Object { Write-Output $_ } } else { Write-Output "All referenced .ps1 OK (BOM present or pure ASCII)" }
