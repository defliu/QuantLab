# DE 独立审查报告：T-20260913-001 批次（P16 三策略自我迭代机制 9 项）

- **审查日期**：2026-09-13（周日晚）
- **审查对象**：2026-09-13 落地的自我迭代机制批次，含 ①ENS 资金池滚动 ②纸面自动降级闸 ③G2↔ENS 反向互斥 ④特征 IC 周报 ⑤enh 新鲜度硬门禁 ⑥G2 试验模型自动回滚 ⑦E6 双调度器互查巡检 ⑧配置漂移周度复查 ⑨P3 制度固化
- **审查方式**：只读静态审查 + 已落盘数据文件实证核对 + 调度器（schtasks）与 PS5.1 运行时行为实测。未运行任何训练/回测/下单。
- **结论速览**：9 项中 **7 项 PASS（含风险注记）、1 项 FAIL（⑥自动回滚，存在 P0 死链）**；另有一个**横切 P0**（调度 ps1 丢 UTF-8 BOM，违反 AGENTS 硬规则）。降级闸、资金池滚动、互斥、新鲜度门禁、E6、漂移复查、制度固化均可运行；**⑥的 --auto 自动回退当前为死代码路径，修复前不可依赖**；**BOM 必须在 09-14 周训前恢复**。

---

## ① 审查范围与结论摘要表

| # | 机制 | 结论 | 主要问题 |
|---|---|---|---|
| 1 | ENS 资金池滚动（reconcile_ens.py） | **PASS** | P2×2（TW 调度写 QMT_POOL 与 E6 边界表述张力；realized 不含费） |
| 2 | 纸面自动降级闸（paper_forward_downgrade.py + 三 rebalance 接入） | **PASS（带 P1）** | P1-1（文档承诺 exit 2 未实现）；P2×3（fail-open、每日重写标记、V1.3 臂口径） |
| 3 | G2↔ENS 反向互斥 | **PASS** | P2×2（读文件 fail-open；ENS 侧无陈旧清理致过度排除） |
| 4 | 特征 IC 周报（feature_health_weekly.py） | **PASS** | P2×2（--check 仍写文件；--check 下 ALERT 返回 0 的语义含糊） |
| 5 | enh 新鲜度硬门禁（check_enh_freshness.py） | **PASS** | P2×2（exit 1 日志文案误导；全量读 parquet 性能） |
| 6 | G2 试验模型自动回滚（rollback_check_g2.py --auto） | **FAIL** | **P0-1（列名与真实 CSV 不匹配 → 死链）**；P1-2（回退失败静默 return 0）；P2×3（缺"连续 3 日"、指针写入非原子、窗口口径） |
| 7 | E6 双调度器互查巡检（nightly_check.py E6） | **PASS** | P2×1（快照新鲜度未校验） |
| 8 | 配置漂移周度复查（config_drift_recheck.py） | **PASS** | P2×3（gate 回滚后仍用被拒候选跑；argmax 无容差；正则解析登记串） |
| 9 | P3 制度固化（repowiki 方法论第七节） | **PASS** | P2×1（"连续 3 日"规格与实现/文档链不一致，随 ⑥） |
| — | **横切：run_scheduled.ps1 / paper_forward_daily.ps1 丢 BOM** | **FAIL（P0-2）** | 违反 AGENTS 硬规则；PS5.1 下中文日志乱码（已实测）；09-14 周训依赖此文件 |

---

## ② 逐项审查明细

### 1. ENS 资金池滚动 —— PASS

