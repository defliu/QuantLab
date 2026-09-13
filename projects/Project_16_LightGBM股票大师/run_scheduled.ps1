# coding: utf-8
# ============================================================
# 项目16 LightGBM股票大师 · 定时任务调度器
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode daily
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode monitor           # 只读监控（默认，仅预警）
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode monitor -AutoSell # 外部自动卖出（与内置风控双跑，谨慎）
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
    [string]$Mode,
    [switch]$AutoSell
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
                    # ---- 每日刷新 enh 面板（T-20260912-001）：33 特征慢变量（train_g2 / build_g2_daily 消费）----
                    # 背景：enh 面板此前无 writer，冻结在 8/14，实盘行业动量/换手排名/事件特征停摆一月。
                    Run-Py "refresh_panel_enh.py"
                    if ($LASTEXITCODE -ne 0) {
                        Log "!! [enh面板] refresh_panel_enh 失败，G2 慢变量沿用旧面板（asof 落后），需核查"
                    }
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
            # ---- 资金池滚动（DE 体检 P1-2，2026-09-11）：初始+已实现+浮盈 → strategy_capital.json ----
            # 历史：strategy_capital.py 无调度调用 → 资金池恒 95,897.60 超配；对账后更新保证买入预算真实。
            Run-Py "strategy_capital.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [资金池] strategy_capital.py 更新失败，需人工核查（data/strategy_capital.json）"
            } else {
                Log "[资金池] strategy_capital.json 已按当日已实现+浮盈滚动"
            }
            # ---- 模型-面板同步校验（T-20260828-005 固化）：面板重建后若正式模型未同步 promote 则告警 ----
            # 防"新面板喂旧模型"=训练/推理分布不一致（8/14 冻结面板教训）；retrain 后 promote 前会在此报警。
            Run-Py "verify_model_panel_sync.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [模型-面板同步] 面板与正式模型版本不一致，需重训+promote 或检查绑定记录（data/model_panel_binding.json）"
            } else {
                Log "[模型-面板同步] 面板与正式模型同版，OK"
            }
            # ---- G4 观察期回滚检查（T-20260910-005 固化，只读→2026-09-13 M3 深度劣化自动回退）：每日盘后检查 live 模型观察期 ----
            # 背景：rollback_check_g2.py 此前无任何调度调用（纯手动），G2 观测期回滚永不触发。
            # 2026-09-13 起 --auto：深度劣化（劣于基线 >0.05pp）自动回退 + 飞书；轻度劣化仍 exit 2 人工确认。
            Run-Py "rollback_check_g2.py --auto"
            if ($LASTEXITCODE -eq 2) {
                Log "!! [G4观察期] 观察期结束且前向轻度劣于基线 —— 建议人工回退（python rollback_check_g2.py --apply；深度劣化已自动处理）"
            } elseif ($LASTEXITCODE -eq 0) {
                Log "[G4观察期] 无待观察 trial / 观察期通过 / 深度劣化已自动回退，OK"
            } else {
                Log "[G4观察期] 检查跳过（exit=$LASTEXITCODE，无 trial 字段或数据不足）"
            }
        }
        "monitor" {
            # 2026-09-09 起默认只读（仅预警不自动卖出）：V1.3 内置 tick 风控（QMT 内置运行）已接管自动卖出，
            # 外部 monitor 若再带 --auto-sell 会与内置风控双跑重复卖出（2026-09-09 300413 双挂单教训）。
            # 如需恢复外部自动卖出，显式传 -AutoSell 开关。
            if ($AutoSell) {
                Log "[盯盘快照] 开始（--auto-sell: 触发信号自动卖出——与内置风控双跑，需谨慎）"
                Run-Py "qmt_monitor.py --once --auto-sell"
            } else {
                Log "[盯盘快照] 开始（只读监控: 仅预警不自动卖出，内置 tick 风控负责卖出）"
                Run-Py "qmt_monitor.py --once"
            }
        }
        "retrain" {
            Log "[周更重训] 开始（约1.5-2小时，含 G2 模型重训）"
            # -1) 特征健康周报前置（2026-09-13 立，T-20260913-001 F1）：逐特征近4周 IC/缺失率监控，
            #      ALERT 特征（IC趋零+缺失率>5%）飞书告警；仅告警不阻断训练（去留人工裁决）。
            #      先于面板刷新跑：用上周期数据评估（refresh 后跑等于看新面板，失去"训练前核查"意义）
            Run-Py "feature_health_weekly.py"
            if ($LASTEXITCODE -eq 2) {
                Log "!! [特征健康] 有 ALERT 特征（近4周IC趋零+缺失率>5%），已飞书告警——训练继续，特征去留待人工裁决"
            }
            # 0) 重训前备份当前正式模型，防止覆盖（SERVER_DEPLOY.md 六、安全与备份 第2条要求）
            $formalModel = "D:/QuantLab/models/lgb_model_v3.txt"
            if (Test-Path $formalModel) {
                $modelBak = Join-Path $proj ("versions\models\lgb_model_v3_pre_retrain_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".txt")
                Copy-Item $formalModel $modelBak -Force
                Log "已备份正式模型 -> $modelBak"
            }
            Run-Py "refresh_panel_v3.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [周更重训] refresh_panel_v3 失败（exit=$LASTEXITCODE）—— 面板未重建，跳过 V1.3 候选训练与自动上线（避免在旧面板上训练候选，模型与数据必须同版联动）；G2 重训使用独立面板，继续执行"
                $skipV13 = $true
            } else {
                $skipV13 = $false
            }
            # ---- 刷新 enh 面板（T-20260912-001）：train_g2 的 6 个慢变量来源，必须在 train_g2 之前 ----
            # 失败不跳过 G2 重训：旧 enh 面板若为近日刷新（daily 链维护）仅落后数日，可接受；
            # refresh_panel_enh 自带校验门禁（行数坍缩/口径漂移/负值回归），失败保留旧面板。
            Run-Py "refresh_panel_enh.py"
            if ($LASTEXITCODE -ne 0) {
                Log "!! [周更重训] refresh_panel_enh 失败（exit=$LASTEXITCODE）—— train_g2 将用旧 enh 面板（asof 落后天数见训练日志），需核查"
            }
            # ---- enh 面板新鲜度硬门禁（2026-09-13 立，T-20260913-001 F2）：绝对日历锚，>7 天阻断 G2 重训 ----
            # 背景：refresh_panel_enh 有相对门禁（vs merged），但若 daily 刷新链断流 N 天无人发现，
            #       train_g2 会用陈旧 enh 慢变量训练（asof 落后），越训越差。此门禁用"最近交易日"兜底。
            Run-Py "check_enh_freshness.py"
            if ($LASTEXITCODE -eq 2) {
                Log "!! [enh新鲜度] enh 面板落后 >7 天 —— 阻断 G2 重训（train_g2 跳过），先核查 refresh_panel_enh/daily 链"
                $skipG2 = $true
            } elseif ($LASTEXITCODE -eq 1) {
                # P2-3 修复：exit 1 = 数据缺失 fail-safe 放行（不是"新鲜"），须告警区分
                Log "!! [enh新鲜度] 无法判定（merged_daily_full 缺失，fail-safe 放行）——继续 G2 重训，但需核查数据链"
            } else {
                Log "[enh新鲜度] enh 面板新鲜，继续 G2 重训"
            }
            # 写入带日期后缀的候选模型（lgb_model_v3_retrain_YYYYMMDD.txt），不覆盖正式模型 lgb_model_v3.txt
            if (-not $skipV13) {
                $retrainTag = "_retrain_" + (Get-Date -Format 'yyyyMMdd')
                Run-Py "train_optuna.py --panel-file data/feature_panel_v3.parquet --meta-file data/features_v3.json --n-trials 20 --model-tag $retrainTag"
                # ---- 条件式自动上线（2026-08-31 立）：门禁全过才 promote，任一不过则拒绝上线并告警 ----
                # 背景：promote_model.py 是唯一写生产模型的入口且默认交互确认，无人值守的 retrain 调不动它，
                #       导致「面板已更新、正式模型未同步」，若无人接管次日 09:15 会旧模型吃新面板。
                # 机制：auto_promote.py 把人工拍板编码为 G0~G6 门禁（IC 下限/不退步/ICIR/分位方向/新面板），
                #       全过才自动 promote；任一不过则 exit 1，正式模型保持不变，交人工介入。
                # T-20260903 修复：G6 面板同步只在 promote 成功后做硬校验（确认绑定已更新）；
                #       门禁拒绝（坏模型被拦）属设计行为，不再触发面板同步硬校验，避免「拒绝被误报为系统故障」。
                $candModel = "D:/QuantLab/models/lgb_model_v3$retrainTag.txt"
                if (Test-Path $candModel) {
                    Run-Py "auto_promote.py --candidate $candModel --yes"
                    if ($LASTEXITCODE -ne 0) {
                        Log "!! [自动上线] 门禁未通过，候选未上线 —— 正式模型保持不变（G0~G6 拒绝坏模型属设计行为，非故障；G6 面板同步不拦截本次拒绝，需人工核对 data/optuna_report.json 与上方 FAIL 项）"
                        Run-Py "verify_model_panel_sync.py --warn-only"
                        Log "[模型-面板同步]（门禁拒绝后 warn-only，仅供信息，不阻断）"
                    } else {
                        Log "[自动上线] 模型层门禁（G0~G6）通过，候选已提升为正式模型"
                        # ---- 策略层回测门禁（T-20260910-003）：promote 后复核 V1.4 最优配置 ----
                        # V1.3 周更候选是 27 特征模型，用配置库 V1.4 最优配置（fixed/N10/红线58/TOP2/lite，
                        # 面板 v3_sc，已验证 27 特征可推理）对比候选与旧正式模型（同一面板，公平对比）。
                        # 候选（新正式模型）不得差于 promote 前的旧正式模型；FAIL 则回滚旧正式模型。
                        $v13Meta = Join-Path $proj "data\features_v3.json"
                        # 旧正式模型 = 本次 retrain 开头备份的 pre_retrain 文件（时间戳在 1.5-2 小时前，
                        # 不能重新 Get-Date——会生成不同时间戳导致 Test-Path 恒 False、门禁被永远跳过，T-20260910-005 修复）
                        $oldFormal = Get-ChildItem (Join-Path $proj "versions\models\lgb_model_v3_pre_retrain_*.txt") -ErrorAction SilentlyContinue |
                            Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { $_.FullName }
                        if (Test-Path $v13Meta -and (Test-Path $oldFormal)) {
                            Log "[策略层门禁] V1.4 最优配置（fixed/N10/红线58/TOP2+lite）候选 vs 旧正式 复核（约10分钟）..."
                            Run-Py "gate_strategy_layer.py --strategy V1.4 --candidate $candModel --meta $v13Meta --live $oldFormal --live-meta $v13Meta"
                            if ($LASTEXITCODE -eq 2) {
                                Log "!! [策略层门禁] FAIL —— 新正式模型在 V1.4 最优配置下差于旧模型，回滚旧正式模型（$oldFormal）"
                                Copy-Item $oldFormal $formalModel -Force
                                Log "[策略层门禁] 已回滚正式模型 -> $formalModel（旧模型 restore）"
                            } elseif ($LASTEXITCODE -eq 0) {
                                Log "[策略层门禁] PASS —— 新正式模型在 V1.4 最优配置下不差于旧模型，保留上线"
                            } else {
                                Log "!! [策略层门禁] 运行异常（exit=$LASTEXITCODE）—— 无法评估，需人工核查，新正式模型暂保留"
                            }
                        } else {
                            Log "!! [策略层门禁] 未找到 V1.3 meta 或旧模型备份（$v13Meta / $oldFormal），跳过策略层复核"
                        }
                        # 仅 promote 成功后做硬校验：绑定必须与面板同版（gap=0），失败说明绑定写入异常
                        Run-Py "verify_model_panel_sync.py"
                        if ($LASTEXITCODE -ne 0) {
                            Log "!! [模型-面板同步] promote 后绑定仍不一致 —— 绑定写入异常，需人工介入（data/model_panel_binding.json）"
                        } else {
                            Log "[模型-面板同步] 面板与正式模型同版，OK"
                        }
                    }
                } else {
                    Log "!! [自动上线] 未找到候选模型 $candModel，跳过（重训可能失败）"
                }
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
            if (-not $skipG2) {
                Run-Py "train_g2.py --promote"
                if ($LASTEXITCODE -ne 0) {
                    Log "!! [G2重训] 门禁未过或训练失败，G2 live 保持不变，需人工核查（data/g2_live_model.json）"
                } else {
                Log "[G2重训] 模型层门禁（G1-G3 strict）通过，G2 live 已更新"
                # ---- 策略层回测门禁（T-20260910-003）：promote 后立即用官方引擎复核 ----
                # 模型层门禁只保证 IC/尾部IC/Top2 模拟不退步，但 IC 与实盘收益隔着评分卡选股/过滤/出场，
                # 需在历史最优配置（G2=live_trail/N15/红线60/TOP2）下跑官方引擎回测，
                # 候选（新 live）不得差于 promote 前 live；FAIL 则自动回滚到 prev_model（train_g2 写入 trial.prev_model）。
                $g2New = $null
                if (Test-Path $g2LivePointer) {
                    try { $g2New = (Get-Content $g2LivePointer -Raw | ConvertFrom-Json).model_path } catch { }
                }
                if ($g2New -and (Test-Path $g2New)) {
                    # 新 live 的 meta：从模型文件名推导 features_v3_g2_strong_real_<date>.json
                    $g2Stamp = [regex]::Match($g2New, 'g2_strong_real_(\d{8})_').Groups[1].Value
                    $g2Meta = Join-Path $proj "data\features_v3_g2_strong_real_$g2Stamp.json"
                    if (Test-Path $g2Meta) {
                        Log "[策略层门禁] G2 最优配置（live_trail/N15/红线60/TOP2）候选 vs live 复核（约10分钟）..."
                        Run-Py "gate_strategy_layer.py --strategy G2 --candidate $g2New --meta $g2Meta --rollback"
                        if ($LASTEXITCODE -eq 0) {
                            Log "[策略层门禁] PASS —— 新 G2 live 在最优配置下不差于旧 live，保留上线"
                        } elseif ($LASTEXITCODE -eq 2) {
                            Log "!! [策略层门禁] FAIL —— 新 G2 live 在最优配置下差于旧 live，已自动回滚到 prev_model；需人工核查重训质量"
                        } else {
                            Log "!! [策略层门禁] 运行异常（exit=$LASTEXITCODE）—— 无法评估，需人工核查，G2 live 暂保持 promote 状态"
                        }
                    } else {
                        Log "!! [策略层门禁] 未找到新 live 对应 meta（$g2Meta），跳过策略层复核，需人工核查"
                    }
                } else {
                    Log "!! [策略层门禁] 无法读取 promote 后 G2 live 指针，跳过策略层复核"
                }
                }
            } else {
                Log "!! [G2重训] enh 面板陈旧被阻断（check_enh_freshness exit=2），本周围更未重训 G2——核查 refresh_panel_enh/daily 链后人工补跑"
            }
            # ---- 配置漂移复查（2026-09-13 立，T-20260913-001 P1）：G2 promote 成功后复查最优配置是否漂移 ----
            # 只登记不改实盘；3 个关键邻域点（N10/15/20 × live_trail）约 30 分钟。
            # P2-2 修复：gate 段可能已把 live 回滚（FAIL → prev_model），此时 $g2New 指向的是被拒候选，
            #            复查被拒候选无意义。故此处重读 live 指针，与 $g2New 比对，已回滚则跳过并说明。
            $g2CurFinal = $null
            if (Test-Path $g2LivePointer) {
                try { $g2CurFinal = (Get-Content $g2LivePointer -Raw | ConvertFrom-Json).model_path } catch { }
            }
            if (-not $g2New -or -not $g2CurFinal -or $g2New -ne $g2CurFinal) {
                Log "!! [配置漂移复查] G2 promote 被 gate 回滚或指针异常（live=$g2CurFinal），跳过本轮漂移复查（复查被拒候选无意义）"
            } elseif ($g2New -and (Test-Path $g2New)) {
                $g2Meta = $null
                try { $g2Meta = (Get-Content $g2LivePointer -Raw | ConvertFrom-Json).meta_path } catch { }
                if ($g2Meta -and (Test-Path $g2Meta)) {
                    Log "[配置漂移复查] G2 promote 成功，邻域小网格（N10/15/20 × live_trail，约30分钟）..."
                    Run-Py "config_drift_recheck.py --strategy G2 --model $g2New --meta $g2Meta"
                    if ($LASTEXITCODE -ne 0) {
                        Log "!! [配置漂移复查] 运行异常（exit=$LASTEXITCODE）——仅登记功能受影响，实盘配置未动"
                    }
                } else {
                    Log "!! [配置漂移复查] 新 live meta 读取失败，跳过"
                }
            } else {
                Log "[配置漂移复查] 本轮未 promote G2，跳过（仅 promote 后复查，省算力）"
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
