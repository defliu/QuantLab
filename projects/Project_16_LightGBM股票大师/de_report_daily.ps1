# coding: utf-8
# DE 报告流水线（T-20260903-023）：TRAE 端协调下指令 → DE(GLM5.3) 生成报告 → 飞书推送
# 用法：powershell.exe -File de_report_daily.ps1 -Task close|midday [-ForcePush] [-DryRun]
#   close  = 盘后持仓复盘（15:40 后，当日四策略执行痕迹+行情 → 复盘报告 + 风险核对）
#   midday = 午休持仓报告（11:35 后，半日持仓/盘面 → 简报告）
#   -ForcePush = 同日已推送过也强制重发（修完抽取器后补推用）
#   -DryRun    = 只产出推送正文文件、不真发飞书（验证正文完整度用）
# 流程：is_trade_day 校验 → 生成 UTF-8 prompt → deveco run 生成报告到 data/schedules/de_report_<task>_<date>.md
#       → 抽取正文（stdout=正文 / stderr=统计，分开落盘）→ 飞书全文推送（超长自动分片）。
# 退出码：0 正常 / 3 参数错 / 10 抽取失败(告警已发) / 11 推送失败 / 12 抽取+推送双失败
param(
    [string]$Task = "close",
    [switch]$ForcePush,
    [switch]$DryRun
)
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
$sentFile = Join-Path $sched ($tag + ".pushed")
$failFile = Join-Path $sched ($tag + ".extract_failed")
$devecoOut = Join-Path $sched ($tag + ".deveco.txt")
$exOut = Join-Path $sched ($tag + ".push.txt")
$exErr = Join-Path $sched ($tag + ".push.err.txt")
$pushFile = Join-Path $sched ($tag + ".pushmsg.txt")
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd"
$utf8 = New-Object System.Text.UTF8Encoding($false)

# 本脚本用 UTF-8 追加日志；历史日志由 `*>>` 写成 UTF-16LE，两者混在一个文件里会花屏。
# 检测到旧编码日志就改名留档，本轮从新文件开始，绝不混写。
if (Test-Path $log) {
    $fs = [System.IO.File]::OpenRead($log)
    $bom = New-Object byte[] 2
    $null = $fs.Read($bom, 0, 2)
    $fs.Close()
    if ($bom[0] -eq 0xFF -and $bom[1] -eq 0xFE) {
        $bak = $log + ".utf16.bak"
        if (Test-Path $bak) {
            $bak = $log + ".utf16." + (Get-Date -Format "HHmmss") + ".bak"
        }
        Move-Item -Path $log -Destination $bak -Force
    }
}

function Write-Log([string]$line) {
    [System.IO.File]::AppendAllText($log, $line + [Environment]::NewLine, $utf8)
}

function Read-Text8([string]$path) {
    if (-not (Test-Path $path)) { return "" }
    return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
}

# 抽取器需要 stdout(正文) 与 stderr(统计) 严格分离。
# PS 5.1 的 `1>` 写 UTF-16LE、`2>` 会把 stderr 包成 NativeCommandError、`2>&1` 会把统计混进正文，
# 三种都不能用；Start-Process 走句柄级重定向，保留原始 UTF-8 字节且能拿到退出码。
# 入参限制：只用于「无空格路径」的原生命令（is_trade_day / extract），带空格长文本一律不走这里。
function Run-NativeSplit([string]$exe, [string[]]$argv, [string]$outF, [string]$errF) {
    $p = Start-Process -FilePath $exe -ArgumentList $argv -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $outF -RedirectStandardError $errF
    return $p.ExitCode
}

Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 报告 $Task 开始 ($date) ===="

$isoRc = Run-NativeSplit $py @("$proj\is_trade_day.py") (Join-Path $sched ($tag + ".trade_day.txt")) (Join-Path $sched ($tag + ".trade_day.err.txt"))
if ($isoRc -ne 0) {
    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 非交易日($date)，跳过 $Task DE 报告"
    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 报告 $Task 结束 ===="
    exit 0
}