**实现**：`reconcile_ens.py` 复刻 G2 的四件套：
- `_all_fills`（L29-41）：扫 `成交记录_ens_*.csv`，`str(成交数量).startswith('-')` 判卖出，含 PARTIAL_FILLED 合并。
- `_realized_pnl_from_fills`（L44-76）：仅按已平仓部分累计 `(sell_vwap - buy_vwap) * vol`，用 FIFO 配对，**不包含手续费**（与 G2 同口径的近似，见 P2-14）。
- `_float_pnl_now`（L79-106）：当前持仓浮盈，现价取 `_latest_price`（桥客户端最新价）。
- `_roll_ens_capital`（L109-124）：`capital = START_CAPITAL + realized + float`，与 `reconcile_g2.py` L29-124 **逐函数同构**（仅 import 从 `g2_config`→`g2_ens_config`、`qmt_bridge_client`→`qmt_bridge_client_ens`），无逻辑漂移。

**账本安全**：`g2_ens_config.py` `load_ens_capital`（L50-63）校验 `account_id` 戳（70180771），不匹配自动备份 `.bak_acct_*` 并空仓起步——满足 AGENTS「持票账本 account_id 戳」红线；`save_ens_capital`（L66-79）tmp+`os.replace` 原子写。

**实证**：`data/reconcile_g2_ens/reconcile_g2_ens_20260913.json` 显示 capital 100000→98469.21，components（realized/float）字段齐全，首次滚动已生效。

**注记**：
- 周日跑对账会出现「账本比账户多」误报（T-20260911-001 已知），非本批引入。
- ENS 调度来自 TW 15:45 日终对账任务（E6 快照 18c0c8d2）——该任务实际是 **QMT_POOL 资金池文件（ens_strategy_capital.json）的写入者**，与 E6 自身 owner_boundary「D:/QMT_POOL 产物生成调度唯一 owner = QuantLab schtasks」存在表述张力（P2-10，边界声明需细化或迁移调度）。

### 2. 纸面自动降级闸 —— PASS（带 P1）

**触发条件**（`paper_forward_downgrade.py`）：`n >= MIN_TRIALS(30)` 且 `mean_excess < 0` 且 `< EXCESS_FLOOR(-0.0002)`（L45-47、L168）。L168 的 `mean_excess < EXCESS_SIGN and mean_excess < EXCESS_FLOOR` 是冗余条件（EXCESS_SIGN=0，后者更严），功能正确，纯美观问题。

**超额收益口径**：`load_open`/`load_bench` 用相同 universe/市值过滤对齐（L63-98），持有期到期门（`_arm["hold"]`，L147-149）+ rank≤2 门槛（L142）与 ARMS 定义一致；G2 臂 hold=15 匹配 live N15，ENS 臂 hold=10 匹配，**V1.3 臂 hold=10 但实盘 `rebalance_daily.py` HOLD_DAYS=5（L47，T-20260904-001 到期制）**——评估窗口与实盘持有期口径不匹配（P2-8）。

**--check 确认不写标记不推送**：`check_only=True` 时 per-arm 循环在标记变更前 `continue`（L204-205）；`save_mark` 仅 `if not check_only`（L220-221）；alerts 只在非 check 分支 append → 不触发 `notify_feishu`。main 尾部打印 `[CHECK] dry-run：未写标记未推送`。**核实为真只读评估**。

**冻结时卖出真的保留**：
- `rebalance_g2.py`：卖出计划在 L365-392 全部 append 完成后，L398-408 才查 `is_frozen`（冻结→`n_slots=0`），买入段 L425 起 `n_slots>0` 才进——**先卖后买的顺序保证冻结只清买入**。
- `rebalance_ens.py`：同构，L348-358。
- `rebalance_daily.py`（V1.3）：L387-396 冻结时 `exec_buys=[]`，但 `sell_plan`/`trim_orders` 的执行在 L439-459 **独立于冻结标志**——减仓/止损卖出保留。
- 桥内风控（STOP/TP/TRAIL）在 QMT 桥策略进程内运行，与 rebalance 无关，天然不受冻结影响。V1.3 的 qmt_monitor 内置风控同理。**设计文档「冻结只停买入，卖出/减仓/桥内风控保留」三条全部核实**。

