# coding: utf-8
# DE 报告流水线（T-20260903-023）：TRAE 端协调下指令 → DE(GLM5.3) 生成报告 → 飞书推送
# 用法：powershell.exe -File de_report_daily.ps1 -Task close|midday
#   close  = 盘后持仓复盘（15:40 后，当日三策略执行痕迹+行情 → 复盘报告 + 风险核对）
#   midday = 午休持仓报告（11:35 后，半日持仓/盘面 → 简报告）
# 流程：is_trade_day 校验 → 生成 UTF-8 prompt → deveco run 生成报告到 data/schedules/de_report_<task>_<date>.md
#       → 飞书推送报告路径/摘要。
param([string]$Task = "close")
$Task = $Task.ToLower()
if ($Task -notin @("close", "midday")) {
    Write-Error "Task 必须为 close/midday"
    exit 3
}
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sched = "D:\QuantLab\data\schedules"
New-Item -ItemType Directory -Force -Path $sched | Out-Null

$date = Get-Date -Format "yyyyMMdd"
$tag = "de_report_" + $Task + "_" + $date
$log = Join-Path $sched ($tag + ".log")
$promptFile = Join-Path $sched ($tag + ".prompt.txt")
$outFile = Join-Path $sched ($tag + ".md")
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd"

& $py "$proj\is_trade_day.py" *>> $log
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 非交易日($date)，跳过 $Task DE 报告" -Encoding UTF8
    exit 0
}

if ($Task -eq "midday") {
    $prompt = @"
你是 Project_16 午休持仓报告助手（DE 生成，只读，绝不下单）。工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\data\（qmt_trade_log.csv、selections\、rebalance_g2\、reconcile_g2\、real\）、D:/QMT_POOL/g2_bridge/state/、p16_ensemble_bridge/state/、p16_v13_risk/state/、D:/QMT_POOL/g2_bridge/cmd/、p16_ensemble_bridge/cmd/、p16_v13_risk/cmd/ 的 json/csv。
任务：汇总当日（$date）截至午休（11:30）的三策略（V1.3 账号67014907 / G2 与融合 账号70180771）持仓报告，输出：
①**逐票持仓明细表**（每只持仓一行，必含）：代码、名称、所属策略、持仓股数、成本价（含费）、现价（用腾讯行情 qt.gtimg.cn 实时取价，格式 q=sh601058/q=sz300475，cost 从 positions_cfg 读）、当日涨跌幅%、持仓市值（现价×股数）、浮盈/浮亏金额（(现价-成本)×股数）与浮盈/浮亏百分比、距止损线（成本×0.93）与止盈线（成本×1.15）的百分比缓冲；
②当日已成交动作（V1.3 qmt_trade_log / G2与融合 fills）买卖明细；
③桥健康（三桥心跳时间/build_tag/pending/peak）；
④午后关注点：浮亏最大、最接近止损/止盈线、当日跌幅最大的持仓优先提示。
数据只读当日与最近历史；腾讯取价失败则该票标"取价失败"并说明。
输出：写报告到 $outFile（markdown，含完整逐票明细表格与结论）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
} else {
    $prompt = @"
你是 Project_16 盘后持仓复盘助手（DE 生成，只读，绝不下单）。工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\data\（qmt_trade_log.csv、selections\、rebalance_g2\、reconcile_g2\、reconcile\、real\）、D:/QMT_POOL/g2_bridge/state/、p16_ensemble_bridge/state/、p16_v13_risk/state/、D:/QMT_POOL/g2_bridge/cmd/、p16_ensemble_bridge/cmd/、p16_v13_risk/cmd/、data\schedules\de_g2_compare_$date.md（若有）的 json/csv。
任务：汇总当日（$date）收盘后三策略（V1.3 账号67014907 / G2 与融合 账号70180771）持仓复盘，输出：
①**逐票持仓明细表**（每只持仓一行，必含）：代码、名称、所属策略、持仓股数、成本价（含费，positions_cfg 读）、当日收盘价（腾讯行情 qt.gtimg.cn 实时取价，格式 q=sh601058/q=sz300475；失败标"取价失败"）、当日涨跌幅%、当日开盘/最高/最低、持仓市值、浮盈/浮亏金额（(现价-成本)×股数）与百分比、距止损线（成本×0.93）与止盈线（成本×1.15）的百分比缓冲、持有起始日与已持交易日/满期剩余日；
②**已实现盈亏明细**：当日卖出成交（G2/融合 fills、V1.3 qmt_trade_log）每笔：代码/股数/卖出价/估算盈亏；以及三策略资金池当前值（g2/ens/v13 的 strategy_capital）；
③当日各策略动作汇总（换仓/对账结果/持有期）；
④桥健康（三桥心跳时间/build_tag/pending/peak、异常标注）；
⑤风控体检：逐票列"距止损/止盈线最近"风险排行（缓冲<3% 标 🔴）；
⑥明日关注点（候选 top 变化、数据新鲜度、临近到期的持仓）。
数据只读当日与最近历史。
输出：写报告到 $outFile（markdown，含完整逐票明细表格、盈亏排行、风险分级）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
}
[System.IO.File]::WriteAllText($promptFile, $prompt, (New-Object System.Text.UTF8Encoding($false)))

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 报告 $Task 开始 ($date) ====" -Encoding UTF8
# 幂等（T-20260903-023）：当日报告已生成 → 不重跑 DE，直接推送既有报告（TRAE/Windows 双触发保护）
if ((Test-Path $outFile) -and ((Get-Item $outFile).LastWriteTime.Date -eq (Get-Date).Date)) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 当日报告已存在，跳过 DE 重跑: $outFile" -Encoding UTF8
    $rc = 0
} else {
$msg = "Read the file $promptFile and strictly follow every instruction in it. This is a READ-ONLY report generation task. Do NOT place orders, do NOT use --live, do NOT modify any files, do NOT commit. Write your report to the output path specified in the file."
& $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $log
$rc = $LASTEXITCODE
}
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] deveco exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 报告 $Task 结束 ====" -Encoding UTF8

$pushMsg = ""
if (Test-Path $outFile) {
    $pushMsg = (& $py "$proj\extract_de_report_push.py" "$outFile" 2>&1 | Out-String).Trim()
}
if ([string]::IsNullOrWhiteSpace($pushMsg) -or $pushMsg -match "FAIL_NO_ROWS") { $pushMsg = "【DE报告 $Task $date】exit=$rc，报告: $outFile（持仓数据提取失败，详见文件）" }
$oPush = (& $py -c "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])" "$pushMsg" 2>&1 | Out-String)
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送: $pushMsg" -Encoding UTF8
exit $rc
