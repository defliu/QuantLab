# Factor Mining S1+S2 weekend batch (QuantLab)
# Runs latest unrun candidate batch -> IC screen report -> Feishu summary.
# Owner: QuantLab schtasks (unique). Script-first: core is pure Python.
$ErrorActionPreference = "Stop"
$py = "D:\Python311\python.exe"
$dir = "D:\QuantLab\research_factor_mining"
$logDir = "D:\QuantLab\data\schedules"
$log = Join-Path $logDir ("factor_screen_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".log")
$pyPush = "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])"

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start factor screen weekend batch" -Encoding UTF8

& $py "$dir\run_latest_batch.py" *>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] run_latest_batch exit=$code" -Encoding UTF8

if ($code -ne 0) {
    $msg = "[因子挖掘] S1/S2 周末批失败 exit=$code 详见 $log"
    $oPush = (& $py -c $pyPush "$msg" 2>&1 | Out-String)
    Add-Content -Path $log -Value $oPush -Encoding UTF8
    exit $code
}

# Summary card: read latest report funnel
$summary = ""
$latestYaml = Get-ChildItem "$dir\candidates\batch_*.yaml" | Sort-Object LastWriteTime | Select-Object -Last 1
if ($latestYaml) {
    $bid = $latestYaml.BaseName -replace "^batch_", ""
    $rp = "$dir\ic_screen\batch_${bid}_report.md"
    if (Test-Path $rp) {
        $funnel = Select-String -Path $rp -Pattern "^- 登记:|^- S1 晋级:|^- S2 通过:" | ForEach-Object { $_.Line.Trim() }
        $summary = "批次 $bid`n" + ($funnel -join "`n")
    }
}
if (-not $summary) { $summary = "无新批次或报告缺失，见 $log" }
$msg = "[因子挖掘] S1/S2 周末批完成 exit=0`n$summary"
$oPush = (& $py -c $pyPush "$msg" 2>&1 | Out-String)
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] done" -Encoding UTF8
exit 0
