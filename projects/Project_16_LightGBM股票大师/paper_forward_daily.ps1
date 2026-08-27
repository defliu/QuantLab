# paper_forward_daily.ps1 - g2 daily pipeline (independent of V1.1 pipeline)
# Runs Mon-Fri 16:45 by scheduled task 'paper_forward_daily'
# Steps: 1) build_g2_daily.py  (g2 43-feature snapshot; F5 当日行业涨幅由增量库自算)
#        2) deploy_predict_g2.py (模型Top100 -> F2 新浪当日主力净额实时覆盖 -> 真实评分卡红线60 -> top2)
# 2026-08-25: g2_realtime.py 接入，F2/F5 当日实时化（V1.1 资产未触碰）
$ErrorActionPreference = "Stop"
$proj = $PSScriptRoot
$py = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $proj "data\real\g2_pipeline_daily.log"
try {
    Push-Location $proj
    & $py -u build_g2_daily.py *>> $log
    & $py -u deploy_predict_g2.py *>> $log
    Pop-Location
    Add-Content -Path $log -Value "[$stamp] OK"
    exit 0
} catch {
    Add-Content -Path $log -Value "[$stamp] FAILED: $_"
    exit 1
}
