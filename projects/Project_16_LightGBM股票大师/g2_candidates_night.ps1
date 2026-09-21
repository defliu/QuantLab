# coding: utf-8
# 每日 20:05 预生成次日 G2 候选（T-1 数据已就绪：16:30 面板增量 + 19:30 Tushare moneyflow 刷新）
# 1) build_g2_daily：用最新 moneyflow 重建 g2 特征快照（16:47 快照的 mf_* 是刷新前旧值，必须重建）
# 2) deploy_predict_g2 --top 10 + --top 3：生成次日候选（rebalance 读 top10，09:25 防御性重算会兜底）
# 3) top10 调用带 --ab --ensemble --ens-live（T-20260910-005 + 2026-09-14 P2 修复）：
#    纸面三臂每日累积（G2 live / v3_enh / 融合），AB/融合臂记录 Top10 全集，前向统计按 rank<=3
#    筛实盘口径，30 笔目标更快达成。此前缺失该开关：v3_enh/融合臂自 09-08 手动跑后未再累积新信号，纸面前向验证停滞。
#    --ens-live（2026-09-14 P2 补传）：融合实盘候选落盘 data/selections/g2_ens/<date>_g2_top10.csv
#    （rebalance_ens 消费）；此前夜间只出纸面融合臂，融合实盘候选无夜间兜底，09:25 TRAE 失效日即断链。
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/g2_candidates_night.log"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$ts] ==== G2 候选预生成（夜间）开始 ====" -Encoding UTF8
& $py "$dir/build_g2_daily.py" *>> $log
$rc1 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] build_g2_daily exit=$rc1" -Encoding UTF8
& $py "$dir/deploy_predict_g2.py" --threshold 60 --top 10 --pool 100 --ab --ensemble --ens-live *>> $log
$rc2 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] deploy top10 (+AB+ENS+ENSLIVE) exit=$rc2" -Encoding UTF8
& $py "$dir/deploy_predict_g2.py" --threshold 60 --top 3 --pool 100 *>> $log
$rc3 = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] deploy top3 exit=$rc3" -Encoding UTF8

