# Factor Mining candidate-design weekend (QuantLab) - delegate DE for next batch
# Friday 20:30: generate prompt -> deveco run (DE reads prompt, web-search, designs next batch)
# -> timeout guard (50min) + Feishu alert. Owner: QuantLab schtasks (unique).
$ErrorActionPreference = "Continue"
$py = "D:\Python311\python.exe"
$schedDir = "D:\QuantLab\data\schedules"
$fmDir = "D:\QuantLab\research_factor_mining"
$deveco = "C:\Users\Administrator\AppData\Roaming\npm\deveco.cmd"
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
D:\QuantLab\research_factor_mining\{run_screen.py, factor_defs_r2.py, candidates\*.yaml, exclude_diag_report.md, exclude_ablation_report.md}、
D:\QuantLab\研究总览与路线图.md（第二节 IC 实证）、D:\QuantLab\research_audit\factor_health_报告.md。
允许上网检索：搜索 A 股小市值横截面因子的新构造方向、可落地的另类数据字段、与已证伪方向的区分。

背景：R2 批已跑（漏斗 40->S1 17->S2 通过 10 全为负向因子），排雷五分位诊断确认 PX_AMT_RATIO_20D/PX_VOL_STD20/PX_LISTED_NEW 尾部驱动，
ablation 首跑：排除 PX_AMT_RATIO_20D +2.65pp（最强）、PX_VOL_STD20 +1.32pp、PX_LISTED_NEW +1.00pp、PX_MAX20 -1.09pp（低波池内反例）。
已证伪/确认负向：北向持股水平、龙虎榜上榜频率/成交占比、研报覆盖计数、财务单科目（BP 外基本面因子弱）。

任务：
1. 上网检索并综述：①A 股小市值"成交活跃度/换手异常"类因子的构造细节与实证 ②研报评级/目标价修正通道的 A 股实证 ③龙虎榜机构席位的短期/长期效应 ④其他小盘域未试过的另类维度（股东户数、大宗交易、融资融券余额、股权质押、限售解禁、商誉减值预告等，点名 Tushare/本机数据可得性）。
2. 设计 R3 批次候选（40-60 条，五族分置：A-成交活跃度深化 / B-研报修正细化 / C-龙虎榜席位深化 / D-新增另类维度 / E-财务背离续测），每条给 id/name/direction/source/fn/logic/direction_hint，直接给可粘贴 YAML 块（batch_r3_<date>.yaml 格式，fn 遵循 factor_defs 签名）。
3. 给出 R3 存活预期与防过拟合要点（吸取 R2 教训：负向因子为主，价值出口=排雷；PX_MAX 在低波池内是反例，不要再登记同类）。

写作要求：输出文件 D:\QuantLab\data\schedules\de_factor_mining_r3_$date.md（唯一允许写入）；数字必须来自白名单或实检索来源，禁止编造；结论敢下判断；完成后直接结束不得询问。
"@

Set-Content -Path $taskFile -Value $prompt -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] prompt: $taskFile" -Encoding UTF8

$argLine = "run `"读取任务书 $taskFile，严格按任务书执行：可上网、只读白名单、只写输出文件 de_factor_mining_r3_$date.md，完成后直接结束不得询问`" --dangerously-skip-permissions --dir D:\QuantLab --format json"
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$deveco`"", $argLine -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $schedDir "factor_design_de_out.log") -RedirectStandardError (Join-Path $schedDir "factor_design_de_err.log")

$deadline = (Get-Date).AddMinutes(50)
$done = $false
while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
    Start-Sleep -Seconds 30
    if (Test-Path $outFile) {
        $sz = (Get-Item $outFile).Length
        $mt = (Get-Item $outFile).LastWriteTime
        if ($sz -gt 2000 -and ((Get-Date) - $mt).TotalMinutes -lt 10) { $done = $true; break }
    }
}
if (-not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    $code = 124
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE TIMEOUT killed" -Encoding UTF8
} elseif ($done) {
    $code = 0
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE output landed" -Encoding UTF8
} else {
    $code = $proc.ExitCode
    Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] DE exit=$code" -Encoding UTF8
}

if ($code -ne 0 -or -not (Test-Path $outFile)) {
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
