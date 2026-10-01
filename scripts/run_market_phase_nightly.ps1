# coding: utf-8
# 每日 20:30 市场情绪周期 6 阶段标签（T-20260927-002 A 方案落地）
# 流程: astock 主库优先 + Tushare 在线补缺(常态主路径) → D:/QMT_POOL/market_phase.csv + 飞书日报卡片
# 非交易日脚本内部自跳过(exit 0)；推送失败 exit 2（标签已落盘，仅通知降级）
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$log = "D:/QuantLab/research_study/market_phase_backfill/daily_run.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== market_phase 夜盘开始 ====" -Encoding UTF8
& $py "D:/QuantLab/scripts/daily_market_phase.py" *>> $log
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] daily_market_phase exit=$rc" -Encoding UTF8
exit $rc
