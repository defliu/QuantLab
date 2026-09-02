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
# Python 子进程 stdout 统一 UTF-8，防 GBK 控制台对 ✅ 等字符 UnicodeEncodeError（2026-08-28 daily 链路 3 处崩溃根因）
$env:PYTHONIOENCODING = "utf-8"
# 配套：PowerShell 侧解码也必须 UTF-8，否则 Run-Py 用管道把 Python 的 UTF-8 输出交给 Log 时
# 会按 GBK（中文 Windows 默认 [Console]::OutputEncoding）解码，中文日志落成乱码
# （2026-08-31 发现：retrain_20260831 日志全篇乱码，导致周更结果不可读，只能靠猜）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

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
    # ---- 确保飞书卡片回调服务存活（长驻守护；未运行则拉起，已运行则跳过）----
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $proj "start_card_webhook.ps1")

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
                $latest = (& $py "get_latest_incr_date.py" | Select-Object -Last 1).Trim()
                Log "最新交易日: $latest"
                if ($latest) {
                    Run-Py "merge_live_features.py --date $latest"
                    # ---- 每日刷新 v3 面板（2026-09-01 落地，T-20260901-001）：合并增量重建 v2 + 切片 v3 ----
                    # 背景：此前面板只在周更重训(refresh_panel_v3)时更新，周中面板落后（如 9/1 仍用 8/28）。
                    # 现在每天增量入库后刷新面板到最新交易日，次日 09:15 候选自动用最新数据；约 10 分钟。
                    # 模型保持周更 promote（1 天增量分布差异可接受），verify 已放宽 ≤7 自然日差异不告警。
                    Run-Py "refresh_panel_v3.py"
                    Run-Py "deploy_predict.py --model v3 --top-k 10"
                } else {
                    Log "未能确定最新交易日，跳过 merge/deploy"
                }
            } else {
                Log "今天无增量更新（非交易日或更新失败），跳过 merge/deploy"
            }
            # ---- 盘后交易对账（P0，2026-08-27 加入）：本地成交记录 vs QMT 实测，异常告警 ----
            # 应对 8/24 超买 939万 / 7,600股未入账 / 601865 未成交误记等数据与风控事故；
            # 每次盘后核对持仓一致性 + 已实现盈亏 + 异常检测，不一致或异常时退出码非 0 并在日志告警。
            Run-Py "reconcile_trades.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [对账] 持仓不一致或成交记录异常，需人工核查（见 data/reconcile_<date>.md）"
            } else {
                Log "[对账] 持仓一致，无异常"
            }
            # ---- 模型-面板同步校验（T-20260828-005 固化）：面板重建后若正式模型未同步 promote 则告警 ----
            # 防"新面板喂旧模型"=训练/推理分布不一致（8/14 冻结面板教训）；retrain 后 promote 前会在此报警。
            Run-Py "verify_model_panel_sync.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [模型-面板同步] 面板与正式模型版本不一致，需重训+promote 或检查绑定记录（data/model_panel_binding.json）"
            } else {
                Log "[模型-面板同步] 面板与正式模型同版，OK"
            }
        }
        "monitor" {
            Log "[盯盘快照] 开始（--auto-sell: 触发信号自动卖出）"
            Run-Py "qmt_monitor.py --once --auto-sell"
        }
        "retrain" {
            Log "[周更重训] 开始（约1.5-2小时，含 G2 模型重训）"
            # 0) 重训前备份当前正式模型，防止覆盖（SERVER_DEPLOY.md 六、安全与备份 第2条要求）
            $formalModel = "D:/QuantLab/models/lgb_model_v3.txt"
            if (Test-Path $formalModel) {
                $modelBak = Join-Path $proj ("versions\models\lgb_model_v3_pre_retrain_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".txt")
                Copy-Item $formalModel $modelBak -Force
                Log "已备份正式模型 -> $modelBak"
            }
            Run-Py "refresh_panel_v3.py"
            # 写入带日期后缀的候选模型（lgb_model_v3_retrain_YYYYMMDD.txt），不覆盖正式模型 lgb_model_v3.txt
            $retrainTag = "_retrain_" + (Get-Date -Format 'yyyyMMdd')
            Run-Py "train_optuna.py --panel-file data/feature_panel_v3.parquet --meta-file data/features_v3.json --n-trials 20 --model-tag $retrainTag"
            # ---- 条件式自动上线（2026-08-31 立）：门禁全过才 promote，任一不过则拒绝上线并告警 ----
            # 背景：promote_model.py 是唯一写生产模型的入口且默认交互确认，无人值守的 retrain 调不动它，
            #       导致「面板已更新、正式模型未同步」，若无人接管次日 09:15 会旧模型吃新面板。
            # 机制：auto_promote.py 把人工拍板编码为 G0~G6 门禁（IC 下限/不退步/ICIR/分位方向/新面板），
            #       全过才自动 promote；任一不过则 exit 1，正式模型保持不变，交人工介入。
            $candModel = "D:/QuantLab/models/lgb_model_v3$retrainTag.txt"
            if (Test-Path $candModel) {
                Run-Py "auto_promote.py --candidate $candModel --yes"
                if ($LASTEXITCODE -ne 0) {
                    Log "!! [自动上线] 门禁未通过，候选未上线 —— 正式模型保持不变，需人工核对 data/optuna_report.json 与上方 FAIL 项"
                } else {
                    Log "[自动上线] 门禁通过，候选已提升为正式模型"
                }
            } else {
                Log "!! [自动上线] 未找到候选模型 $candModel，跳过（重训可能失败）"
            }
            # 复核：面板与正式模型是否同版（自动上线成功则应 exit 0）
            Run-Py "verify_model_panel_sync.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [模型-面板同步] 面板与正式模型版本仍不一致，需人工介入（data/model_panel_binding.json）"
            } else {
                Log "[模型-面板同步] 面板与正式模型同版，OK"
            }
            # ---- G2 模型周更重训（2026-09-01 补：g2_strong_real 生产主模型，V2.0 审计认定）----
            # 背景：此前 g2_strong_real 只在 08-25 通宵研究训练一次后冻结，周更只 promote V1.3；
            # train_g2.py 构建 43 特征面板 + 训练 + 门禁（IC>=max(0.03,当前live)）+ 提升 live 指针，
            # deploy_predict_g2 / build_g2_daily 自动读最新 live（data/g2_live_model.json）。
            $g2LivePointer = Join-Path $proj "data\g2_live_model.json"
            $g2Cur = $null
            if (Test-Path $g2LivePointer) {
                try { $g2Cur = (Get-Content $g2LivePointer -Raw | ConvertFrom-Json).model_path } catch { }
            }
            if ($g2Cur -and (Test-Path $g2Cur)) {
                $g2LiveBak = Join-Path $proj ("versions\models\lgb_model_v3_g2_strong_real_pre_retrain_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".txt")
                Copy-Item $g2Cur $g2LiveBak -Force
                Log "已备份 G2 live 模型 -> $g2LiveBak"
            } else {
                Log "G2 live 指针缺失，跳过备份（train_g2 失败时回退 08-25 初始 live）"
            }
            Run-Py "train_g2.py --promote"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [G2重训] 门禁未过或训练失败，G2 live 保持不变，需人工核查（data/g2_live_model.json）"
            } else {
                Log "[G2重训] 门禁通过，G2 live 已更新"
            }
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
