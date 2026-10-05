# coding: utf-8
# Project_22 RPS 每日监控 —— 交易日单日运行包装（T-20261004-003）
# 定位: 只做确定性数据层（CSV + 状态快照），非交易、不下单、不产生交易语义输出
#
# 交易日口径（两层，缺一不可）:
#   第1层 任务触发器 DaysOfWeek=62（周一至周五）→ 过滤周末
#   第2层 本脚本内查 astock 主库当日是否已有行情 → 过滤法定节假日/调休/数据未更新
#   注意: 仅靠第1层会把国庆春节全部误触发；两层都做才等价于"交易日"
#
# 退出码:
#   0  = 已产出且推送成功 / 非交易日正常跳过（不是错误）
#   1  = 交易日运行失败（门控 FAIL 或 Python 异常）/ 日历探测故障→ 需人工介入
#   2  = 通知降级（产物已落盘、核心计算成功，但飞书推送失败或收件人无法解析）
#   注: 日历探测失败属exit 1 而非 0 —— 探测挂了与"非交易日"必须可区分，
#       否则基础设施故障会被静默吞成正常跳过（fail-closed）
#   注: 成功路径不得无条件 exit 0 —— 推送失败必须以 exit 2 传出，否则
#       "跑成功但没推送到"在调度器里与正常无异，等于静默失联
$ErrorActionPreference = 'Continue'
$proj = 'D:/QuantLab/projects/Project_22_RPS每日监控'
$py = 'D:/Python311/python.exe'
$logDir = "$proj/results/logs"
$log = "$logDir/rps_daily_run.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 以 CLI 方式调用 P16 的统一推送出口（不 import，避免跨项目耦合 + 失败记录落错目录）
$larkPush = 'D:/QuantLab/projects/Project_16_LightGBM股票大师/lark_push.py'

