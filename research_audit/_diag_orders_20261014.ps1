$ErrorActionPreference = 'Continue'
function Hex16($path) {
  try {
    $b = [System.IO.File]::ReadAllBytes($path)
    $n = [Math]::Min(16, $b.Length)
    $hex = ($b[0..($n-1)] | ForEach-Object { $_.ToString('X2') }) -join ' '
    return $hex
  } catch { return 'ERR: ' + $_.Exception.Message }
}
function Info($label, $path) {
  try {
    $f = Get-Item $path
    $out = $label + ' | LastWriteTime=' + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss.fff') + ' | Size=' + $f.Length + ' | First16=' + (Hex16 $f.FullName)
    Write-Output $out
  } catch {
    Write-Output ($label + ' | MISSING: ' + $_.Exception.Message)
  }
}

Write-Output '===== G2 BRIDGE CMD ====='
Info 'G2 orders_20261014.json (LOST)' 'D:\QMT_POOL\g2_bridge\cmd\orders_20261014.json'
Info 'G2 orders_20261012_done_1.json (OK-archive)' 'D:\QMT_POOL\g2_bridge\cmd\orders_20261012_done_1.json'
Info 'G2 orders_20260929_done_1.json (OK-archive)' 'D:\QMT_POOL\g2_bridge\cmd\orders_20260929_done_1.json'

Write-Output ''
Write-Output '===== ENS BRIDGE CMD ====='
Info 'ENS orders_20261014.json (LOST)' 'D:\QMT_POOL\p16_ensemble_bridge\cmd\orders_20261014.json'
Info 'ENS orders_20261013_done_1.json (OK-archive)' 'D:\QMT_POOL\p16_ensemble_bridge\cmd\orders_20261013_done_1.json'
Info 'ENS orders_20261012_done_1.json (OK-archive)' 'D:\QMT_POOL\p16_ensemble_bridge\cmd\orders_20261012_done_1.json'

Write-Output ''
Write-Output '===== TMP RESIDUE SCAN ====='
Get-ChildItem 'D:\QMT_POOL\g2_bridge' -Recurse -Filter '*.tmp' -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ('G2-TMP: ' + $_.FullName + ' ' + $_.LastWriteTime) }
Get-ChildItem 'D:\QMT_POOL\p16_ensemble_bridge' -Recurse -Filter '*.tmp' -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ('ENS-TMP: ' + $_.FullName + ' ' + $_.LastWriteTime) }
Write-Output 'tmp scan done'

Write-Output ''
Write-Output '===== STATE DIRS ====='
Get-ChildItem 'D:\QMT_POOL\g2_bridge\state' -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Write-Output ('G2-state: ' + $_.Name + ' | ' + $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + ' | ' + $_.Length) }
Get-ChildItem 'D:\QMT_POOL\p16_ensemble_bridge\state' -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Write-Output ('ENS-state: ' + $_.Name + ' | ' + $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + ' | ' + $_.Length) }
