# coding: utf-8
# DE 兜底：三策略（V1.3 / G2 / 融合）换仓独立核验（只读 + dry-run，绝不下单）——T-20260903-023
# 用法：powershell.exe -File de_rebalance_guard_daily.ps1 -Strategy v13|ens|g2
# 时间：各策略 TRAE 换仓之后（V13 09:45 / G2 09:50 / ENS 09:52）错峰运行
# 流程：is_trade_day 校验 → 生成 UTF-8 prompt → deveco run（GLM5.3）dry-run 重算+规则核验+对比当日执行
#       → 写 de_<strategy>_compare_<date>.md → 飞书推送报告路径/风险标记
# 安全：prompt 写 UTF-8 文件（DE 用 read 读，规避 5.1->cmd 中文编码问题）；消息只传 ASCII 路径；
#       硬约束禁 --live/下单/改源文件/commit。
param([string]$Strategy = "g2")
$Strategy = $Strategy.ToLower()
if ($Strategy -notin @("v13", "g2", "ens")) {
    Write-Error "Strategy 必须为 v13/g2/ens"
    exit 3
}
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sched = "D:\QuantLab\data\schedules"
New-Item -ItemType Directory -Force -Path $sched | Out-Null

$date = Get-Date -Format "yyyyMMdd"
$tag = "de_" + $Strategy + "_compare_" + $date
$log = Join-Path $sched ($tag + ".log")
$promptFile = Join-Path $sched ($tag + ".prompt.txt")
$outFile = Join-Path $sched ($tag + ".md")
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd"

# 交易日检查（0=交易日，1=非交易日 -> 跳过）
& $py "$proj\is_trade_day.py" *>> $log
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 非交易日($date)，跳过 $Strategy DE 兜底" -Encoding UTF8
    exit 0
}

