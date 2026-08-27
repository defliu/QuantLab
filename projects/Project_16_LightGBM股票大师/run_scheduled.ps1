# coding: utf-8
# ============================================================
# 项目16 LightGBM股票大师 · 定时任务调度器
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode daily
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode monitor
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode retrain
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode factor
# 日志输出到: <项目>/data/schedules/<mode>_<时间戳>.log
# 各模式对应 WORKFLOW_DEPLOY.md 第九节规划:
#   daily   = 盘后增量+选股 (交易日 16:30)
#   monitor = 午间盯盘快照   (交易日 13:00)
#   retrain = 周更重训       (周一 17:00)
#   factor  = 月度因子监控   (每月1日 20:00)
# ============================================================
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("daily", "monitor", "retrain", "factor")]
    [string]$Mode
)
$ErrorActionPreference = "Continue"

# ---- 项目路径 ----
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataDir = Join-Path $proj "data"
$logDir = Join-Path $dataDir "schedules"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "$Mode`_$stamp.log"

# ---- 日志函数（先定义，供后续 Python 选择段使用）----
function Log($m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

# ---- Python 解释器（优先 PATH，但必须带 numpy/lightgbm；否则回退内置）----
$FALLBACK_PY = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
function Get-GoodPy {
    $cands = @()
    $p = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($p) { $cands += $p }
    if (Test-Path $FALLBACK_PY) { $cands += $FALLBACK_PY }
    foreach ($c in $cands) {
        try {
            $v = & $c -c "import numpy, lightgbm; print('OK')" 2>$null | Select-Object -Last 1
            if ($v -eq 'OK') { return $c }
        } catch { }
    }
    return $null
}
$py = Get-GoodPy
if (-not $py) {
    Log "!! 未找到带 numpy/lightgbm 的 Python，定时任务无法运行"
    exit 1
}
Log "使用 Python: $py"

function Run-Py($argsStr) {
    Log ">> python $argsStr"
    $pyArgs = @($argsStr -split '\s+')
    & $py $pyArgs 2>&1 | ForEach-Object { Log $_ }
    Log ">> exit=$LASTEXITCODE"
}

Push-Location $proj
try {
    # ---- A股交易日判断（锁死交易时间：周末/法定节假日不执行任何模式）----
    & $py "is_trade_day.py" --json *> $null
    $tradeDay = ($LASTEXITCODE -eq 0)
    if (-not $tradeDay) {
        $tdReason = (& $py "is_trade_day.py" | Select-Object -Last 1)
        Log "非 A 股交易日，跳过 [$Mode]（$tdReason）"
        exit 0
    }
    Log "确认 A 股交易日，执行 [$Mode]"

    switch ($Mode) {
        "daily" {
            Log "[盘后链路] 开始"
            Run-Py "xtdata_update.py"

            # 交易日判断：meta.updated_at 是否为今天
            $metaPath = Join-Path $proj "data_live\update_meta.json"
            $fresh = $false
            if (Test-Path $metaPath) {
                $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
                $upd = [datetime]::Parse($meta.updated_at)
                if ($upd.Date -eq (Get-Date).Date) { $fresh = $true }
            }
            if ($fresh) {
                Log "今天增量更新成功，继续 merge + deploy"
                $latest = (& $py -c "import pandas as pd; d=pd.read_parquet(r'data_live\incremental_daily.parquet', columns=['trade_date']); print(d['trade_date'].max().strftime('%Y-%m-%d'))" | Select-Object -Last 1).Trim()
                Log "最新交易日: $latest"
                if ($latest) {
                    Run-Py "merge_live_features.py --date $latest"
                    Run-Py "deploy_predict.py --model v3 --top-k 10"
                } else {
                    Log "未能确定最新交易日，跳过 merge/deploy"
                }
            } else {
                Log "今天无增量更新（非交易日或更新失败），跳过 merge/deploy"
            }
        }
        "monitor" {
            Log "[盯盘快照] 开始（--auto-sell: 触发信号自动卖出）"
            Run-Py "qmt_monitor.py --once --auto-sell"
        }
        "retrain" {
            Log "[周更重训] 开始（约1小时）"
            # 0) 重训前备份当前正式模型，防止覆盖（SERVER_DEPLOY.md 六、安全与备份 第2条要求）
            $formalModel = "D:/QuantLab/models/lgb_model_v3.txt"
            if (Test-Path $formalModel) {
                $modelBak = Join-Path $proj ("versions\models\lgb_model_v3_pre_retrain_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".txt")
                Copy-Item $formalModel $modelBak -Force
                Log "已备份正式模型 -> $modelBak"
            }
            Run-Py "build_features_v2.py"
            # 写入带日期后缀的候选模型（lgb_model_v3_retrain_YYYYMMDD.txt），不覆盖正式模型 lgb_model_v3.txt
            $retrainTag = "_retrain_" + (Get-Date -Format 'yyyyMMdd')
            Run-Py "train_optuna.py --panel-file data/feature_panel_v3.parquet --meta-file data/features_v3.json --n-trials 20 --model-tag $retrainTag"
        }
        "factor" {
            Log "[月度因子监控] 开始"
            Run-Py "factor_ic_monitor.py"
        }
    }
    Log "[$Mode] 完成"
}
catch {
    Log "[$Mode] 异常: $_"
}
Pop-Location
