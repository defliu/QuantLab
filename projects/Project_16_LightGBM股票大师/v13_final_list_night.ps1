# coding: utf-8
# Nightly 20:20: build_v13_final.py -> data/selections/<T>_final_list.csv + .meta.json
# V1.3 plan-B paper phase (T-20260929-006, SPEC 20260930): prob Top10 pool -> scorecard_real
# -> redline 58 -> total desc Top5. PAPER phase: no rebalance, no orders, production untouched.
# Non-trading day -> [SKIP] exit 0. Stale data / empty pool / F2 all-NaN -> exit 1 (fail-loud).
# Log encoding: python pinned to UTF-8 (PYTHONIOENCODING), cmd.exe appends RAW bytes (no
# PS 5.1 transcoding -- *>> writes UTF-16LE and corrupts mixed logs), Add-Content is UTF-8.
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/v13_final_list.log"
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== V13 final_list (nightly 20:20) start ====" -Encoding UTF8
& cmd.exe /c "`"$py`" `"$dir\build_v13_final.py`" 1>>`"$log`" 2>&1"
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] build_v13_final exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== V13 final_list end ====" -Encoding UTF8
exit $rc
