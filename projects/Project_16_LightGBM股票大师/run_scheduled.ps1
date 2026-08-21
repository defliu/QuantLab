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

# ---- Python 解释器（优先 PATH，找不到则用 TraeWork 内置）----
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
    $py = "C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
}

function Log($m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding UTF8
}

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
            Run-Py "build_features_v2.py"
            Run-Py "train_optuna.py --panel v3 --n-trials 20"
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