# ---- 次日候选副本（2026-09-14 修复）----
# 根因：deploy_predict_g2 无 --date 时按快照最新日（=运行日）命名候选文件，20:05 夜间产物实际是
# "次日候选"但文件名是运行日 → 次日早晨 rebalance 新鲜度闸（候选数据日 vs 增量库最新日）必拒，
# 周五→周一断链尤甚（20260914 G2/融合双双 NOOP 半仓即此因，当日仅靠 14:34 人工干预补买）。
# 修复：按 is_trade_day.py 日历推下一交易日，复制当日产物并改写 trade_date 列为下交易日，
# 生成 <下交易日>_g2_top{10,3}.csv（周五 → 周一可用）；09:25 TRAE 防御性重算仍会覆盖刷新，互不冲突。
$runDay = Get-Date -Format "yyyyMMdd"
# ---- 快照留档（2026-09-20 立，T-20260919-002 每日复跑对比机制）：deploy 全部跑完后固化当日快照 ----
# 盘后复跑对比（data/schedules/compare_g2_daily.py，20:10 调度）用它保证可复现；
# 次日 build_g2_daily 会重建并覆盖 g2_latest_features.parquet，不提前留档则复跑时已无当日输入可对比。
$snapFail = 0
$snapSrc = "$dir\data_live\g2_latest_features.parquet"
$snapDst = "$dir\data_live\snapshot_g2_${runDay}.parquet"
if (Test-Path $snapSrc) {
    Copy-Item -LiteralPath $snapSrc -Destination $snapDst -Force
    if ($LASTEXITCODE -ne 0) { $snapFail = 1 }
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 快照留档 g2_latest_features.parquet -> snapshot_g2_${runDay}.parquet exit=$LASTEXITCODE" -Encoding UTF8
} else {
    $snapFail = 1
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] [WARN] g2_latest_features.parquet 不存在，快照留档跳过（复跑对比将降级为健康校验）" -Encoding UTF8
}
# ---- V1.3 快照留档（2026-09-20 立，T-20260920-001 扩展）：latest_features.parquet 为 16:30 merge 产物，夜间脚本不改动 ----
# compare_v13_daily.py（20:12 调度）用它重放 deploy_predict.py --model v3 对比当日 V1.3 候选（data/selections/<runDay>_model_top10.csv）
$snapFail13 = 0
$snapSrc13 = "$dir\data_live\latest_features.parquet"
$snapDst13 = "$dir\data_live\snapshot_v13_${runDay}.parquet"
if (Test-Path $snapSrc13) {
    Copy-Item -LiteralPath $snapSrc13 -Destination $snapDst13 -Force
    if ($LASTEXITCODE -ne 0) { $snapFail13 = 1 }
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 快照留档 v13 latest_features.parquet -> snapshot_v13_${runDay}.parquet exit=$LASTEXITCODE" -Encoding UTF8
} else {
    $snapFail13 = 1
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] [WARN] latest_features.parquet 不存在，V1.3 快照留档跳过（复跑对比将降级为健康校验）" -Encoding UTF8
}
$nextDay = $null
$probe = (Get-Date).Date
for ($i = 0; $i -lt 10; $i++) {
    $probe = $probe.AddDays(1)
    & $py "$dir\is_trade_day.py" $probe.ToString("yyyy-MM-dd") *> $null
    if ($LASTEXITCODE -eq 0) { $nextDay = $probe.ToString("yyyyMMdd"); break }
}
$copyFail = 0
if ($null -ne $nextDay -and $nextDay -ne $runDay) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 下一交易日=$nextDay（运行日=$runDay），生成次日候选副本" -Encoding UTF8
    foreach ($n in @("top10", "top3")) {
        $src = "$dir\data\selections\g2\${runDay}_g2_${n}.csv"
        $dst = "$dir\data\selections\g2\${nextDay}_g2_${n}.csv"
        if (Test-Path $src) {
            & $py -c "import pandas as pd,sys; df=pd.read_csv(sys.argv[1],encoding='utf-8-sig'); df['trade_date']=sys.argv[3]; df.to_csv(sys.argv[2],index=False,encoding='utf-8-sig')" $src $dst $nextDay *>> $log
            if ($LASTEXITCODE -ne 0) { $copyFail = 1 }
            Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 次日副本 ${runDay}_g2_${n}.csv -> ${nextDay}_g2_${n}.csv exit=$LASTEXITCODE" -Encoding UTF8
        } else {
            Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] [INFO] 当日 ${runDay}_g2_${n}.csv 不存在（红线无候选属设计内宁缺毋滥），跳过次日副本" -Encoding UTF8
        }
    }
    $srcMd = "$dir\data\selections\g2\${runDay}_g2_selection.md"
    $dstMd = "$dir\data\selections\g2\${nextDay}_g2_selection.md"
    if (Test-Path $srcMd) { Copy-Item -LiteralPath $srcMd -Destination $dstMd -Force }
    # ---- 融合（ens）次日候选副本（2026-09-14 P2，与 G2 副本同因）----
    # --ens-live 产物名 = 运行日，与 G2 同样存在"次日 freshness 闸拒收"断链；不复制则本修复只对
    # G2 生效、融合腿次日仍断（09-25 TRAE 失效日融合会重演 09-14 的 NOOP）。列结构含 trade_date，改写方式同上。
    $srcEns = "$dir\data\selections\g2_ens\${runDay}_g2_top10.csv"
    $dstEns = "$dir\data\selections\g2_ens\${nextDay}_g2_top10.csv"
    if (Test-Path $srcEns) {
        & $py -c "import pandas as pd,sys; df=pd.read_csv(sys.argv[1],encoding='utf-8-sig'); df['trade_date']=sys.argv[3]; df.to_csv(sys.argv[2],index=False,encoding='utf-8-sig')" $srcEns $dstEns $nextDay *>> $log
        if ($LASTEXITCODE -ne 0) { $copyFail = 1 }
        Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 次日副本(ens) ${runDay}_g2_top10.csv -> ${nextDay}_g2_top10.csv exit=$LASTEXITCODE" -Encoding UTF8
    } else {
        Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] [INFO] 当日 ens ${runDay}_g2_top10.csv 不存在（红线无融合候选属设计内宁缺毋滥），跳过 ens 次日副本" -Encoding UTF8
    }
} else {
    $copyFail = 1
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] [WARN] 下一交易日推算失败（is_trade_day.py 连续 10 日无非交易日判定？），次日候选副本未生成" -Encoding UTF8
}
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== G2 候选预生成（夜间）结束 ====" -Encoding UTF8
$total = $rc1 + $rc2 + $rc3 + $copyFail + $snapFail + $snapFail13
if ($total -gt 0) { exit 1 } else { exit 0 }
