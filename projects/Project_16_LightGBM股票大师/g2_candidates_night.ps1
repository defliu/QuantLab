# coding: utf-8
# 每日 20:05 预生成次日 G2 候选（T-1 数据已就绪：16:30 面板增量 + 19:30 Tushare moneyflow 刷新）
# 1) build_g2_daily：用最新 moneyflow 重建 g2 特征快照（16:47 快照的 mf_* 是刷新前旧值，必须重建）
# 2) deploy_predict_g2 --top 10 + --top 2：生成次日候选（rebalance 读 top10，09:25 防御性重算会兜底）
# 3) top10 调用带 --ab --ensemble（T-20260910-005）：纸面三臂每日累积（G2 live / v3_enh / 融合），
#    AB/融合臂记录 Top10 全集，前向统计按 rank<=2 筛实盘口径，30 笔目标更快达成。
#    此前缺失该开关：v3_enh/融合臂自 09-08 手动跑后未再累积新信号，纸面前向验证停滞。
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/g2_candidates_night.log"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== G2 候选预生成（夜间）开始 ====" -Encoding UTF8
& $py "$dir/build_g2_daily.py" *>> $log
$rc1 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] build_g2_daily exit=$rc1" -Encoding UTF8
& $py "$dir/deploy_predict_g2.py" --threshold 60 --top 10 --pool 100 --ab --ensemble *>> $log
$rc2 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] deploy top10 (+AB+ENS) exit=$rc2" -Encoding UTF8
& $py "$dir/deploy_predict_g2.py" --threshold 60 --top 2 --pool 100 *>> $log
$rc3 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] deploy top2 exit=$rc3" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== G2 候选预生成（夜间）结束 ====" -Encoding UTF8
$total = $rc1 + $rc2 + $rc3
if ($total -gt 0) { exit 1 } else { exit 0 }
