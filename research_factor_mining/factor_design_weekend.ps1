# Factor Mining candidate-design weekend (QuantLab) - delegate DE for next batch
# Friday 20:30: generate prompt -> deveco run (DE reads prompt, web-search, designs next batch)
# -> timeout guard (50min) + Feishu alert. Owner: QuantLab schtasks (unique).
$ErrorActionPreference = "Continue"
$py = "D:\Python311\python.exe"
$schedDir = "D:\QuantLab\data\schedules"
$fmDir = "D:\QuantLab\research_factor_mining"
# 直接用原生 exe（路径无空格），不经 .cmd shim：2026-09-25~10-30 连续 6 次失败根因是
# cmd.exe /c "带空格路径" 被剥引号 -> 'C:\...\deveco.cmd" run "读取任务书' 不是内部或外部命令
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\node_modules\@deveco\deveco-code\bin\deveco.exe"
$date = Get-Date -Format yyyyMMdd
$log = Join-Path $schedDir ("factor_design_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".log")
$taskFile = Join-Path $schedDir ("de_factor_mining_r3_prompt_" + $date + ".txt")
$outFile = Join-Path $schedDir ("de_factor_mining_r3_" + $date + ".md")
$pyPush = "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])"

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start factor design (DE delegation)" -Encoding UTF8

# Latest batch results as input reference
$latestRef = ""
$latestYaml = Get-ChildItem "$fmDir\candidates\batch_*.yaml" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
if ($latestYaml) {
    $bid = $latestYaml.BaseName -replace "^batch_", ""
    $rp = "$fmDir\ic_screen\batch_${bid}_report.md"
    if (Test-Path $rp) { $latestRef = $rp }
}

$prompt = @"
你是 QuantLab 独立研究伙伴（DE R3：扩源候选设计，可上网检索，只读 + 只写指定输出文件）。

工作目录：D:\QuantLab。
允许读取：D:\QuantLab\data\schedules\de_factor_mining_r2_20260916.md（DE R2 设计基线）、
$latestRef（最新批次结果）、
D:\QuantLab\research_factor_mining\{run_screen.py, factor_defs_r2.py, candidates\*.yaml, exclude_diag_report.md, exclude_robustness_audit.md}、
（注：exclude_ablation_report.md 的 +2.65pp 已证伪，不要读它作前提；以 exclude_robustness_audit.md 为准）
D:\QuantLab\研究总览与路线图.md（第二节 IC 实证）、D:\QuantLab\research_audit\factor_health_报告.md。
允许上网检索：搜索 A 股小市值横截面因子的新构造方向、可落地的另类数据字段、与已证伪方向的区分。

背景：R2 批已跑（漏斗 40->S1 17->S2 通过 10 全为负向因子），排雷五分位诊断确认 PX_AMT_RATIO_20D/PX_VOL_STD20/PX_LISTED_NEW 尾部驱动。
【重要·已证伪，勿再引用旧数】旧 exclude_ablation_report.md 里的「排除 PX_AMT_RATIO_20D +2.65pp（最强）」不成立——
2026-09-17 真实约束回测（amt_exclude_engine_结题.md）base 与 excl 逐位一致、+0.00pp；2026-11-01 复核（exclude_robustness_audit.md）
在共同网格 + 真实日历年化下只剩 +0.40pp，且增益来自个别换名而非系统性排雷，样本外仅 +0.89pp。PX_LISTED_NEW 排除恒为 +0.00pp。
结论：R2 的 4 个 PX 排雷因子对 ATR 载体无可落地增益，R3 不要再沿「排雷 ablation 增益」这条线续做。
已证伪/确认负向：北向持股水平、龙虎榜上榜频率/成交占比、研报覆盖计数、财务单科目（BP 外基本面因子弱）。

任务：
1. 上网检索并综述：①A 股小市值"成交活跃度/换手异常"类因子的构造细节与实证 ②研报评级/目标价修正通道的 A 股实证 ③龙虎榜机构席位的短期/长期效应 ④其他小盘域未试过的另类维度（股东户数、大宗交易、融资融券余额、股权质押、限售解禁、商誉减值预告等，点名 Tushare/本机数据可得性）。
2. 设计 R3 批次候选（40-60 条，五族分置：A-成交活跃度深化 / B-研报修正细化 / C-龙虎榜席位深化 / D-新增另类维度 / E-财务背离续测），每条给 id/name/direction/source/fn/logic/direction_hint，直接给可粘贴 YAML 块（batch_r3_<date>.yaml 格式，fn 遵循 factor_defs 签名）。
3. 给出 R3 存活预期与防过拟合要点（吸取 R2 教训：负向因子为主，价值出口=排雷；PX_MAX 在低波池内是反例，不要再登记同类）。

写作要求：输出文件 D:\QuantLab\data\schedules\de_factor_mining_r3_$date.md（唯一允许写入）；数字必须来自白名单或实检索来源，禁止编造；结论敢下判断；完成后直接结束不得询问。
"@

Set-Content -Path $taskFile -Value $prompt -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] prompt: $taskFile" -Encoding UTF8

