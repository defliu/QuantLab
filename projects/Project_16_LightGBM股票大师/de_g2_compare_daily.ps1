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
1) 用解释器 C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe 运行项目目录下的 rebalance_g2.py，参数 --date $date （只 dry-run，绝对禁止加 --live）。**运行前先把 data\rebalance_g2\rebalance_g2_$date.json 复制为 data\rebalance_g2\rebalance_g2_$date.de_bak.json 备份当日执行现场**（若 .de_bak.json 已存在则保留首份、禁止覆盖——那是最初的执行现场证据，重跑不得用新内容冲掉），对比用备份文件。
2) 读取 dry-run 输出的换仓计划，独立核验四条规则：a) 目标持仓是否 top3 等权；b) 候选评分是否>=红线60；c) 卖出是否只来自 G2 自己账本；d) 持仓是否满15个交易日才到期（不足则 SKIP 属正常）。
3) 读取当日实际执行（用步骤1的备份文件），给出对比结论：DE 的独立核验结论是否与当日执行一致？有无风险点？
4) 把完整对比报告（计划摘要 / 核验结论 / 与当日实际执行对比 / 风险点）写入 $outFile。若部分文件读取失败，基于已读内容给出部分结论并在报告标注缺失项，报告必须写入指定路径。
5) **报告必须以「## 飞书推送摘要」章节收尾**（紧跟在核验结论之后、作为报告最后一个章节），供推送脚本抽取后直接发到飞书。该章节格式必须严格如下（用一个无列名要求的 Markdown 表格 + 编号列表，脚本按表头关键词取列，故表头文字要含「结论/卖出/买入/跳过/风险」等关键词）：
   ## 飞书推送摘要
   | 项 | 内容 |
   |---|---|
   | 核验结论 | 一句话（如「四条规则全部通过，与当日实际执行一致」） |
   | 卖出计划 | 逐笔列出（代码/股数/原因），无则填「无」 |
   | 买入计划 | 逐笔列出（代码/股数/原因），无则填「无」 |
   | 跳过项 | 逐条列出（代码/原因），无则填「无」 |
   | 风险点 | 逐条列出（P0/P1/P2/P3 + 一句话），无则填「无风险」 |
   该章节必须能独立读懂，不依赖上文；表格里的文字要完整，不要写「见上文」。
硬约束：禁止 --live、禁止任何下单/撤单/改仓；禁止修改 g2_config.py/rebalance_g2.py 及任何源文件；禁止 commit；禁止触碰 V1.3(67014907) 与融合(p16_ensemble_bridge) 的任何文件；如遇不确定只记录、不擅自行动。
"@
[System.IO.File]::WriteAllText($promptFile, $prompt, (New-Object System.Text.UTF8Encoding($false)))

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE G2 换仓对比开始 ($date) ====" -Encoding UTF8
$msg = "Read the file $promptFile and strictly follow every instruction in it. This is a READ-ONLY comparison task. Do NOT place any orders, do NOT use --live, do NOT modify any source files, do NOT commit. Write your report to the output path specified in the file."
& $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $log
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] deveco exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE G2 换仓对比结束 ====" -Encoding UTF8
# 飞书推送（2026-10-15 优化：推报告正文，不再是只有路径的一行）
# 从报告「## 飞书推送摘要」章节抽取正文；抽取成功推正文，失败回退推"路径+风险标记"。
# 正文经文件传递而非 argv（中文+换行经命令行有编码/转义风险，与 de_report_daily.ps1 同法）。
$utf8 = New-Object System.Text.UTF8Encoding($false)
$pushMsgFile = Join-Path $sched ("de_g2_pushmsg_" + $date + ".txt")
$risky = ""
if (Test-Path $outFile) {
    $head = Get-Content $outFile -TotalCount 120 -Encoding UTF8 | Out-String
    if ($head -match "P0|风险点\s*[:：]?\s*[1-9]|不一致|缺陷") { $risky = " ⚠ 发现风险/差异" }
}
# 抽取「## 飞书推送摘要」章节正文（到下一个 ## 或文档结束）
$body = ""
if (Test-Path $outFile) {
    $lines = Get-Content $outFile -Encoding UTF8
    $inSec = $false
    $buf = @()
    foreach ($ln in $lines) {
        if ($ln -match "^##\s+飞书推送摘要") { $inSec = $true; continue }
        if ($inSec -and $ln -match "^##\s+") { break }
        if ($inSec) { $buf += $ln }
    }
    $body = ($buf -join "`n").Trim()
}
if ($body.Length -gt 0) {
    $pushMsg = "【DE兜底 G2 $date】deveco exit=$rc，$risky`n" + $body + "`n（报告全文: $outFile）"
} else {
    # 摘要节缺失（DE 未按新 prompt 产出/旧报告）：回退推路径，不静默丢内容标记
    $pushMsg = "【DE兜底 G2 $date】deveco exit=$rc，报告: $outFile$risky`n（⚠ 未解析到「飞书推送摘要」节，本次仅推路径）"
}
[System.IO.File]::WriteAllText($pushMsgFile, $pushMsg, $utf8)
$nsCode = "import io,sys; sys.path.insert(0, r'$proj'); from qmt_bridge_client import notify_feishu; sys.exit(0 if notify_feishu(io.open(sys.argv[1],encoding='utf-8').read()) else 3)"
$oPush = (& $py -c $nsCode $pushMsgFile 2>&1 | Out-String).Trim()
$nsRc = $LASTEXITCODE
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送 exit=$nsRc 正文=$($pushMsg.Length) 字符（摘要节=$([bool]$body)）" -Encoding UTF8
if ($nsRc -ne 0) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 正文推送失败，回退推路径" -Encoding UTF8
    $fbMsg = "【DE兜底 G2 $date】deveco exit=$rc，报告: $outFile$risky"
    $oFb = (& $py -c "import sys; sys.path.insert(0, r'$proj'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])" "$fbMsg" 2>&1 | Out-String)
    Add-Content -Path $log -Value $oFb -Encoding UTF8
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书回退推送: $fbMsg" -Encoding UTF8
}
exit $rc
