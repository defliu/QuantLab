# paper_forward_daily.ps1 - g2 daily pipeline (independent of V1.1 pipeline)
# Runs Mon-Fri 16:45 by scheduled task 'paper_forward_daily'
# Steps: 1) build_g2_daily.py  (g2 43-feature snapshot; F5 当日行业涨幅由增量库自算)
#        2) deploy_predict_g2.py --ensemble --ens-live (模型Top100 -> F2 实时覆盖 -> 红线60 -> top10候选池；
#            --ensemble --ens-live 顺带产出融合实盘选股 selections/g2_ens/<date>_g2_top10.csv 供次日 rebalance_ens 消费)
# 2026-09-10: deploy_predict_g2 增加 --ensemble --ens-live（融合 live 管道：v3_enh+G2 rank融合 w=0.5 红线60）。
#        3) paper_forward.py --backfill (回填 live 候选未来 N=10 交易日 open→open 收益，审计 P1-1)
#        4) forward_stats.py    (超额统计报告 data/real/forward_stats_<date>.md)
#        5) paper_forward_exit.py --top 2 --hold 10 (出场规则前向验证：对 rank<=2 实盘口径候选逐笔模拟
#           none/fixed/live_trail/atr20 等规则，累积 N>=30 + 逐笔配对 t>2 判定是否值得改实盘出场参数；
#           产物 data/real/paper_forward_exit_live.csv + paper_forward_exit_<date>.md)
# 2026-08-25: g2_realtime.py 接入，F2/F5 当日实时化（V1.1 资产未触碰）
# 2026-09-01: 新增 --backfill 步骤（此前计划任务缺失，8/31 起管道断更，已重建 paper_forward_daily 16:45）
# 2026-09-04: ①backfill 数据源改「主库+data_live增量」合并（此前仅主库，主库停更8/28导致连续4天回填0笔
#             却仍 exit 0，无人察觉）；②backfill 停更时 exit 2，此处显式捕获并 exit 2 让计划任务
#             LastTaskResult 非 0（fail-loud）；③新增 forward_stats.py 输出超额统计到日志。
# 2026-09-07: 新增步骤5 paper_forward_exit.py 出场规则前向验证（ATR2.0 挂观察，exit_ablation 的延续）。
# 2026-09-13: 新增步骤6 paper_forward_downgrade.py 纸面自动降级闸（T-20260913-001 P1-8）：
#             任一策略纸面臂样本外持续为负 → 自动写冻结标记 + 飞书告警，次日 rebalance 只卖不买。
$ErrorActionPreference = "Stop"
$proj = $PSScriptRoot
$py = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $proj "data\real\g2_pipeline_daily.log"
try {
    Push-Location $proj
    & $py -u build_g2_daily.py *>> $log
    & $py -u deploy_predict_g2.py --ensemble --ens-live *>> $log
    & $py -u paper_forward.py --backfill --hold 10 *>> $log
    $bfExit = $LASTEXITCODE
    & $py -u forward_stats.py --hold 10 *>> $log
    & $py -u paper_forward_exit.py --top 2 --hold 10 *>> $log
    $pfExit = $LASTEXITCODE
    & $py -u paper_forward_downgrade.py *>> $log
    $dgExit = $LASTEXITCODE
    # 三臂纸面季评（2026-09-13 立，P1-1）：每日随管道跑（样本不足自动标"样本不足"不排名），
    # 连续两季最末才提议退役/降配；只提议不动资金分配。失败记 ALERT 不阻断主链（季评是观察件）。
    & $py -u paper_quarterly_review.py *>> $log
    $qrExit = $LASTEXITCODE
    Pop-Location
    if ($bfExit -eq 2) {
        Add-Content -Path $log -Value "[$stamp] BACKFILL-ALERT 行情数据源停更，无新样本产出 (backfill exit=2)"
        exit 2
    }
    if ($bfExit -ne 0) {
        Add-Content -Path $log -Value "[$stamp] BACKFILL-ALERT backfill 异常退出 (exit=$bfExit)"
        exit 2
    }
    if ($pfExit -ne 0) {
        Add-Content -Path $log -Value "[$stamp] EXIT-RULE-ALERT paper_forward_exit 异常退出 (exit=$pfExit)"
        exit 2
    }
    if ($dgExit -ne 0) {
        Add-Content -Path $log -Value "[$stamp] DOWNGRADE-ALERT paper_forward_downgrade 异常退出 (exit=$dgExit)"
        exit 2
    }
    if ($qrExit -ne 0) {
        Add-Content -Path $log -Value "[$stamp] QUARTERLY-ALERT paper_quarterly_review 异常退出 (exit=$qrExit) ——季评失败不阻断主链，人工核查"
    }
    Add-Content -Path $log -Value "[$stamp] OK"
    exit 0
} catch {
    Add-Content -Path $log -Value "[$stamp] FAILED: $_"
    exit 1
}
