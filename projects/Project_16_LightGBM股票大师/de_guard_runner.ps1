# coding: utf-8
# DE 保障框架（T-20260903-024，2026-09-11）：5 个保障方向统一入口。
# 用法：powershell.exe -File de_guard_runner.ps1 -Mode health|reconcile|nightly|data|intraday
#   health    = 周度体系体检（周五盘后，直接跑 DE 7 维度体检）
#   reconcile = 日终对账异常复核（对账报告有 issues → DE 独立排查根因）
#   nightly   = 夜间检修 FAIL 分析（检修报告含 FAIL → DE 定位根因给建议）
#   data      = 数据断更诊断（新鲜度滞后/缺失 → DE 沿链路定位断点）
#   intraday  = 盘中异动归因（心跳超时/持仓波动>5% → DE 归因）
# 流程：交易日/窗口校验 → 触发检测（无异常则跳过）→ 生成 UTF-8 prompt → deveco run
#       → 写报告到 data/schedules/de_<mode>_<date>.md → 飞书推送。
param([string]$Mode = "health")
$Mode = $Mode.ToLower()
if ($Mode -notin @("health", "reconcile", "nightly", "data", "intraday")) {
    Write-Error "Mode 必须为 health/reconcile/nightly/data/intraday"
    exit 3
}
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$proj = "D:\QuantLab\projects\Project_16_LightGBM股票大师"
$sched = "D:\QuantLab\data\schedules"
New-Item -ItemType Directory -Force -Path $sched | Out-Null

$date = Get-Date -Format "yyyyMMdd"
$dateDash = Get-Date -Format "yyyy-MM-dd"
$tag = "de_" + $Mode + "_" + $date
$log = Join-Path $sched ($tag + ".log")
$promptFile = Join-Path $sched ($tag + ".prompt.txt")
$outFile = Join-Path $sched ($tag + ".md")
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd"

# ============ 飞书完成通知（2026-09-16 T-20260915-003 延续：所有完成路径都推送） ============
# 每日一次任务（health/reconcile/nightly/data）无论结果都推；intraday 每 30 分钟一次，仅触发/异常才推，
# 避免窗口外跳过/无异常刷屏。
function Send-Notify([string]$text) {
    $pyPush = "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])"
    $oPush = (& $py -c $pyPush "$text" 2>&1 | Out-String)
    $pushTry = 1
    while ($pushTry -lt 3 -and $oPush -match "飞书失败|not_configured") {
        Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送失败(第${pushTry}次)，15s 后重试" -Encoding UTF8
        Start-Sleep -Seconds 15
        $pushTry++
        $oPush = (& $py -c $pyPush "$text" 2>&1 | Out-String)
    }
    Add-Content -Path $log -Value $oPush -Encoding UTF8
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 飞书推送: $text" -Encoding UTF8
}

# 交易日检查（0=交易日，1=非交易日 -> 跳过）
& $py "$proj\is_trade_day.py" *>> $log
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 非交易日($date)，跳过 $Mode DE 保障" -Encoding UTF8
    if ($Mode -ne "intraday") { Send-Notify "【DE保障 $Mode $date】非交易日，跳过（任务已调度未执行）" }
    exit 0
}

# 时间窗口
$hhmm = [int](Get-Date -Format "HHmm")
if ($Mode -eq "intraday") {
    if ($hhmm -lt 930 -or $hhmm -gt 1510) {
        Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] $Mode 窗口外(09:30-15:10)，跳过" -Encoding UTF8
        exit 0
    }
}