**消费端 fail-open**：三个 rebalance 的 `is_frozen` 均 try/except，标记文件读取失败→`frozen=False`（买入恢复）。PROJECT_MEMORY 已记为拍板（「import 失败降级为不冻结」），但**文件损坏（非 import 失败）也走同一路径静默解冻**，原子写缓解了大部分风险，仍建议加告警日志（P2-4）。

**实证**：`data/real/paper_forward_downgrade.json` G2 臂 n=8、mean_excess=-0.0242——**<30 未触发冻结，判定正确**。设计文档宣称「G2 已有 18 笔」与实测 n=8 不符（N15 到期口径下样本尚少），为文档数字过期，非代码 bug。

**P1-1（退出码失实）**：模块 docstring（L20）承诺「推送失败 exit 2」，`_notify` 失败时 main 打印「冻结推送失败（已写标记，调度应感知）」（L264），**但 main() 恒 return 0（L268），退出码 2 从未返回** → `paper_forward_daily.ps1` 的 DOWNGRADE-ALERT 分支（dgExit -ne 0 → exit 2）对「推送失败」这个场景**永不可达**。推送失败当前实际上 fail-silent。

### 3. G2↔ENS 反向互斥 —— PASS

**路径对接核实**：`g2_config.py` L40-41 `ENS_HOLD_DATES_FILE = DATA_DIR / "rebalance_g2_ens" / "g2_ens_hold_dates.json"` 与 `g2_ens_config.py` L40 `HOLD_DATES_FILE` **同指一文件**；实测文件存在，含 300138.SZ、600409.SH 两条（ENS 09-10 建仓）。`rebalance_g2.py` L426-438 买入候选剔除 ENS 持仓，方向互斥成立（ENS 侧自 09-10 起已剔 G2 持仓，本次补齐反向）。

**fail-open 风险**：`rebalance_g2.py` L429-435 读 ENS 文件失败→**不排除**（异常吞掉、excluded 空集）→ 双买风险静默回归（P2-5）。与「互斥是硬约束」的目标不对称，建议至少打告警日志。

**ENS 侧账本质量**：`rebalance_ens.py` `_update_hold_dates`（L96-112）**没有** G2 版的陈旧条目清理（`rebalance_g2.py` L95-117 有）→ ENS 桥内风控卖出后 hold_dates 残留陈旧条目 → G2 持续过度排除该候选。方向保守（只会少买不会多买），但属预存分歧，与本批「口径一致」目标相关（P2-5b）。

### 4. 特征 IC 周报 —— PASS

**实现核对**：`feature_health_weekly.py` 缺失率阈值 15%/25%（L36-37）与需求一致；ALERT 判定 = `avg_ic < -0.02` **且** `pos_ratio < 0.4` 双条件（L96-105），单条件只进 WATCH——与设计文档口径一致。27 个特征全表输出（features_v3.json feature_cols=27，报告 27 行吻合）。

**挂载时点**：`run_scheduled.ps1` L181-184，位于 refresh 面板**之前**（retrain 模式步骤内）——刻意用上周期面板评估上周特征，符合设计理由（本次训练数据不能用于评估本次特征）。

**退出码语义**：非 check 模式 ALERT → exit 2（L169），ps1 L182 `-eq 2` 告警不阻断——语义匹配（周报是监测不是门禁）。**但 --check 模式下 ALERT 也返回 0**（docstring「0 正常（含 WATCH）」未提 check 分支），dry-run 语义含糊（P2-11b）。

**首跑实证**：`data/feature_health_weekly_20260913.md` 27 特征，`fin_ocf_to_profit` 缺失率 13.34% 进 WATCH（未达 15% ALERT），其余 NORMAL——告警链路未实际触发过 ALERT，规则正确性仅静态核实。

**P2-11a**：`--check` 号称「只读评估不推送」，但 L123-159 仍写 `.json`/`.md` 报告文件——「只读」不严格（无副作用风险，描述不精确）。

### 5. enh 新鲜度硬门禁 —— PASS

