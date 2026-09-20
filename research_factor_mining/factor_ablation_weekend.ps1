# Factor Mining risk-exclude ablation weekend (QuantLab)
# Runs ATR-domain exclusion ablation -> report -> Feishu summary.
# Owner: QuantLab schtasks (unique). Script-first: core is pure Python.
$ErrorActionPreference = "Stop"
$py = "D:\Python311\python.exe"
$dir = "D:\QuantLab\research_factor_mining"
$logDir = "D:\QuantLab\data\schedules"
$log = Join-Path $logDir ("factor_ablation_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".log")
$pyPush = "import sys; sys.path.insert(0, r'D:\QuantLab\projects\Project_16_LightGBM股票大师'); from qmt_bridge_client import notify_feishu; notify_feishu(sys.argv[1])"

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start factor ablation" -Encoding UTF8

& $py "$dir\run_exclude_ablation.py" *>&1 | Tee-Object -FilePath $log
$code = $LASTEXITCODE
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] run_exclude_ablation exit=$code" -Encoding UTF8

if ($code -ne 0) {
    $msg = "[因子挖掘] 排雷 ablation 失败 exit=$code 详见 $log"
    $oPush = (& $py -c $pyPush "$msg" 2>&1 | Out-String)
    Add-Content -Path $log -Value $oPush -Encoding UTF8
    exit $code
}

# Summary: config table lines
$summary = ""
$rp = "$dir\exclude_ablation_report.md"
if (Test-Path $rp) {
    $rows = Select-String -Path $rp -Pattern "^\| (基线|排除|组合)" | ForEach-Object { $_.Line }
    $summary = ($rows | Select-Object -First 8) -join "`n"
}
if (-not $summary) { $summary = "报告缺失，见 $log" }
$msg = "[因子挖掘] 排雷 ablation 完成 exit=0`n$summary"
$oPush = (& $py -c $pyPush "$msg" 2>&1 | Out-String)
Add-Content -Path $log -Value $oPush -Encoding UTF8
Add-Content -Path $log -Value "[$(Get-Date -Format 'HH:mm:ss')] done" -Encoding UTF8
exit 0