if ($Task -eq "midday") {
    $prompt = @"
你是 Project_16 午休持仓报告助手（DE 生成，只读，绝不下单）。工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\data\（qmt_trade_log.csv、selections\、rebalance_g2\、reconcile_g2\、real\）、D:/QMT_POOL/g2_bridge/state/、p16_ensemble_bridge/state/、p16_v13_risk/state/、D:/QMT_POOL/g2_bridge/cmd/、p16_ensemble_bridge/cmd/、p16_v13_risk/cmd/、D:/QMT_POOL/v2_holdings_state.json、D:/QMT_POOL/strategy_log_v2.txt、D:\QuantLab\config\value_smallcap_v2_config.yaml 的 json/csv。
任务：汇总当日（$date）截至午休（11:30）的四策略（V1.3 账号67014907 / 价值小盘V2 账号67014907 / G2 与融合 账号70180771）持仓报告，输出：
①**逐票持仓明细表**（每只持仓一行，必含）：代码、名称、所属策略、持仓股数、成本价（含费）、现价（用腾讯行情 qt.gtimg.cn 实时取价，格式 q=sh601058/q=sz300475，cost 从 positions_cfg 读）、当日涨跌幅%、持仓市值（现价×股数）、浮盈/浮亏金额（(现价-成本)×股数）与浮盈/浮亏百分比、距止损线（成本×0.93）与止盈线（成本×1.15）的百分比缓冲；
②当日已成交动作（V1.3 qmt_trade_log / G2与融合 fills）买卖明细；
③桥健康（G2/融合/V13 三桥心跳时间/build_tag/pending/peak；价值小盘V2 无桥，心跳取 strategy_log_v2.txt 最后更新时间及其 hb 行的持仓数/现金/nav）；
④午后关注点：浮亏最大、最接近止损/止盈线、当日跌幅最大的持仓优先提示。
数据只读当日与最近历史；腾讯取价失败则该票标"取价失败"并说明。
输出：写报告到 $outFile（markdown，含完整逐票明细表格与结论）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
} else {
    $prompt = @"
你是 Project_16 盘后持仓复盘助手（DE 生成，只读，绝不下单）。工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\data\（qmt_trade_log.csv、selections\、rebalance_g2\、reconcile_g2\、reconcile\、real\）、D:/QMT_POOL/g2_bridge/state/、p16_ensemble_bridge/state/、p16_v13_risk/state/、D:/QMT_POOL/g2_bridge/cmd/、p16_ensemble_bridge/cmd/、p16_v13_risk/cmd/、D:/QMT_POOL/v2_holdings_state.json、D:/QMT_POOL/strategy_log_v2.txt、D:\QuantLab\config\value_smallcap_v2_config.yaml、data\schedules\de_g2_compare_$date.md（若有）的 json/csv。
任务：汇总当日（$date）收盘后四策略（V1.3 账号67014907 / 价值小盘V2 账号67014907 / G2 与融合 账号70180771）持仓复盘，输出：
①**逐票持仓明细表**（每只持仓一行，必含）：代码、名称、所属策略、持仓股数、成本价（含费，positions_cfg 读）、当日收盘价（腾讯行情 qt.gtimg.cn 实时取价，格式 q=sh601058/q=sz300475；失败标"取价失败"）、当日涨跌幅%、当日开盘/最高/最低、持仓市值、浮盈/浮亏金额（(现价-成本)×股数）与百分比、距止损线（成本×0.93）与止盈线（成本×1.15）的百分比缓冲、持有起始日与已持交易日/满期剩余日；
②**已实现盈亏明细**：当日卖出成交（G2/融合 fills、V1.3 qmt_trade_log）每笔：代码/股数/卖出价/估算盈亏；以及四策略资金池当前值（g2/ens/v13 的 strategy_capital、价值小盘V2 的 v2_holdings_state.json 现金+持仓市值）；
③当日各策略动作汇总（换仓/对账结果/持有期）；
④桥健康（G2/融合/V13 三桥心跳时间/build_tag/pending/peak；价值小盘V2 无桥，心跳取 strategy_log_v2.txt 最后更新时间及其 hb 行、异常标注）；
⑤风控体检：逐票列"距止损/止盈线最近"风险排行（缓冲<3% 标 🔴）；
⑥明日关注点（候选 top 变化、数据新鲜度、临近到期的持仓）。
数据只读当日与最近历史。
输出：写报告到 $outFile（markdown，含完整逐票明细表格、盈亏排行、风险分级）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
}
[System.IO.File]::WriteAllText($promptFile, $prompt, $utf8)

# 幂等（T-20260903-023）：当日报告已生成 → 不重跑 DE，直接推送既有报告（TRAE/Windows 双触发保护）
if ((Test-Path $outFile) -and ((Get-Item $outFile).LastWriteTime.Date -eq (Get-Date).Date)) {
    Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 当日报告已存在，跳过 DE 重跑: $outFile"
    $rc = 0
} else {
    $msg = "Read the file $promptFile and strictly follow every instruction in it. This is a READ-ONLY report generation task. Do NOT place orders, do NOT use --live, do NOT modify any files, do NOT commit. Write your report to the output path specified in the file."
    # deveco 的提示词含空格与中文，交给 `&` 由 PowerShell 自己转义，不能走 Start-Process 的裸拼串。
    # 输出独立落盘：本文件内始终是同一种编码，日志只摘要前 2000 字符，不整包灌进主日志。
    & $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $devecoOut
    $rc = $LASTEXITCODE
    # 失败重试（2026-09-14）：模型服务过载等导致 exit!=0 或报告未生成 → 等 60 秒用同一 prompt 重跑 1 次；
    # 仍失败则按原逻辑结束。幂等：外层"当日报告已存在则跳过 DE"保护不变，重跑为只读报告生成，无副作用。
    if (($rc -ne 0) -or (-not (Test-Path $outFile))) {
        Write-Log "[$(Get-Date -Format 'HH:mm:ss')] deveco 首次运行未成功(exit=$rc，报告已生成=$((Test-Path $outFile)))，60 秒后重试 1 次"
        Start-Sleep -Seconds 60
        & $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $devecoOut
        $rc = $LASTEXITCODE
        Write-Log "[$(Get-Date -Format 'HH:mm:ss')] deveco 重试 exit=$rc，报告已生成=$((Test-Path $outFile))"
    }
    $devecoLog = (Get-Content -Raw -Path $devecoOut -ErrorAction SilentlyContinue)
    if ($devecoLog) { Write-Log "[$(Get-Date -Format 'HH:mm:ss')] deveco 输出: $($devecoLog.Substring(0, [Math]::Min(2000, $devecoLog.Length)))" }
}
Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] deveco exit=$rc"