**实现**：`check_enh_freshness.py` `MAX_LAG_DAYS=7`（L22）；面板最新交易日滞后 >7 天 → exit 2（L57-59）；merged parquet 缺失 → exit 1（fail-safe 放行）。

**调度接线**：`run_scheduled.ps1` L209-215 `$skipG2=$true` → L288 `if (-not $skipG2)` 门住 `train_g2`——**train_g2 被有效阻断**；L323-325 else 分支记录日志。接线正确。

**与 B5 双保险**：`nightly_check.py` B5（L236-246）同阈值 ENH_MAX_LAG_DAYS=7 每晚独立再查——纵深防御成立。

**P2-3**：exit 1（数据缺失、放行）在 ps1 L214 打印「enh 面板数据异常…视为新鲜，继续 G2 重训」——行为正确（fail-safe 放行是拍板），**文案「视为新鲜」误导**，日志审计时会误判。另有 L26-30/L34-42 全量读 parquet 只为取 max date，周频可接受（P2）。

### 6. G2 试验模型自动回滚 —— **FAIL（P0）**

**设计意图**：`rollback_check_g2.py` 对比 trial 与 live 的纸面前向超额，深度劣化（trial < live - 0.0005/日，L32、L136）→ `--auto` 自动回滚 + 飞书；轻度劣化 exit 2 调度告警。

**P0-1（死链，必须修）**：脚本 L39 读 `df["trade_date"]`、L121-124 读 `after["fwd_ret"]`，**但真实 `data/real/paper_forward_live.csv` 表头为 `date,code,total_new,prob,entry,exit,ret,hold,rank`，不存在这两列**。后果链：
1. 一旦 trial 存在且满观察窗，`trading_days_since` 计算 KeyError → 脚本 exit 1（「无法计算交易日」）；
2. `run_scheduled.ps1`（retrain L160-162 与 daily 分支）把非 0 非 2 退出码归入「检查跳过」类日志——**exit 1 与「确实没有 trial 需要检查」不可区分**；
3. 深度劣化 → 自动回退、轻度 → exit 2 告警，两条路径**永远不会执行**。

当前未暴露的唯一原因：`g2_live_model.json` 尚无 trial 字段（观察窗未满），脚本提前 return 0。**下一个 train_g2 产生 trial 后即触发**。单测 9/9 通过是因为 mock CSV 用了错误 schema 自测自证——MOCK 测不到真实数据 schema 是 AGENTS 已知教训的又一实例。

**P1-2（静默失败）**：`--auto` 分支（L143-149）中 `_do_rollback` 失败（如 prev_model 文件缺失）仅 print 后**仍 return 0**，无飞书 → 调度日志显示「深度劣化已自动回退，OK」——**假阳性确认**。自动回退的失败路径必须与触发路径同级 fail-loud。

**P2-6a（规格偏离）**：讨论纪要 M3 明确「**连续 3 日**深度劣化才自动回退」，实现是**单次评估即触发**；方法论第七节 L56-61 同样未写该条件——需求、实现、制度文档三者不一致，需拍板对齐（补实现或改文档）。

**P2-13**：`_do_rollback` 指针写入（L82-83）非原子（对比账本/标记文件的 tmp+os.replace 惯例），回滚瞬间崩溃会留下半写指针。

**P2-9（窗口口径）**：CSV 的 ret 由 `paper_forward_daily.ps1` L29 以 `--hold 10` 回填，而 G2 live 持有期 N15——即使修复列名，回滚评估窗口与实盘持有期仍不一致，建议 CSV 回填按 ARMS 口径分臂传 hold。

### 7. E6 双调度器互查巡检 —— PASS

**实现**：`nightly_check.py` L425-445 读 `data/tw_schedule_snapshot.json`，`conflict_with` 非空 **且** `status=="Active"` → FAIL；只记 conflict 不记 paused。

