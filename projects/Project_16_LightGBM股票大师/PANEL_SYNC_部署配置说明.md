# 面板-模型同步防呆 · 部署配置说明

> 项目：Project\_16 LightGBM 股票大师
> 用途：新设备部署本策略时，按本文配置「面板更新 → 模型重训 → Promote 门禁 → 每日校验」全链路防呆，避免因面板停更导致评分重大错误。
> 适用版本：V2.0（2026-08-31 起，33 特征 + N3 标签）

## 一、为什么需要这道防呆

2026-08-14 起发生一起事故：生产特征面板 `feature_panel_v3.parquet` 停在 8/14 不再更新，但 `deploy_predict.py` 一直在用这份冻结面板打分。后果是模型输入特征连续多日完全不变，选股文件里同一只股票的 `model_prob` 跨日完全一致（如 000737 连续三天都是 0.6452），评分排名失真，但没有任何告警。直到 8/28 人工核验才定位根因：周更重训任务只重建了 v2 面板，v3 面板要靠一行手工命令切片，不在任何自动化里，8/20 后没人跑就停更了。

本次事故暴露出两个机制缺口，本文档对应的防呆措施即针对这两点：

1. **面板重建必须固化进自动化**，不能依赖手工命令。
2. **面板与正式模型必须同版联动**：面板更新后若模型未同步重训并 Promote，必须当场告警（"新面板喂旧模型"同样是评分失真）。

## 二、防呆机制总览

```
每周一 17:00  Quant_Weekly_Retrain
  └─ run_scheduled.ps1 -Mode retrain
       ├─ 备份当前正式模型 → versions/models/lgb_model_v3_pre_retrain_<stamp>.txt
       ├─ refresh_panel_v3.py   （合并主库+增量 → 重建 v2 → 切片 v3 面板）★面板重建自动化
       ├─ train_optuna.py       （产出候选模型 lgb_model_v3_retrain_<date>.txt，不覆盖正式模型）
       └─ verify_model_panel_sync.py  → 提示「面板已更新，请 promote 今日候选」★校验

Promote（人工确认，唯一写生产入口）
  └─ promote_model.py --model-file <候选> --suffix v3 --yes
       ├─ 校验特征数一致 → 备份旧正式模型 → 提升 → 重设只读
       └─ 写入绑定记录 data/model_panel_binding.json ★同版登记

每个交易日 16:30  Quant_Daily_Update
  └─ run_scheduled.ps1 -Mode daily
       ├─ xtdata_update → merge → deploy_predict（正常选股）
       ├─ reconcile_trades.py（对账）
       └─ verify_model_panel_sync.py  → 面板与正式模型不一致则告警 ★每日兜底
```

三道防线缺一不可：**重建自动化**保证面板不停更；**Promote 门禁**保证正式模型只能经人工确认后替换；**每日校验**保证任何一步漏做都会在当天盘后显式报警。

## 三、涉及文件清单

| 文件                                    | 作用                                                                                                                               | 部署时必须存在 |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `refresh_panel_v3.py`                 | 合并主库+增量 → 重建 v2 → 切片 v3 面板（面板重建自动化）；**V2.0 起切片时补算 industry\_mom20/turnover\_rank**（build\_features\_v2 不产出，缺失则 31/33 特征与正式模型不匹配） | 是       |
| `promote_model.py`                    | 唯一写生产模型的入口；提升时写绑定记录                                                                                                              | 是       |
| `verify_model_panel_sync.py`          | 面板最新日 vs 正式模型绑定日一致性校验（exit 1 = 不一致）                                                                                              | 是       |
| `run_scheduled.ps1`                   | 定时任务调度器（daily/monitor/retrain/factor 四模式）                                                                                        | 是       |
| `data/feature_panel_v3.parquet`       | 生产面板（V2.0 为 2026-08-28 版，7,549,623 行/33 特征）                                                                                      | 是       |
| `data/features_v3.json`               | v3 特征定义（**33 特征**，V2.0 含 industry\_mom20/turnover\_rank），Promote 校验特征数用                                                          | 是       |
| `data/model_panel_binding.json`       | 正式模型-面板绑定记录，校验器读它                                                                                                                | 是       |
| `D:/QuantLab/models/lgb_model_v3.txt` | 正式模型（V2.0 为 3017 树，只读保护）                                                                                                         | 是       |
| `data_live/incremental_daily.parquet` | 每日增量行情（xtdata 拉取），面板重建的数据源之一                                                                                                     | 是       |
| `data_live/merged_daily_full.parquet` | 主库+增量合并日线（refresh\_panel\_v3 中间产物，可重建）                                                                                           | 否，可生成   |
| `D:/astock/daily/stock_daily.parquet` | 主库周更权威快照（只读，refresh\_panel\_v3 数据源）                                                                                              | 是       |

依赖 Python 包：`lightgbm pyarrow pandas numpy scikit-learn scipy optuna optuna-integration`（研究环境 Python 3.10/3.11 均可）。

## 四、新设备配置步骤

### 第 1 步：复制项目

把整个 `Project_16_LightGBM股票大师` 目录复制到新设备，保留上述「必须存在」的全部文件（尤其是面板、正式模型、绑定记录三件套，缺一不可）。模型与面板也可从版本归档恢复：`versions/models/lgb_model_v3.txt`、`versions/panels_bak_20260828/`。

### 第 2 步：核对数据源路径

检查 `data_config.py` 中 `ASTOCK_DIR = "D:/astock"` 指向新设备主库位置，主库需包含 `daily/stock_daily.parquet` 等。若路径不同，改本文件即可（部署时只需改这一处）。

