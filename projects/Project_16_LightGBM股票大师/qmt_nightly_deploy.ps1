# coding: utf-8
# QMT 夜间闭环（T-20261028-002，2026-10-28 立）：构建 G2 产物 → 部署到 70180771 沙箱客户端
# → 重启 → 核对 BUILD_TAG → 扫描两台客户端日志 → 落 md/json 报告。
#
# 边界（硬约束，不在本脚本里放宽）：
#   1) 只重启 --qmt-dir 指定的沙箱客户端 D:\QMT交易端模拟（qmt_autodeploy 按 ExecutablePath
#      前缀过滤 PID，绝不误杀生产客户端）；
#   2) 生产客户端 D:\国金QMT交易端模拟（67014907，P16 V1.3 + G2/ENS）**只读扫描、绝不重启**；
#   3) 交易时段 09:00-15:05 由 qmt_autodeploy 内置时段闸拒绝（本脚本不加 --force-offhours）；
#   4) 构建失败 = 不部署（qmt_autodeploy --build 返回非 0 直接退出）。
#
# 调度：每日 21:10（非交易时段；20:54 客户端自重启之后，21:20 夸克备份之前）。
# 演练：powershell -File qmt_nightly_deploy.ps1 -Rehearsal（只构建+校验，**不落盘、不重启**，
#       跳过 TAG 核对；报告改名 REHEARSAL_*，且不覆盖 latest.json —— 演练永远不算 PASS）。
# 失败处置：退非 0，agent/人读 data/schedules/qmt_nightly.log 尾部 +
#          results/qmt_nightly/latest.json（含每模型 BUILD_TAG / 被动 / 异常签名）接手。
# 登录前置闸：真实闭环前先问 scripts/qmt_login_probe.py；判「未登录」（rc=2）则整轮跳过、
#          退 0 并记 WAIT（不 build/不落盘/不重启，见上文）。rc=4（进程不在）不拦。
param(
    [switch]$Rehearsal
)
$py = "C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe"
$dir = "D:/QuantLab/projects/Project_16_LightGBM股票大师"
$log = "$dir/data/schedules/qmt_nightly.log"
$reportDir = "$dir/results/qmt_nightly"
New-Item -ItemType Directory -Force -Path "$dir/data/schedules" | Out-Null
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$mode = if ($Rehearsal) { "演练：不重启" } else { "构建+部署+重启" }
Add-Content -Path $log -Value "[$ts] ==== QMT 夜间闭环（$mode）开始 ====" -Encoding UTF8

# ---- 登录前置闸（2026-10-29 立，T-20261029-002）----
# 客户端没登录券商账号时 handlebar 永不触发 ⇒ 本轮部署必然回读不到 BUILD_TAG，
# 结果是「白耗一次客户端重启 + 一条假 FAIL 报告，还会覆盖 latest.json」。
# 所以真实闭环之前先问只读探针 scripts/qmt_login_probe.py：
#   退出码 2（进程在跑但未登录，最常见是柜台 200003 超时）⇒ 整轮跳过，不 build / 不落盘 /
#     不重启，只记一行 WAIT（当日 08:45 前每 15 分钟由 Quant_P16_QMT_NightlyRetry 再试）；
#   退出码 4（客户端进程不在）⇒ 不拦，autodeploy 会带 reboot 把它拉起来，正是恢复路径；
#   -Rehearsal ⇒ 不拦（演练本来就不碰客户端）。
if (-not $Rehearsal) {
    $env:PYTHONIOENCODING = "utf-8"
    $encBak = [Console]::OutputEncoding
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
    $probeOut = & $py "D:/QuantLab/scripts/qmt_login_probe.py" --verdict --client "D:\QMT交易端模拟" 2>&1
    $probeRc = $LASTEXITCODE
    try { [Console]::OutputEncoding = $encBak } catch { }
    $probeLine = (($probeOut | Out-String) -replace "\s+", " ").Trim()
    if ($probeRc -eq 2) {
        Add-Content -Path $log -Value "[$ts] WAIT 沙箱未登录，本轮闭环整轮跳过（不 build/不落盘/不重启）：$probeLine" -Encoding UTF8
        Add-Content -Path $log -Value "[$ts] ==== QMT 夜间闭环跳过（登录前置闸）====" -Encoding UTF8
        exit 0
    }
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] PROBE rc=$probeRc $probeLine" -Encoding UTF8
}