**实证**：快照 generated_at=2026-09-13 19:30，13 任务中唯一带 conflict_with 的 eda9b0c3 状态 Paused → **当前判定 PASS 正确**（paused 任务不构成双注册冲突）。

**P2-7**：`generated_at` 字段未被校验——若快照长期未手动更新（现为手动维护），巡检会基于过期清单给出假 PASS。建议加「快照龄 >7 天 → WARN」。

### 8. 配置漂移周度复查 —— PASS

**实现**：`config_drift_recheck.py` `NEIGHBORHOOD`（L31-35）：G2 N=[10,15,20]、融合 [5,10,15]、V1.4 [5,10,15]，各 3 点 + EXIT 固定单点；**只登记不改实盘**（L127-130、L144 仅写 md 报告，无 strategy_cfg_lib/g2_config 写操作）——与设计「登记观察、下一批才拍板」一致。

**邻域中心核对**：`strategy_cfg_lib.json` G2 N=15 → 邻域 [10,15,20] 以 15 为中心 ✓；融合 N=10、V1.4 N=10 同理 ✓。

**挂载**：`run_scheduled.ps1` L326-343，retrain 模式 gate_strategy_layer 之后、仅当 `$g2New` 存在时跑。**$g2New 作用域本身无 bug**（PowerShell if 块不建作用域，跨 switch 分支可见；skipG2 时为 $null 正确跳过；`Run-Py` 管道后 `$LASTEXITCODE` 保留正常；`$ErrorActionPreference="Continue"` 不误中断）——审查重点④担心的作用域/短路问题**均不存在**。

**实证**：`data/config_drift_G2_20260913.md`（09-13 19:35 手动首跑）：N15 +0.21%/日（=登记值）、N10 +0.12%、N20 +0.02% → 判定「配置未漂移」，与 strategy_cfg_lib 登记串 +0.213% 吻合；slip 0.001 与 cfg_lib 口径一致。

**P2-2（逻辑瑕疵）**：gate FAIL 自动回滚（ps1 L311-312）后，L329-334 的漂移复查**仍以被拒候选为 --model、以回滚后指针的 meta 为 --meta** 跑——model/meta 错配，轻则 3 点全失败记「运行异常」，重则产出无意义结果。应在 gate 后重读指针决定跑不跑。

**P2-17/18**：3 点纯 argmax 无显著性边际——微小的日超额差（如 0.0002）也会被记为「漂移」，噪声登记风险；`hist_excess` 靠正则从中文登记串解析（L91-93），脆弱但仅显示用。

### 9. P3 制度固化 —— PASS

`.qoder/repowiki/zh/content/因子研究系统/网格寻优与防过拟合方法论.md` 第七节（L56-61）5 条制度与实现一一对应：N 邻域 3 点、周频重扫、只登记不自动改配置、trial 观察、漂移定义。`主要策略详解.md` L173 已同步——满足 AGENTS「知识库同步收尾」硬规则。

**唯一缺口**：第七节未含 M3 的「连续 3 日」条件（随 ⑥ 的规格偏离一并处理）。

---

## ③ 问题清单（P0/P1/P2）

### P0（修复前不可依赖该机制）

**P0-1 rollback_check_g2.py 列名与真实 CSV schema 不匹配 → --auto 死链**
- 证据：脚本 L39 `df["trade_date"]`、L121-124 `after["fwd_ret"]` vs `data/real/paper_forward_live.csv` 实际表头 `date,code,total_new,prob,entry,exit,ret,hold,rank`（实测）。
- 后果：trial 存在时必 exit 1 → ps1 记「检查跳过」→ 自动回退/轻度告警永不可达；当前无 trial 才未被暴露。
- 修复：改用真实列名（date/ret/hold），并以真实 CSV 回归测试替换单测 mock schema（9/9 通过是假阴性）；ps1 侧把 exit 1 与「无 trial」分开记日志。