function Write-Log([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# ---- 飞书推送配置（2026-10-04 业主拍板：成功与失败都推，便于知晓结果）----
# 收件人复用 Project_16 的 FEISHU_OPEN_ID（业主 2026-10-04 指定），P22 自身不另设配置。
# 唯一事实源 = P16 qmt_config.py 的 FEISHU_OPEN_ID：运行时正则提取，**P22 不留任何副本常量**。
# 为何不留 emergency fallback：副本一旦存在，P16 换人后 P22 会静默推给旧 open_id ——
#   业主以为在收通知、实际无人收到，这是无声失联，比"推不出去"危险得多。
#   推不出去有 exit 2 + 留档可查，无声失联没有任何信号。
$openId = $null
$p16Cfg = 'D:/QuantLab/projects/Project_16_LightGBM股票大师/qmt_config.py'
if (Test-Path -LiteralPath $p16Cfg) {
    $m = [regex]::Match((Get-Content -LiteralPath $p16Cfg -Raw -Encoding UTF8), 'FEISHU_OPEN_ID\s*=\s*["'']([^"'']+)["'']')
    if ($m.Success -and $m.Groups[1].Value) { $openId = $m.Groups[1].Value.Trim() }
}
if (-not $openId) {
    Write-Log "!! 无法从 $p16Cfg 解析 FEISHU_OPEN_ID —— 本次推送全部降级为本地留档（P22 不留副本常量，避免静默推错人）"
} else {
    Write-Log "飞书收件人取自 P16 qmt_config.py: $openId"
}

# 推送消息：写文件后用 --file 发送（避开 Windows 命令行 32767 字符上限）
# 推送是「非核心动作」：失败只记 exit 2，不改变产物已落盘的判定
function Save-PushFailed([string]$msgFile, [string]$tag, [string]$detail) {
    $keep = "$logDir/push_failed_$tag_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
    Move-Item -LiteralPath $msgFile -Destination $keep -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $keep) {
        $who = if ($openId) { $openId } else { '(收件人未解析)' }
        Add-Content -Path $keep -Value "`n---- 推送返回 ----`n$detail`n收件人: $who" -Encoding UTF8
    }
    Write-Log "未送达正文已留档: $keep"
}

function Send-Card([string]$title, [string]$body, [string]$tag) {
    $msgFile = "$logDir/msg_$tag.txt"
    $body | Set-Content -Path $msgFile -Encoding UTF8
    if (-not $openId) {
        # 收件人不可解析 = 无法确定推给谁，宁可不推也不推错人（正文留档，人工可补读）
        Write-Log "!! 收件人未解析，跳过推送并留档（fail-closed，不猜、不用历史值）"
        Save-PushFailed $msgFile $tag '收件人未解析：P16 qmt_config.py 的 FEISHU_OPEN_ID 不可读或为空'
        return 2
    }
    $pushOut = & $py $larkPush --file $msgFile --user-id $openId --title $title 2>&1
    $pushRc = $LASTEXITCODE
    Add-Content -Path $log -Value (($pushOut | Out-String).Trim()) -Encoding UTF8
    if ($pushRc -ne 0) {
        Write-Log "!! 飞书推送失败 exit=$pushRc（产物不受影响），失败详情见上方推送输出"
        # 失败时把未送达内容留档到 P22 自己的目录（P16 lark_push 的兜底记录落在 P16 下，
        # 跨项目查不到 = 等于没留痕）。成功才删临时文件。
        Save-PushFailed $msgFile $tag (($pushOut | Out-String).Trim())
        return 2
    }
    Write-Log "飞书推送成功: $title (收件人 $openId)"
    Remove-Item -LiteralPath $msgFile -Force -ErrorAction SilentlyContinue
    return 0
}

Write-Log "==== RPS 日跑开始 ===="

# ---- 交易日第2层判定: 主库当日是否已有行情 ----
# 复用 rps_daily 自身的日历口径（同一 stock_daily 日历），避免两套判断打架
$probe = @'
import sys, datetime as dt
import pyarrow.dataset as ds
from pandas import Timestamp
target = Timestamp(sys.argv[1])
dsrc = ds.dataset(r"D:\astock\daily\stock_daily.parquet")
# 只取目标日附近窗口：谓词下推，避免全表扫描（全列读约需27s）
# 向前多留 60 天历史（主库更新滞后时仍能报出 MAX 便于排查），向后留 30 天
lo = target - dt.timedelta(days=60)
hi = target + dt.timedelta(days=30)
tbl = dsrc.to_table(
    columns=["trade_date"],
    filter=(ds.field("trade_date") >= lo) & (ds.field("trade_date") <= hi),
)
dates = tbl.column("trade_date").to_pandas()
mx = dates.max() if len(dates) else None
hit = target in set(dates)
print("HIT=%d" % (1 if hit else 0))
print("MAX=%s" % (mx.strftime("%Y-%m-%d") if mx is not None else "unknown"))
'@
$probeFile = Join-Path $env:TEMP "p22_day_probe.py"
Set-Content -Path $probeFile -Value $probe -Encoding UTF8

$today = (Get-Date).ToString('yyyy-MM-dd')
$todayCompact = (Get-Date).ToString('yyyyMMdd')
$probeOut = & $py $probeFile $today 2>&1
$probeRc = $LASTEXITCODE
Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue

if ($probeRc -ne 0) {
    Write-Log "!! 日历探测失败 exit=$probeRc（非交易日不可能触发此分支），需人工介入"
    Write-Log ($probeOut | Out-String).Trim()
    $body = "【P22 RPS 日跑·失败】日历探测异常`n时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n退出码: $probeRc`n`n探测输出:`n$(($probeOut | Out-String).Trim())"
    Send-Card 'P22 RPS 日跑·失败' $body 'fail_probe' | Out-Null
    exit 1
}

$outText = ($probeOut | Out-String)
if ($outText -notmatch 'HIT=(\d)') {
    Write-Log "!! 日历探测输出无 HIT 标记，判为故障而非非交易日: $($outText.Trim())"
    Send-Card 'P22 RPS 日跑·失败' "【P22 RPS 日跑·失败】日历探测输出无 HIT 标记，判为故障而非非交易日。`n时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n`n原始输出:`n$($outText.Trim())" 'fail_nohit' | Out-Null
    exit 1
}
$hit = $Matches[1]
$maxDate = if ($outText -match 'MAX=([\d\-]+)') { $Matches[1] } else { 'unknown' }

Write-Log "交易日判定: 今天=$today 主库最新=$maxDate HIT=$hit"

if ($hit -ne '1') {
    Write-Log "非交易日或主库未更新到今日（正常跳过，exit 0）"
    exit 0
}

# ---- 交易日: 跑单日 ----
Write-Log "交易日命中，开始单日运行 --date $todayCompact"
$pyLog = "$logDir/rps_daily_$todayCompact.log"
& $py "$proj/research/rps_daily.py" --date $todayCompact --workers 2 *>> $pyLog
$rc = $LASTEXITCODE
Write-Log "rps_daily.py exit=$rc，详细输出: $pyLog"

if ($rc -ne 0) {
    Write-Log "!! 交易日运行失败，需人工介入"
    $tail = if (Test-Path -LiteralPath $pyLog) { (Get-Content -LiteralPath $pyLog -Tail 25 -Encoding UTF8 | Out-String).Trim() } else { '(单日日志不存在)' }
    Send-Card 'P22 RPS 日跑·失败' "【P22 RPS 日跑·失败】rps_daily.py 运行失败`n日期: $today`n退出码: $rc`n日志: $pyLog`n`n末尾输出:`n$tail" 'fail_run' | Out-Null
    exit 1
}

# ---- 落盘后自检: 当日两份产物是否生成 ----
$daily = "$proj/results/rps_rank_daily_$todayCompact.csv"
$full = "$proj/results/rps_rank_full_$todayCompact.csv"
if (-not (Test-Path -LiteralPath $daily) -or -not (Test-Path -LiteralPath $full)) {
    Write-Log "!! 退出码 0 但产物缺失（daily/full），视为失败"
    Send-Card 'P22 RPS 日跑·失败' "【P22 RPS 日跑·失败】退出码 0 但产物缺失`n日期: $today`ndaily 存在: $(Test-Path -LiteralPath $daily)`nfull 存在: $(Test-Path -LiteralPath $full)" 'fail_artifact' | Out-Null
    exit 1
}
$rows = (Import-Csv -LiteralPath $daily).Count
Write-Log "产物校验通过: daily 行数=$rows"

# ---- 成功推送: Top 榜摘要（非交易日跳过不推，只在真有结果时告知）----
# 注意: 推送失败必须以 exit 2 传出（通知降级），不能被末尾无条件 exit 0 吞掉 ——
#       否则"成功但没收到通知"在任务调度器里看起来跟完全正常一样，等于静默失联。
$pushRcFinal = 0
$basicBody = "【P22 RPS 日跑·完成】$today`n池规模(daily 行数): $rows`n产物: $daily`n(本次仅基础通知，Top10 摘要构建异常，详见运行日志)"
try {
    $top = Import-Csv -LiteralPath $daily | Select-Object -First 10
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("【P22 RPS 日跑·完成】$today")
    $lines.Add("池规模(daily 行数): $rows")
    $lines.Add("")
    $lines.Add("RPS Top10（代码/RPS20/RPS60/RPS120/涨停/题材）：")
    foreach ($r in $top) {
        # 列名以实际 CSV 表头为准: ts_code / rps_20 / rps_60 / rps_120 / in_zt_pool / zt_stat
        # （产物无 name 列，勿臆造字段，否则摘要整列为空）
        $code = $r.ts_code; $v20 = $r.rps_20; $v60 = $r.rps_60; $v120 = $r.rps_120
        $zt = if ($r.in_zt_pool -eq '1') { 'Y' } else { 'N' }
        $stat = $r.zt_stat; $ths = $r.ths_reason
        $lines.Add("$code | 20日:$v20 | 60日:$v60 | 120日:$v120 | 涨停:$zt $stat $ths")
    }
    $body = ($lines -join "`n")
    $pushRcFinal = Send-Card 'P22 RPS 日跑·完成' $body 'ok'
} catch {
    # 摘要构建失败也要让业主知道"跑成功了" —— 用户要求成功必推，故降级为基础通知
    Write-Log "!! 构建推送摘要异常（产物已落盘，不影响判定）: $($_.Exception.Message)"
    $pushRcFinal = Send-Card 'P22 RPS 日跑·完成' $basicBody 'ok_basic'
}
Write-Log "==== RPS 日跑结束 ===="
if ($pushRcFinal -ne 0) { Write-Log "产物已落盘但通知降级，退出码 2"; exit 2 }
exit 0