### 第 3 步：验证绑定一致性

```powershell
cd D:\QuantLab\projects\Project_16_LightGBM股票大师
python verify_model_panel_sync.py
```

输出「校验结果: 一致」且 exit code 0，说明面板与正式模型同版，可继续。若输出「!! 面板已更新至 X，但正式模型仍绑定 Y」，说明存在版本脱节，先补做重训+Promote（见第 5 步）再继续。

### 第 4 步：创建 Windows 计划任务

以下任务在**每周一 17:00** 自动重建面板并重训，在**每个交易日 16:30** 自动更新增量并选股，两处都会自动执行同步校验。以管理员 PowerShell 执行：

```powershell
# 盘后增量+选股+对账+同步校验（工作日 16:30）
schtasks /Create /TN "Quant_Daily_Update" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:30 /F `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\QuantLab\projects\Project_16_LightGBM股票大师\run_scheduled.ps1 -Mode daily"

# 周更重训（周一 17:00）：备份模型→重建面板→重训候选→同步提示
schtasks /Create /TN "Quant_Weekly_Retrain" /SC WEEKLY /D MON /ST 17:00 /F `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\QuantLab\projects\Project_16_LightGBM股票大师\run_scheduled.ps1 -Mode retrain"
```

监控任务（可选，与防呆无关但策略运行需要），工作日各时间点，命令同上但 `-Mode monitor`，任务名 `Quant_Monitor_0945 / 1030 / 1100 / 1330 / 1400 / 1430`。

创建后核对：

```powershell
schtasks /Query /TN "Quant_Daily_Update" /V /FO LIST
```

### 第 5 步：手动触发一次盘后链路验证

等当日增量数据就绪后，手动跑一次 daily 模式确认全链路：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File run_scheduled.ps1 -Mode daily
```

日志落 `data/schedules/daily_<时间戳>.log`，重点看末段两行：

```
[对账] 持仓一致，无异常           （或 !! [对账] ... 需人工核查）
[模型-面板同步] 面板与正式模型同版，OK   （或 !! [模型-面板同步] 面板与正式模型版本不一致）
```

两条都输出正常行，即配置完成。

## 五、周更重训与 Promote 的每周操作节奏

每周一 17:00 定时任务自动完成「备份模型 → 重建面板 → 重训候选」，**但不会自动覆盖正式模型**。定时任务日志会输出：

```
!! [模型-面板同步] 面板已更新但正式模型未 promote —— 请用 promote_model.py 提升今日候选 lgb_model_v3_retrain_<date>.txt
```

此时需人工决策是否提升候选（`promote_model.py` 是唯一写生产模型的入口，刻意保持人工，防止重训直接覆盖正式模型的事故——2026-08-24 曾发生周更任务直接写正式模型、无备份的覆盖事故）：

```powershell
# 先看候选在测试集的指标（data/optuna_report.json 或 data/real/train_optuna_<date>.log）
# 决定提升则执行（--yes 跳过交互确认）：
python promote_model.py --model-file D:/QuantLab/models/lgb_model_v3_retrain_<date>.txt --suffix v3 --yes --note "说明"
```

提升后校验器自动通过（绑定记录同步更新），次日盘后 daily 任务输出「同版，OK」。

判断是否提升的参考标准（2026-08-28 V1.2 首次实践）：候选在新面板测试集（2024-07\~2026-08-27）上的 test IC / ICIR / 准确率不劣于当前正式模型即可提升；核心诉求是**训练面板与生产面板一致**，哪怕指标持平也值得提升，因为旧模型训练于旧面板、喂新面板本身就是分布错位。

## 六、异常与处置速查

| 现象                                                        | 原因                                         | 处置                                                                                              |
| --------------------------------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| daily 日志 `!! [模型-面板同步] ...不一致`                            | 面板已更新但正式模型未 Promote，或绑定记录丢失/被改             | 先跑 `verify_model_panel_sync.py` 看具体差异；若为「面板更新未 promote」则按第五节提升今日候选；若绑定记录缺失，重跑一次 Promote 或手工补齐绑定 |
| `verify_model_panel_sync.py` 提示「正式模型树数 != 绑定」或「mtime 不一致」 | 正式模型被直接改动，绕过 Promote 门禁                    | 从 `versions/models/` 恢复受控版本，重新 Promote；杜绝绕过门禁直接覆盖模型                                             |
| retrain 日志 `!! 增量无完整收盘日`                                  | 增量库当天盘中数据不完整，`refresh_panel_v3.py` 按设计剔除当天 | 属正常保护，等收盘后重跑即可                                                                                  |
| 选股文件同票 `model_prob` 跨日完全一致                                | 面板停更（正是本次防呆要消灭的场景）                         | 跑 `verify_model_panel_sync.py`；确认面板与正式模型绑定日是否脱节，脱节则重训+Promote                                   |

## 七、防呆边界（已知限制）

* 校验器只对比「面板最新日 vs 绑定日」，不校验面板内容是否被局部篡改。面板重建必须走 `refresh_panel_v3.py` 全量重建，不要手工改 parquet。

* Promote 门禁靠「人工执行 promote\_model.py + 只读保护」实现，定时任务不会自动 promote——这是设计，不是遗漏。

* 主库 `E:/astock` 为周更快照，面板最新日取决于主库+增量合并结果；若主库长期不更新，面板最新日会停留在合并后的最大值（此时校验器仍能发现「面板未前进」，但不会误报）。

