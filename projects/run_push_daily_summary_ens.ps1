# 融合盘后总览卡推送（UTF-8 BOM）
$ErrorActionPreference = "Stop"
Set-Location "D:\QuantLab\projects\Project_16_LightGBM股票大师"
& "D:\Python311\python.exe" push_daily_summary_card.py --date 20261001 --summary "融合对账:无差额|无孤儿"
exit $LASTEXITCODE