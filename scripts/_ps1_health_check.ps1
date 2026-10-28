# ASCII-only helper: verify UTF-8 BOM + PowerShell 5.1 parse for the QMT nightly scripts.
# Chinese paths are discovered at runtime so this file itself stays pure ASCII.
$ErrorActionPreference = "Stop"
$proj = Get-ChildItem -Path "D:\QuantLab\projects" -Directory |
    Where-Object { $_.Name -like "Project_16_*" } | Select-Object -First 1
$files = @(
    (Join-Path $proj.FullName "qmt_nightly_retry.ps1"),
    (Join-Path $proj.FullName "qmt_nightly_deploy.ps1"),
    "D:\QuantLab\scripts\qmt_watch_login.ps1",
    "D:\QuantLab\scripts\fix_qmt_nightly_retry_trigger.ps1"
)
$bad = 0
foreach ($f in $files) {
    if (-not (Test-Path -LiteralPath $f)) { Write-Host "MISSING $f"; $bad++; continue }
    $bytes = [System.IO.File]::ReadAllBytes($f)
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    # BOM is only mandatory for files containing non-ASCII (Chinese) text; ASCII-only
    # helpers are fine without it, so do not flag them.
    $hasNonAscii = $false
    foreach ($b in $bytes) { if ($b -gt 127) { $hasNonAscii = $true; break } }
    $tok = $null; $err = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$tok, [ref]$err)
    $n = 0
    if ($err) { $n = @($err).Count }
    $lines = @([System.IO.File]::ReadAllLines($f, [System.Text.Encoding]::UTF8)).Count
    Write-Host ("{0}  BOM={1}  nonAscii={2}  tokens={3}  parseErrors={4}  lines={5}" -f `
        (Split-Path $f -Leaf), $bom, $hasNonAscii, @($tok).Count, $n, $lines)
    if (-not $bom -and $hasNonAscii) { Write-Host "   !! BOM MISSING (file has Chinese)"; $bad++ }
    if ($n -gt 0) {
        $bad++
        foreach ($e in $err) { Write-Host ("   !! L{0}: {1}" -f $e.Extent.StartLineNumber, $e.Message) }
    }
}
Write-Host ("RESULT bad={0}" -f $bad)
exit $bad