# ---- 部署互斥锁 ----
# 闭环会「关闭客户端 → 带 reboot 参数重启」，中途存在几十秒的无进程窗口。
# scripts/qmt_watch_login.ps1 每 10 分钟探一次，看到客户端不在跑就会抢先 Start-Process 拉起，
# 与闭环自己的重启撞车（单实例交接 → PID 抖动，历史上已因此产生过「假失败」）。
# 因此闭环期间落一个锁文件，watch_login 见到新鲜锁文件就跳过本轮。
# 锁不做后台清理：脚本若被强杀会留残锁，watch_login 按 40 分钟龄期自动失效。
$lock = "$dir/data/schedules/qmt_deploy.lock"
Set-Content -Path $lock -Value ("PID={0} START={1} MODE={2}" -f $PID, $ts, $mode) -Encoding UTF8

# ---- 报告留存清理：只留最近 60 天（md/json 同名，逐对删）----
if (Test-Path $reportDir) {
    $cut = (Get-Date).AddDays(-60)
    Get-ChildItem -Path $reportDir -Filter "*.md" -File | Where-Object { $_.LastWriteTime -lt $cut } | ForEach-Object {
        $base = $_.FullName.Substring(0, $_.FullName.Length - 3)
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ($base + ".json") -Force -ErrorAction SilentlyContinue
    }
}

# ---- 闭环执行 ----
# python 侧输出 UTF-8；PS 5.1 的原生命令输出按 [Console]::OutputEncoding 解码，
# 不改就会把 UTF-8 当 GBK 解成乱码；且 `*>> $log` 用 UTF-16LE 追加，与本脚本的
# UTF-8 行混在同一文件里 → 抓回变量后统一 Add-Content -Encoding UTF8 写入。
$env:PYTHONIOENCODING = "utf-8"
$oldEnc = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$loopArgs = @(
    "$dir/qmt_nightly.py", "loop",
    "--build", "build_p16_g2_selfcontained.py",
    "--product", "build/strategy_p16_g2_selfcontained.py",
    "--model", "STRATEGY_P16_G2_SELFCONTAINED",
    "--qmt-dir", "D:\QMT交易端模拟",
    "--timeout", "300"
)
# 演练用 --deploy-dry-run（只校验/构建，既不落盘也不重启）：
# 不能用 --no-restart —— 那会写新产物进客户端 python\ 目录但不重启加载，
# 造成「盘上文件 TAG ≠ 运行中代码」的失真状态。
if ($Rehearsal) { $loopArgs += "--deploy-dry-run" }
$out = & $py @loopArgs 2>&1
$rc = $LASTEXITCODE
$out | ForEach-Object { Add-Content -Path $log -Value "$_" -Encoding UTF8 }
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] qmt_nightly loop exit=$rc" -Encoding UTF8

# ---- 结论回显（供 agent 直接读日志尾部定位）----
# 注意：UTF-8 控制台编码必须保持到这里，否则下面这次 python 输出又被当 GBK 解 → 中文乱码。
# 演练（-Rehearsal）不写 latest.json（qmt_nightly.py 里已拦），所以这里演练时要改读
# 最新的 REHEARSAL_*.json 并明确标 REHEARSAL，否则日志会拿上一轮真实报告冒充本轮结论。
$echoFile = "$reportDir/latest.json"
$echoTag = ""
if ($Rehearsal) {
    $rh = Get-ChildItem -Path $reportDir -Filter "REHEARSAL_*.json" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($rh) { $echoFile = $rh.FullName; $echoTag = " [REHEARSAL 演练，不计 PASS]" }
}
if (Test-Path $echoFile) {
    $v = & $py -c "import json,sys; f=open(sys.argv[1],encoding='utf-8'); d=json.load(f); f.close(); print('verdict=%s deploy=%s tag=%s' % (d['verdict'], (d.get('deploy') or {}).get('result'), (d.get('deploy') or {}).get('tag')))" "$echoFile" 2>&1
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] $(Split-Path $echoFile -Leaf) -> $v$echoTag" -Encoding UTF8
}
try { [Console]::OutputEncoding = $oldEnc } catch { }
Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] ==== QMT 夜间闭环结束（部署锁已释放）====" -Encoding UTF8
exit $rc
