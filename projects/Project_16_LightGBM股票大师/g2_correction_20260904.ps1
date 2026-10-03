# coding: utf-8
# 一次性 G2 纠错执行包装（2026-09-04 09:43 自动执行，实时行情卖错票→买正确票）
# [已废弃 2026-10-02] 本包装脚本已于 2026-10-02 加门禁停用，实测未注册 schtasks、无现行调用方。
# 危险点：它以 --live 调用 g2_correction_exec.py，而该脚本按 --top 2 找 _g2_top2.csv
#        （现行 SELECT_TOP=10），且 _known_volumes() 会回退硬编码股数，误跑会真实写大 QMT 桥。
# 现行换仓入口：rebalance_g2.py / rebalance_ens.py（TRAE 任务 30344e79 / 051bc2d9）。
# 仅在复现 T-20260903-007 事故证据时，先设 $env:G2_CORRECTION_EXEC_ALLOW = "1" 再运行本脚本。
if ($env:G2_CORRECTION_EXEC_ALLOW -ne "1") {
    Write-Output "[ABORT-fail-closed] g2_correction_20260904.ps1 已废弃停用（2026-10-02）。"
    Write-Output "  现行换仓入口：rebalance_g2.py / rebalance_ens.py（TRAE 任务 30344e79 / 051bc2d9）。"
    Write-Output "  仅在复现 T-20260903-007 事故证据时，先设 `$env:G2_CORRECTION_EXEC_ALLOW='1'`。"
    exit 2
}

& "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe" "D:/QuantLab/projects/Project_16_LightGBM股票大师/g2_correction_exec.py" --live *> "D:/QuantLab/projects/Project_16_LightGBM股票大师/data/schedules/g2_correction_20260904.log"
exit $LASTEXITCODE