switch ($Strategy) {
    "v13" {
        $prompt = @"
你是 Project_16 V1.3 换仓的独立核验助手（DE vs TRAE 对比实验，绝对禁止下单）。
工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\qmt_config.py、rebalance_daily.py、data\qmt_trade_log.csv、data\selections\、data\strategy_capital.json、D:/QMT_POOL/p16_v13_risk\ 下 cmd 与 state 全部 json。
步骤：
1) 用解释器 C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe 运行项目目录下的 rebalance_daily.py，参数 --date $date --top 2 （只 dry-run，绝对禁止加 --live）。运行前先备份当日执行产物（data/rebalance_$date.json，若存在）为 data/rebalance_$date.de_bak.json。
2) 读取 dry-run 输出的换仓计划，独立核验五条规则：a) 目标持仓是否 top2；b) 候选评分是否>=红线58；c) 卖出是否只来自策略持仓（qmt_trade_log 中 BUY 过的代码）；d) 持仓是否满 5 个交易日才到期（不足 SKIP 属正常）；e) 买入预算是否不超资金池（strategy_capital.json）×部署比例。
3) 读取当日实际执行（用步骤1的备份文件），给出对比结论：DE 核验是否与当日执行一致？有无风险点？
4) 把完整对比报告（计划摘要 / 核验结论 / 与当日执行对比 / 风险点）写入 $outFile。若部分文件读取失败，基于已读内容给出部分结论并在报告标注缺失项，报告必须写入指定路径。
硬约束：禁止 --live、禁止任何下单/撤单/改仓；禁止修改 qmt_config.py/rebalance_daily.py 及任何源文件；禁止 commit；禁止触碰 G2(70180771) 与融合(p16_ensemble_bridge) 的任何文件；如遇不确定只记录、不擅自行动。
"@
    }
    "ens" {
        $prompt = @"
你是 Project_16 融合版(ENS)换仓的独立核验助手（DE vs TRAE 对比实验，绝对禁止下单）。
工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\g2_ens_config.py、rebalance_ens.py、data\rebalance_ens\、data\selections\g2\、D:/QMT_POOL/p16_ensemble_bridge\ 下 cmd 与 state 全部 json。
步骤：
1) 用解释器 C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe 运行项目目录下的 rebalance_ens.py，参数 --date $date （只 dry-run，绝对禁止加 --live）。运行前先备份当日执行产物（data/rebalance_ens\rebalance_ens_$date.json 或实际文件名，若存在）为 .de_bak.json。
2) 读取 dry-run 输出的换仓计划，独立核验四条规则：a) 目标持仓是否 top2 等权；b) 候选评分是否>=红线60；c) 卖出是否只来自融合自己账本；d) 持仓是否满 10 个交易日才到期（不足 SKIP 属正常）。
3) 读取当日实际执行（用步骤1的备份文件），给出对比结论：DE 核验是否与当日执行一致？有无风险点？
4) 把完整对比报告（计划摘要 / 核验结论 / 与当日执行对比 / 风险点）写入 $outFile。若部分文件读取失败，基于已读内容给出部分结论并在报告标注缺失项，报告必须写入指定路径。
硬约束：禁止 --live、禁止任何下单/撤单/改仓；禁止修改 g2_ens_config.py/rebalance_ens.py 及任何源文件；禁止 commit；禁止触碰 V1.3(67014907) 与 G2(g2_bridge) 的任何文件；如遇不确定只记录、不擅自行动。
"@
    }
    default {
        $prompt = @"
你是 Project_16 G2 换仓的独立核验助手（DE vs TRAE 对比实验，绝对禁止下单）。
工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\g2_config.py、rebalance_g2.py、data\rebalance_g2\、data\selections\g2\、D:/QMT_POOL/g2_bridge\ 下的 cmd 与 state 全部 json。
步骤：
1) 用解释器 C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe 运行项目目录下的 rebalance_g2.py，参数 --date $date （只 dry-run，绝对禁止加 --live）。运行前先把 data\rebalance_g2\rebalance_g2_$date.json 复制为 data\rebalance_g2\rebalance_g2_$date.de_bak.json 备份当日执行现场，对比用备份文件。
2) 读取 dry-run 输出的换仓计划，独立核验四条规则：a) 目标持仓是否 top2 等权；b) 候选评分是否>=红线60；c) 卖出是否只来自 G2 自己账本；d) 持仓是否满 15 个交易日才到期（不足则 SKIP 属正常）。
3) 读取当日实际执行（用步骤1的备份文件），给出对比结论：DE 的独立核验结论是否与当日执行一致？有无风险点？
4) 把完整对比报告（计划摘要 / 核验结论 / 与当日执行对比 / 风险点）写入 $outFile。若部分文件读取失败，基于已读内容给出部分结论并在报告标注缺失项，报告必须写入指定路径。
硬约束：禁止 --live、禁止任何下单/撤单/改仓；禁止修改 g2_config.py/rebalance_g2.py 及任何源文件；禁止 commit；禁止触碰 V1.3(67014907) 与融合(p16_ensemble_bridge) 的任何文件；如遇不确定只记录、不擅自行动。
"@
    }
}
[System.IO.File]::WriteAllText($promptFile, $prompt, (New-Object System.Text.UTF8Encoding($false)))

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE $Strategy 换仓兜底开始 ($date) ====" -Encoding UTF8
$msg = "Read the file $promptFile and strictly follow every instruction in it. This is a READ-ONLY comparison task. Do NOT place any orders, do NOT use --live, do NOT modify any source files, do NOT commit. Write your report to the output path specified in the file."
& $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $log
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] deveco exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE $Strategy 换仓兜底结束 ====" -Encoding UTF8

# 飞书推送（报告路径 + 风险标记）
$risky = ""
if (Test-Path $outFile) {
    $head = Get-Content $outFile -TotalCount 120 -Encoding UTF8 | Out-String
    if ($head -match "P0|风险点\s*[:：]?\s*[1-9]|不一致|缺陷") { $risky = " ⚠ 发现风险/差异，详见报告" }
}
$pushMsg = "【DE兜底 $Strategy $date】deveco exit=$rc，报告: $outFile$risky"
$oPush = (& $py -c "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])" "$pushMsg" 2>&1 | Out-String)
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送: $pushMsg" -Encoding UTF8
exit $rc