# ============ 触发检测 ============
$triggerInfo = ""
switch ($Mode) {
    "health" {
        $triggerInfo = "周度体系体检（周五盘后例行）"
    }
    "reconcile" {
        $hits = @()
        # G2（TRAE 15:45）：json 存在则查 issues；缺失=对账未跑 → 先自动补跑修复（确定性脚本，安全）
        $g2 = "$proj\data\reconcile_g2\reconcile_g2_$date.json"
        if (-not (Test-Path $g2)) {
            Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] G2 对账产物缺失，自动补跑 reconcile_g2.py" -Encoding UTF8
            $o = (& $py "$proj\reconcile_g2.py" --date $date 2>&1 | Out-String)
            Add-Content -Path $log -Value $o -Encoding UTF8
        }
        if (Test-Path $g2) {
            $txt = Get-Content $g2 -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($txt -match '"issues"\s*:\s*\[\s*[^\]]') { $hits += "G2 对账有 issues" }
        } else {
            $hits += "G2 对账补跑后仍无产物（reconcile_g2.py 失败？）"
        }
        # 融合（TRAE 15:50）：输出 data/reconcile_g2_ens/
        $ens = "$proj\data\reconcile_g2_ens\reconcile_g2_ens_$date.json"
        if (Test-Path $ens) {
            $txt = Get-Content $ens -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($txt -match '"issues"\s*:\s*\[\s*[^\]]') { $hits += "融合对账有 issues" }
        } else {
            $hits += "融合对账产物缺失（15:50 任务未跑？）"
        }
        # V1.3（16:30 daily）：reconcile_trades 只输出 .md
        $v13 = "$proj\data\reconcile_$date.md"
        if (Test-Path $v13) {
            $txt = Get-Content $v13 -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($txt -match "⚠|问题|不一致|差额") { $hits += "V1.3 对账有异常项" }
        } else {
            $hits += "V1.3 对账产物缺失（16:30 daily 未跑？）"
        }
        if ($hits.Count -gt 0) {
            $triggerInfo = "对账核查：" + ($hits -join "；")
        }
    }
    "nightly" {
        $nf = "$proj\data\cache\nightly_check_$date.md"
        if (Test-Path $nf) {
            $txt = Get-Content $nf -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($txt -match "FAIL") { $triggerInfo = "夜间检修含 FAIL（$nf）" }
        } else {
            $triggerInfo = "夜间检修报告缺失（$nf）"
        }
    }
    "data" {
        $o = (& $py "$proj\detect_data_stale.py" 2>&1 | Out-String)
        Add-Content -Path $log -Value $o -Encoding UTF8
        if ($LASTEXITCODE -eq 2) {
            $lines = ($o -split "`r?`n" | Where-Object { $_ -match "⚠|滞后|缺失" })
            $triggerInfo = "数据滞后/缺失：" + (($lines | Select-Object -First 6) -join " | ")
        }
    }
    "intraday" {
        $o = (& $py "$proj\detect_intraday_anomaly.py" 2>&1 | Out-String)
        Add-Content -Path $log -Value $o -Encoding UTF8
        if ($LASTEXITCODE -eq 2) {
            $lines = ($o -split "`r?`n" | Where-Object { $_ -match "⚠" })
            $triggerInfo = "盘中异动：" + (($lines | Select-Object -First 6) -join " | ")
        }
    }
}

if (-not $triggerInfo) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] $Mode 无异常/未触发，跳过 DE" -Encoding UTF8
    if ($Mode -ne "intraday") { Send-Notify "【DE保障 $Mode $date】执行完成，无异常未触发 DE" }
    exit 0
}

# 幂等：当日报告已生成则不重跑 DE
if ((Test-Path $outFile) -and ((Get-Item $outFile).LastWriteTime.Date -eq (Get-Date).Date)) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] 当日报告已存在，跳过 DE 重跑: $outFile" -Encoding UTF8
    Send-Notify "【DE保障 $Mode $date】当日报告已存在，跳过重跑（幂等）"
    exit 0
}

