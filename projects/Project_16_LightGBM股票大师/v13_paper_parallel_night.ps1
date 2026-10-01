# coding: utf-8
# Nightly 20:30: v13_paper_parallel.py -> results/v13_paper_parallel_log.csv + report.md
# V1.3 plan-B paper phase (T-20260929-006, SPEC 20260930): dual-arm forward comparison.
# old arm = <T>_selection_full.csv (09:45 rerank, total>=58 top5)
# new arm = <D>_final_list.csv (D = max final_list date < T, redline top5)
# Both arms enter at exec-day T open, exit at 5th trading day open. No position_filter.
# exit 0 normal (incl. [SKIP] record on non-trading days); exit 1 fail-loud;
# exit 2 = data source stale (>5 calendar days) -- intentional observable alert.
# Log encoding: python pinned to UTF-8 (PYTHONIOENCODING), cmd.exe appends RAW bytes (no
# PS 5.1 transcoding -- *>> writes UTF-16LE and corrupts mixed logs), Add-Content is UTF-8.
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/v13_paper_parallel.log"
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== V13 paper parallel (nightly 20:30) start ====" -Encoding UTF8
& cmd.exe /c "`"$py`" `"$dir\v13_paper_parallel.py`" 1>>`"$log`" 2>&1"
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] v13_paper_parallel exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== V13 paper parallel end ====" -Encoding UTF8
exit $rc