# --auto 是 0.2.4 文档化的等价开关（旧 --dangerously-skip-permissions 仍被静默接受，但不再依赖）
$argLine = "run `"读取任务书 $taskFile，严格按任务书执行：可上网、只读白名单、只写输出文件 de_factor_mining_r3_$date.md，完成后直接结束不得询问`" --auto --dir D:\QuantLab --format json"
$outLog = Join-Path $schedDir "factor_design_de_out.log"
$errLog = Join-Path $schedDir "factor_design_de_err.log"

# 不经 cmd.exe：PowerShell 会把数组参数按空格拼回一条命令行，带引号的路径会被剥引号截断
# 也不用 Start-Process -PassThru：本机实测「无 -Wait 的 PassThru 对象 ExitCode 恒为 $null」
#（2026-11-01 探针 V1/V2：cmd /c exit 3 与 deveco --version 都读不到码；加 -Wait 虽可读但会阻塞，
#  失去 50 分钟超时轮询能力）。改用 .NET Process 直连：V3=3 / V4=0 均正确。
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $deveco
$psi.Arguments = $argLine
$psi.WorkingDirectory = "D:\QuantLab"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi

$deadline = (Get-Date).AddMinutes(50)
$done = $false
$code = 1
$launched = $false
try { $launched = $proc.Start() } catch {
    $code = 127
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE LAUNCH FAILED: $($_.Exception.Message)" -Encoding UTF8
}
if ($launched) {
    # 异步排空管道：deveco --format json 输出很大，同步 ReadToEnd 会与超时轮询互斥死锁
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE pid=$($proc.Id) launched" -Encoding UTF8

    while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
        Start-Sleep -Seconds 30
        if (Test-Path $outFile) {
            $sz = (Get-Item $outFile).Length
            $mt = (Get-Item $outFile).LastWriteTime
            if ($sz -gt 2000 -and ((Get-Date) - $mt).TotalMinutes -lt 10) { $done = $true; break }
        }
    }
    $stillRunning = -not $proc.HasExited
    if ($stillRunning) {
        # 产物已落盘但进程还没退出（deveco 收尾/写 JSON 慢）时也必须杀残留，
        # 但绝不能因此判超时失败——否则「输出已落盘」被 124 覆盖，白跑一轮。
        try { $proc.Kill() } catch { }
    }
    $timedOut = $stillRunning -and (-not $done)
    if ($timedOut) {
        $code = 124
        Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE TIMEOUT killed (50min)" -Encoding UTF8
    } elseif ($stillRunning) {
        Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE output landed, killed leftover pid=$($proc.Id)" -Encoding UTF8
    }

    # 无参 WaitForExit 会等异步流排空；必须在取 ExitCode 之前调用
    try { $proc.WaitForExit() } catch { }
    $enc = New-Object System.Text.UTF8Encoding($false)
    try { [System.IO.File]::WriteAllText($outLog, $outTask.Result, $enc) } catch { }
    try { [System.IO.File]::WriteAllText($errLog, $errTask.Result, $enc) } catch { }

    if (-not $timedOut) {
        # 进程在首次 30s 轮询前就已退出时 $done 仍为 false，这里必须复判产物
        if (-not $done -and (Test-Path $outFile)) {
            $sz = (Get-Item $outFile).Length
            $mt = (Get-Item $outFile).LastWriteTime
            if ($sz -gt 2000 -and ((Get-Date) - $mt).TotalMinutes -lt 10) { $done = $true }
        }
        $ec = $null
        try { $ec = $proc.ExitCode } catch { }
        if ($done) {
            $code = 0
            Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE output landed (exitcode raw=$ec)" -Encoding UTF8
        } elseif ($null -eq $ec) {
            # 拿不到码又不能证明产物落盘 => 判失败（绝不 exit $null 被当成 0）
            $code = 1
            Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE exit code UNAVAILABLE and no output" -Encoding UTF8
        } else {
            $code = [int]$ec
            Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE exit=$code (no output file)" -Encoding UTF8
        }
    }
}

if ($code -ne 0 -or -not (Test-Path $outFile)) {
    # 「进程 exit 0 但产物没落盘」也必须判失败：否则下面 exit 0 又变成假成功
    if ($code -eq 0) { $code = 2 }
    $msg = "[因子挖掘] 扩源候选设计(DE) 异常 exit=$code 输出未落盘 详见 $log"
    $oPush = (& $py -c $pyPush "$msg" 2>&1 | Out-String)
    Add-Content -Path $log -Value $oPush -Encoding UTF8
    exit $code
}
$msg = "[因子挖掘] 扩源候选设计(DE) 完成 exit=0`nR3 报告: $outFile`nR3 YAML: $fmDir\candidates\batch_r3_$date.yaml（如产出）"
$oPush = (& $py -c $pyPush "$msg" 2>&1 | Out-String)
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] done" -Encoding UTF8
exit 0