**P0-2 run_scheduled.ps1 / paper_forward_daily.ps1 丢 UTF-8 BOM**
- 证据：两文件首字节实测 `23 20 63` / `23 20 70`（无 EF BB BF）；均含大量中文；schtasks 实测三任务（quant_weekly_retrain / quant_daily_update / paper_forward_daily）全部以 `powershell.exe`（PS5.1）执行；PS5.1 运行时 Get-Content 解码实测中文变「鐩樺悗…」乱码（与 2026-08-31 事故同型）。
- 矛盾点：PROJECT_MEMORY 09-12 段声称「BOM 校验完好（EF BB BF）」→ 09-13 批次编辑时丢 BOM 且未按硬规则复验。
- 风险：T-20260903-015 前科——下次编辑随时可能让 09-14 周训任务解析崩溃；当前日志中文乱码已在降低可审计性。
- 修复：两文件补 UTF-8 BOM；按 AGENTS 规则在每次 ps1 编辑后强制校验 BOM。

### P1（本批目标的承诺未兑现）

**P1-1 paper_forward_downgrade.py「推送失败 exit 2」未实现**
- 证据：docstring L20 承诺；main L264 打印「调度应感知」但 L268 恒 return 0 → `paper_forward_daily.ps1` DOWNGRADE-ALERT 对推送失败不可达。
- 修复：推送失败路径 return 2，或在 main 汇总 alerts 状态统一返回。

**P1-2 rollback_check_g2.py --auto 回退失败静默 return 0**
- 证据：L143-149 `_do_rollback` 失败仅 print，无飞书、exit 0 → daily 日志「深度劣化已自动回退，OK」假阳性。
- 修复：回退失败 return 非 0 + 飞书告警；ps1 对该场景显式记 ERROR 并轻度告警。

### P2（风险与改进项）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| 1 | M3「连续 3 日」条件未实现，单次评估即触发回滚；方法论第七节同样缺失 | 讨论纪要 M3 vs rollback L136-149；方法论 L56-61 | 拍板对齐：补实现或改三处文档 |
| 2 | gate FAIL 回滚后漂移复查仍用被拒候选 + 错配 meta 跑 | run_scheduled.ps1 L311-312 vs L329-334 | gate 后重读指针再决定 |
| 3 | check_enh_freshness exit 1 日志文案「视为新鲜，继续」误导 | ps1 L214 | 改文案「数据缺失，fail-safe 放行」 |
| 4 | 冻结标记消费端 fail-open：文件损坏→静默不冻结 | rebalance_g2 L398-408 等 try/except | 损坏时打告警日志（保留拍板的 fail-open 行为也可，但须可见） |
| 5 | 互斥读 ENS 文件 fail-open（缺失→不排除，双买回归）；ENS hold_dates 无陈旧清理→过度排除（保守） | rebalance_g2 L429-435；rebalance_ens L96-112 vs rebalance_g2 L95-117 | 读取失败告警；ENS 侧补陈旧清理 |
| 6 | E6 快照新鲜度未校验，手动快照过期→假 PASS | nightly_check L425-445 未用 generated_at | 快照龄 >7 天 WARN |
| 7 | 漂移复查 3 点 argmax 无显著性容差，微差即记漂移；hist_excess 正则解析中文登记串 | config_drift L91-93、L127-130 | 加最小边际（如 0.0005/日）才判漂移 |
| 8 | V1.3 臂评估窗口 N10 vs 实盘持有 N5 口径不匹配 | paper_forward_downgrade ARMS vs rebalance_daily L47 | 确认是否有意（候选口径 vs 到期制），文档注明 |
| 9 | 回滚 CSV 回填窗口 --hold 10 vs G2 live N15 | paper_forward_daily.ps1 L29 | 分臂传 hold 或统一说明 |
| 10 | ENS 资金池文件由 TW 15:45 任务调度写入，与 E6 owner_boundary「QMT_POOL 唯一 owner=QuantLab schtasks」表述张力 | E6 快照 18c0c8d2 | 细化边界声明（TW 可触发 QuantLab 脚本）或迁移调度 |
| 11 | feature_health --check 仍写报告文件；--check 下 ALERT 返回 0 语义含糊 | L123-159、L169 | check 模式不落盘或文档注明 |
| 12 | ENS 与 G2 预存分歧（非本批引入但涉口径一致）：ENS 预算基数未扣保留持仓投入（G2 P1-4 修复未同步）、无大盘门控、hold_dates 缺失 fail-open 即卖（G2 fail-safe 跳过） | rebalance_ens L348-358 等 | 列入下批对齐清单 |
| 13 | _do_rollback 指针写入非原子 | rollback L82-83 | tmp+os.replace |
| 14 | 资金池 realized 不含费 vs positions_cfg 成本含费（G2/ENS 同口径继承性近似） | reconcile_ens L44-76 | 已知近似，两桥一致，可接受 |
| 15 | 降级闸非 check 模式每日无条件重写标记文件（幂等，描述为「新触发才写」不精确） | L220-221 | 文档措辞对齐 |

