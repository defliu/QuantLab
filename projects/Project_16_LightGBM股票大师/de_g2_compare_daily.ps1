# coding: utf-8
# DE vs TRAE 对比实验：G2 换仓独立核验（只读 + dry-run，绝不下单）
# 每日 10:05（TRAE 09:50 G2换仓之后）执行，产出 de_g2_compare_<date>.md
# 依赖：deveco CLI（C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd，v0.1.12）
# 安全设计：prompt 写 UTF-8 文件（DE 用 read 工具读，避免 5.1->cmd 命令行中文编码问题）；
#           消息只传 ASCII 文件路径；硬约束禁 --live/下单/改源文件/commit。
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sched = "D:\QuantLab\data\schedules"
New-Item -ItemType Directory -Force -Path $sched | Out-Null

$date = Get-Date -Format "yyyyMMdd"
$log = Join-Path $sched ("de_g2_compare_" + $date + ".log")
$promptFile = Join-Path $sched ("de_g2_prompt_" + $date + ".txt")
$outFile = Join-Path $sched ("de_g2_compare_" + $date + ".md")
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd"

# 交易日检查（0=交易日，1=非交易日 -> 跳过）
& $py "$proj\is_trade_day.py" *>> $log
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 非交易日($date)，跳过 DE 对比" -Encoding UTF8
    exit 0
}

$prompt = @"
你是 Project_16 G2 换仓的独立核验助手（DE vs TRAE 对比实验，绝对禁止下单）。
工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\g2_config.py、rebalance_g2.py、data\rebalance_g2\、data\selections\g2\、D:/QMT_POOL/g2_bridge\ 下的 cmd 与 state 全部 json。
步骤：
1) 用解释器 C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe 运行项目目录下的 rebalance_g2.py，参数 --date $date （只 dry-run，绝对禁止加 --live）。**运行前先把 data\rebalance_g2\rebalance_g2_$date.json 复制为 data\rebalance_g2\rebalance_g2_$date.de_bak.json 备份当日执行现场**，对比用备份文件。
2) 读取 dry-run 输出的换仓计划，独立核验四条规则：a) 目标持仓是否 top2 等权；b) 候选评分是否>=红线60；c) 卖出是否只来自 G2 自己账本；d) 持仓是否满15个交易日才到期（不足则 SKIP 属正常）。
3) 读取当日实际执行（用步骤1的备份文件），给出对比结论：DE 的独立核验结论是否与当日执行一致？有无风险点？
4) 把完整对比报告（计划摘要 / 核验结论 / 与当日执行对比 / 风险点）写入 $outFile。若部分文件读取失败，基于已读内容给出部分结论并在报告标注缺失项，报告必须写入指定路径。
硬约束：禁止 --live、禁止任何下单/撤单/改仓；禁止修改 g2_config.py/rebalance_g2.py 及任何源文件；禁止 commit；禁止触碰 V1.3(67014907) 与融合(p16_ensemble_bridge) 的任何文件；如遇不确定只记录、不擅自行动。
"@
[System.IO.File]::WriteAllText($promptFile, $prompt, (New-Object System.Text.UTF8Encoding($false)))

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE G2 换仓对比开始 ($date) ====" -Encoding UTF8
$msg = "Read the file $promptFile and strictly follow every instruction in it. This is a READ-ONLY comparison task. Do NOT place any orders, do NOT use --live, do NOT modify any source files, do NOT commit. Write your report to the output path specified in the file."
& $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $log
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] deveco exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE G2 换仓对比结束 ====" -Encoding UTF8
# 飞书推送（报告路径 + 风险标记，2026-09-11）
$risky = ""
if (Test-Path $outFile) {
    $head = Get-Content $outFile -TotalCount 120 -Encoding UTF8 | Out-String
    if ($head -match "P0|风险点\s*[:：]?\s*[1-9]|不一致|缺陷") { $risky = " ⚠ 发现风险/差异，详见报告" }
}
$pushMsg = "【DE兜底 G2 $date】deveco exit=$rc，报告: $outFile$risky"
$oPush = (& $py -c "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])" "$pushMsg" 2>&1 | Out-String)
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送: $pushMsg" -Encoding UTF8
exit $rc