# 推送幂等（2026-09-28）：此前只对 DE 重跑做了幂等，推送没有。
# 日志可见同日 16:12 与 16:20 重复收到同一条降级消息。
if ((Test-Path $sentFile) -and (-not $ForcePush)) {
    Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 同日推送已成功过，跳过重复推送（-ForcePush 可强制重发）: $sentFile"
    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 报告 $Task 结束 ===="
    exit $rc
}

# ---- 抽取推送正文：stdout=正文，stderr=统计，分开落盘 ----
$pushMsg = ""
$pushStat = ""
$exRc = 1
if (Test-Path $outFile) {
    $exRc = Run-NativeSplit $py @("$proj\extract_de_report_push.py", "$outFile") $exOut $exErr
    $pushMsg = (Read-Text8 $exOut).Trim()
    $pushStat = (Read-Text8 $exErr).Trim()
} else {
    $pushStat = "报告文件不存在: $outFile"
}
Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 抽取 exit=$exRc，正文=$($pushMsg.Length) 字符"
if ($pushStat) { Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 抽取统计: $pushStat" }

# 不再静默降级成"只有报告路径"。抽取失败时把真实原因发出去，让问题可见。
$extractFailed = ($exRc -ne 0 -or [string]::IsNullOrWhiteSpace($pushMsg))
if ($extractFailed) {
    $reason = if ($pushMsg) { $pushMsg } else { "(stdout 为空)" }
    $statTxt = if ($pushStat) { $pushStat } else { "(无)" }
    $pushMsg = @"
【DE报告 $Task $date】❌ 推送正文抽取失败，本次未推送报告内容
· 报告文件: $outFile
· 抽取器退出码: $exRc
· 失败输出: $reason
· 解析统计: $statTxt
· 说明: 本条是失败告警，不是报告内容。修复抽取器后请用 -ForcePush 重推。
"@
    [System.IO.File]::WriteAllText($failFile, ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " extract_failed exRc=$exRc"), $utf8)
    Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 抽取失败，改为推送失败详情而非路径占位"
} elseif (Test-Path $failFile) {
    Remove-Item -Path $failFile -Force
}

# ---- 推送：正文全文进飞书，由 notify_feishu 负责分片与失败可见 ----
# 正文经文件传递而非 argv：6K 中文经 Windows 命令行既有长度压力又有引号/换行转义风险。
$ok = $false
if (-not [string]::IsNullOrWhiteSpace($pushMsg)) {
    [System.IO.File]::WriteAllText($pushFile, $pushMsg, $utf8)
    Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 推送正文已落盘: $pushFile（$($pushMsg.Length) 字符）"
    if ($DryRun) {
        Write-Log "[$(Get-Date -Format 'HH:mm:ss')] -DryRun：跳过真实飞书发送"
        $ok = $true
    } else {
        $nsCode = "import io,sys; sys.path.insert(0, r'$proj'); from qmt_bridge_client import notify_feishu; sys.exit(0 if notify_feishu(io.open(sys.argv[1],encoding='utf-8').read()) else 3)"
        $oPush = (& $py -c $nsCode $pushFile 2>&1 | Out-String).Trim()
        $nsRc = $LASTEXITCODE
        $ok = ($nsRc -eq 0)
        if ($oPush) { Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 推送日志: $oPush" }
        if (-not $ok) { Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 推送失败 exit=$nsRc（分片可能残缺，需人工核对）" }
    }
}

# 幂等标记只在"真发成功且正文不是失败告警"时落盘：
# 抽取失败即便告警发出去了也不能写 .pushed，否则抽取器修好后当天再也推不出真报告。
if ($ok -and (-not $extractFailed) -and (-not $DryRun)) {
    [System.IO.File]::WriteAllText($sentFile, ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " pushed by de_report_daily.ps1"), $utf8)
}

$exitCode = $rc
if ($DryRun) { $exitCode = 0 }
if ($extractFailed) { $exitCode = 10 }
if (-not $ok) { $exitCode = 11 }
if ($extractFailed -and (-not $ok)) { $exitCode = 12 }
Write-Log "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送 $(if ($DryRun) { 'DRYRUN 已生成正文' } elseif ($ok) { '成功' } else { '失败' })，推送正文=$($pushMsg.Length) 字符"
Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 报告 $Task 结束 exit=$exitCode ===="
exit $exitCode