---

## ④ 设计层面建议

1. **统一前向评估数据底座**：降级闸、回滚检查、forward_stats、退出统计各自读同一批 CSV 却各自解析 schema——本次 P0-1 正是 schema 漂移的产物。建议抽一个共享的前向记录模块（单一 schema：date/code/rank/ret/hold/arm），四处共用读写，列名改动只改一处。
2. **「动作失败」与「触发」同级 fail-loud**：自动回退（P1-2）、冻结推送（P1-1）的失败路径当前都静默降级。凡自动化动作（回滚/冻结/告警），失败必须 exit 非 0 + 飞书，调度日志显式 ERROR——否则运维只能在灾难后发现机制从未生效。
3. **调度退出码矩阵文档化**：五个新脚本 × 0/1/2 语义目前散在 docstring 与 ps1 分支里，已出现「exit 1 被记为跳过」「exit 1 显示新鲜」「承诺 2 未实现」三种错位。建议在 run_scheduled.ps1 头部维护一张退出码→动作对照表，ps1 分支按表写。
4. **gate 回滚后的流水线状态一致性**：gate/rollback 会改指针，但 ps1 变量（$g2New）不随回滚失效。凡「指针可能被上一步改变」的步骤，执行前重读指针而非依赖变量（P2-2 同源）。
5. **BOM 校验自动化**：把「含中文 .ps1 必须有 BOM」做成 CI/调度前置检查（几行 PowerShell），替代人工记忆——本批丢 BOM 且 PROJECT_MEMORY 还记着「完好」，证明人工校验不可靠。

---

## ⑤ 总体结论

- **可依赖**：①资金池滚动（与 G2 逐函数同构+原子写+账号戳）、③反向互斥、④特征周报、⑤新鲜度门禁（train_g2 阻断接线正确）、⑦E6、⑧漂移复查（只登记）、⑨制度固化——均按设计落地，带 P2 风险注记。
- **不可依赖（修复前）**：⑥自动回滚——列名错配使 --auto 为死链（P0-1），叠加回退失败静默（P1-2），当前「深度劣化自动回退」的承诺实际不存在。**所幸下一轮 train_g2 产生 trial 前必须修复，否则观察窗到期即暴露。**
- **立即行动**：P0-2 BOM 必须在 **09-14 周训前**恢复（两个 ps1 是周训/日更/降级闸三条调度链的载体，PS5.1 前科+硬规则双重命中）。
- **批次总评**：工程完成度约 85%——7/9 项可直接运行，但「自动化动作的失败路径 fail-loud」这一工程纪律在本批系统性缺失（P1-1/P1-2 同型），加上 P0-2 违反已立硬规则，建议本批以「待修复」状态挂看板，P0×2 + P1×2 修复并复验后才标记 DONE。

*（本报告为独立审查产物，全部结论基于真实文件内容与实测数据，引用行号以 2026-09-13 晚工作区状态为准。）*
