# 融合对账执行脚本（UTF-8 BOM）
$ErrorActionPreference = "Stop"
Set-Location "D:\QuantLab\projects\Project_16_LightGBM股票大师"
& "D:\Python311\python.exe" reconcile_ens.py --date 20261001
exit $LASTEXITCODE