# ============ 生成 prompt ============
switch ($Mode) {
    "health" {
        $prompt = @"
你是 Project_16 交易体系周度体检助手（DE 保障，只读，绝不下单）。工作目录：D:\QuantLab。
允许读取：D:\QuantLab\projects\Project_16_LightGBM股票大师\ 下 config/strategy/data/data_live/data\rebalance_g2/data\reconcile_g2/data\selections、D:/QMT_POOL/ 下 g2_bridge、p16_ensemble_bridge、p16_v13_risk 三桥 cmd/state 全部 json、D:\QuantLab\config、D:\QuantLab\全局控制台.md、D:\QuantLab\AGENTS.md、D:\QuantLab\data\schedules\ 下当日 de_* 报告。
任务：对本周（截至 $dateDash）交易体系做 7 维度体检：①三策略（V1.3/G2/融合）配置一致性（账号/资金池/隔离/持有期/红线）；②账本健康（幽灵持仓/hold_dates 陈旧/资金池滚动）；③桥健康（心跳/build_tag/pending/peak）；④定时任务（Windows schtasks 与看板对照，重复/错峰/缺失）；⑤数据新鲜度（moneyflow/增量库/面板/候选）；⑥风控门控（G2 门控/V1.3 tier/止损口径）；⑦产物完整性（positions_cfg/selection/对账/报告）。逐项给 PASS/FAIL/风险分级（P0/P1/P2），每项附证据（文件路径+关键值）；对比上周（若 data\schedules\de_health_weekly_上周.md 存在）给出变化。
输出：写完整周报到 $outFile（markdown：摘要/7 维度明细/风险分级清单/下周建议）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
    }
    "reconcile" {
        $prompt = @"
你是 Project_16 日终对账异常复核助手（DE 保障，只读，绝不下单）。工作目录：D:\QuantLab。
触发：当日（$dateDash）三策略对账报告含 issues。允许读取：data\reconcile_g2\reconcile_g2_$date.json、data\reconcile_$date.json（V1.3，若存在）、data\reconcile_ens\ 下当日 json（若存在）、D:/QMT_POOL/ 三桥 state（fills/positions/heart/asset）。
任务：对每个异常项（持仓差额/孤儿/未终态 fills/pending/资产异常）独立排查根因：①对照 fills 与 positions_cfg 推导账本 vs 账户持仓；②判断紧急度 P0/P1/P2；③给出处理建议（人工操作/脚本修复/仅观察）；④标注是否与昨日对账同源（历史遗留还是新发）。
输出：写报告到 $outFile（markdown：异常清单/逐项根因/紧急度/处理建议）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
    }
    "nightly" {
        $prompt = @"
你是 Project_16 夜间检修 FAIL 分析助手（DE 保障，只读，绝不下单）。工作目录：D:\QuantLab。
触发：当日（$dateDash）夜间检修报告含 FAIL。允许读取：data\cache\nightly_check_$date.md（及其引用的日志/产物文件）、D:\QuantLab\data\schedules\ 下相关日志。
任务：对每个 FAIL 项独立分析根因（读引用日志定位，不臆测），评估影响（次日开盘前是否必须处理），给出精确修复动作建议（命令/脚本路径/人工步骤）。
输出：写报告到 $outFile（markdown：FAIL 清单/逐项根因证据/影响评估/修复建议）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
    }
    "data" {
        $prompt = @"
你是 Project_16 数据断更诊断助手（DE 保障，只读，绝不下单）。工作目录：D:\QuantLab。
触发：当日（$dateDash）数据新鲜度检测发现滞后/缺失（详见 $log 末尾输出）。允许读取：D:\astock\moneyflow\moneyflow.parquet 元信息、data_live\（incremental_daily/merged_daily_full/feature_panel_v3/update_meta.json）、data\selections\、refresh_tushare_moneyflow.py、run_scheduled.ps1（daily 分支）、相关定时任务日志。
任务：对每个滞后/缺失项沿数据链路定位断点（上游刷新任务未跑？管道步骤失败？文件损坏？数据源不可用？），给出恢复动作建议（重跑命令/排查路径/人工步骤）。
输出：写报告到 $outFile（markdown：滞后清单/逐项链路诊断/恢复建议）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
    }
    "intraday" {
        $prompt = @"
你是 Project_16 盘中异动归因助手（DE 保障，只读，绝不下单）。工作目录：D:\QuantLab。
触发：当日（$dateDash）盘中检测到异常（详见 $log 末尾输出：桥心跳超时 或 持仓单日波动>5%）。允许读取：D:/QMT_POOL/ 三桥 state（heart/fills/positions/peak）、data\qmt_trade_log.csv、data\selections\、如可用再读相关新闻/公告（官方公告/财联社）。
任务：对每个异动项独立归因（桥进程异常/大盘带动/个股消息/板块联动/止损触发等），评估持续性与风险（是否可能引发连锁止损），给出关注/处置建议（盯盘重点/是否需人工干预）。
输出：写报告到 $outFile（markdown：异动清单/逐项归因/风险评级/处置建议）。
硬约束：禁止 --live、禁止下单/改仓、禁止修改任何文件、禁止 commit；数据缺失如实标注。
"@
    }
}
[System.IO.File]::WriteAllText($promptFile, $prompt, (New-Object System.Text.UTF8Encoding($false)))

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 保障 $Mode 开始 ($date) ====" -Encoding UTF8
Add-Content -Path $log -Value "触发: $triggerInfo" -Encoding UTF8
$msg = "Read the file $promptFile and strictly follow every instruction in it. This is a READ-ONLY guard task. Do NOT place orders, do NOT use --live, do NOT modify any files, do NOT commit. Write your report to the output path specified in the file. Trigger info: $triggerInfo"
& $deveco run $msg --dangerously-skip-permissions --dir D:\QuantLab --format json *>> $log
$rc = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] deveco exit=$rc" -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ==== DE 保障 $Mode 结束 ====" -Encoding UTF8

$risky = ""
if (Test-Path $outFile) {
    $head = Get-Content $outFile -TotalCount 150 -Encoding UTF8 | Out-String
    if ($head -match "P0|P1|风险|⚠|FAIL|异常|不一致") { $risky = " ⚠ 报告含风险项，详见文件" }
}
if ($rc -ne 0) {
    Send-Notify "【DE保障 $Mode $date】DE 执行异常 exit=$rc，报告: $outFile 未完整产出，需人工核查"
} else {
    $pushMsg = "【DE保障 $Mode $date】执行完成 exit=$rc，报告: $outFile$risky"
    Send-Notify $pushMsg
}
exit $rc
