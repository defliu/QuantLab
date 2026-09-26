# 定时任务迁移可行性评估：TRAE Project_16 14 项 → WorkBuddy

> 评估对象：`定时任务迁移配置明细_WorkBuddy.md`
> 评估时间：2026-09-23
> 评估方式：文档逐条比对 + 本机实况核查（Windows 计划任务 39 项、脚本产物、外部依赖路径、guard 日志）

---

## 一、总体结论

**技术可行，但"完整照搬"不可行。** 14 项中不存在需要"重建 14 个自动化"的场景：

| 判定 | 数量 | 任务编号 |
|---|---|---|
| 不建议迁（Windows 计划任务已覆盖） | 5 | 6 午休报告、7 盘后复盘、10 Tushare刷新、11 夜间检修、13 成本锚 |
| 不建议迁（真实下单·高危竞态） | 3 | 1 开盘实时复核、4 G2换仓、5 融合换仓 |
| 需改造依赖后方可迁 | 3 | 3 集合竞价预判、8 G2日终对账、9 融合日终对账 |
| 可直接迁（零改造） | 2 | 2 候选预生成、14 模拟账号盘后监控 |
| 阻塞待解 | 1 | 12 夸克行情每周检查 |
| **合计** | **14** | |

**核心判断：当前不存在非迁不可的理由。** TRAE 侧三项下单任务 2026-09-22 全部正常触发（见 §二·证据 4），guard 兜底体系设计完备且运行正常。迁移的净收益主要来自"统一管理入口"，而非可靠性提升——在关键路径上下单类任务，WorkBuddy 的可靠性等级反而低于 Windows 计划任务（见 §四·风险 1）。

---

## 二、核查依据（实测证据）

**证据 1｜Windows 计划任务实测清单：38 项 `Quant*` 任务**，全部 `Enabled=True`、全部 `LogonType=S4U`，注册信息完整，与 AGENTS.md 记载的 39 项一致。其中与本文档功能重叠的条目：

| Windows 任务 | 触发 | 动作 | 与文档对应项 |
|---|---|---|---|
| `Quant_Tushare_Moneyflow_Refresh` | 每日 19:30 | `refresh_tushare_moneyflow_daily.ps1` | #10（同时刻同目的） |
| `Quant_P16_NightlyCheck_0100` | 每日 01:00 | `nightly_check_task.ps1` | #11（同时刻） |
| `Quant_P16_CfgPremarket_0900` | 工作日 09:00 | `gen_positions_cfg_premarket.ps1` | #13（同时刻） |
| `Quant_P16_DE_Report_Midday` | 工作日 12:00 | `de_report_daily.ps1 -Task midday` | #6（**同一脚本**） |
| `Quant_P16_DE_Report_Close` | 工作日 16:20 | `de_report_daily.ps1 -Task close` | #7（**同一脚本**） |
| `Quant_Monitor_0945` 等 6 项 | 09:45/10:30/11:00/13:30/14:00/14:30 | `run_scheduled.ps1 -Mode monitor` | 与 #1 **职责不同**（只读盯盘，非下单） |
| `Quant_V13_Rebalance_Guard` | 工作日 10:05 | `rebalance_daily_guard.ps1` | #1 的**兜底**（非重复） |
| `Quant_P16_G2_Rebalance_Guard` | 工作日 10:07 | `rebalance_g2_guard.ps1` | #4 的兜底 |
| `Quant_P16_ENS_Rebalance_Guard` | 工作日 10:08 | `rebalance_ens_guard.ps1` | #5 的兜底 |
| `Quant_P16_G2Candidates_Night` | 工作日 20:05 | `g2_candidates_night.ps1` | **#3/#4/#5 的前置依赖（文档未列）** |
| `quant_daily_update` | 工作日 16:30 | `run_scheduled.ps1 -Mode daily` | **#1 的候选前置（文档未列）** |

**证据 2｜文档引用的关键路径校验**

| 路径 | 文档写法 | 实测 |
|---|---|---|
| lark-cli | `...\lark\1.0.4\bin\lark-cli.exe` | ❌ **不存在**，实际版本为 `1.0.5` |
| quarkclouddrive skill | `C:/Users/Administrator/.trae-cn/skills/quarkclouddrive/...` | ✅ 存在（但为 **TRAE 专有**，WorkBuddy skills 目录无） |
| miniqmt venv | `...\envs\miniqmt\Scripts\python.exe` | ✅ 存在 |
| `D:\QMT_POOL` | — | ✅ 存在 |
| `d:\Workspace`（#14 输出目录） | message 内 | ✅ 存在 |

Project_16 目录下 18 个被 message 引用的脚本**全部存在**（`is_trade_day.py`、`deploy_predict.py`、`review_full.py`、`market_tier.py`、`rebalance_daily.py`、`rebalance_g2.py`、`rebalance_ens.py`、`reconcile_g2.py`、`reconcile_ens.py`、`qmt_bridge_client*.py`、`push_*.py`、`gen_positions_cfg_v13.py`、`de_report_daily.ps1`、`build_g2_daily.py`、`deploy_predict_g2.py` 等）。

**证据 3｜WorkBuddy 侧现状**
- 现有自动化 6 条，其中 `Tushare moneyflow 每日刷新 (P16 F2主源)`（19:30，**PAUSED**）即本文档 #10 的早期迁移尝试——说明此项曾迁过并被暂停，原因与"Windows 侧已覆盖"吻合。
- 已连接 connector：`tdx-connector`、`tushare`、`mx-ds-mcp`（东财）、`westock-mcp`、`wind-finance`、`github`、`agent-mail`。
- **未接入**：悟道（`mcp_wudao`）、`sina-finance`、`iFind`、`full-link`、`mcp_plugin_TDX_tdx`——这些是 TRAE 插件体系，message 中的 F3/F5 降级链直接指向它们。

**证据 4｜TRAE 侧下单任务当前工作正常**（guard 日志实证）

```
rebalance_daily_guard_20260922_100501 : 标记已存在（rebalance_20260922.json executed_live=true）→ TRAE 09:45 已执行，跳过
rebalance_g2_guard_20260922_100701    : rebalance_g2_20260922.json 已存在 → G2 已执行（含 NOOP），跳过 ｜当日计划含 3 条指令
rebalance_ens_guard_20260922_100801   : rebalance_g2_ens_20260922.json 已存在 → ENS 已执行（含 NOOP），跳过 ｜当日计划含 5 条指令
```

三项下单任务（09:45/09:50/09:52）在 2026-09-22 全部按时完成，三项 guard 均走"已执行→跳过"分支。

**证据 5｜guard 的设计前提（源码注释原文）**
> 「触发时点选 10:05：TRAE 09:45 任务 ~09:46 触发、约跑 12 分钟至 ~09:58，10:05 已能区分"已执行/未执行"，且避免与 TRAE 任务并发导致重复下单。」——`rebalance_daily_guard.ps1`

即：**guard 的时点与幂等判据均以"TRAE 任务在 09:45/09:50/09:52 触发"为契约**。迁移会改变契约的另一端。

---

## 三、逐条判定明细

| # | 任务 | 文档时间 | 判定 | 依据 |
|---|---|---|---|---|
| 1 | 开盘实时复核（--live） | 09:45 | ⛔ 不建议迁 | 与 10:05 guard 存在**完成时间越界竞态**，详见 §四·风险 1 |
| 2 | 候选预生成 | 09:15 | ✅ 可直接迁 | 纯本地 LightGBM 推理，不依赖行情接口与 LLM；无 Windows 对应项；幂等（D_model_top10.csv 已存在即退出） |
| 3 | 集合竞价增强预判 | 09:25 | ⚠️ 改造后迁 | 依赖 `mcp_tdx`/`sina-finance`/`悟道`/`iFind` 降级链，WorkBuddy 无对应 connector；F2 主源为本地 parquet 不受影响 |
| 4 | G2换仓（--live） | 09:50 | ⛔ 不建议迁 | 同 #1，对 10:07 guard |
| 5 | 融合换仓（--live） | 09:52 | ⛔ 不建议迁 | 同 #1，对 10:08 guard |
| 6 | 午休持仓报告 | 11:35 | ⛔ 不建议迁 | `Quant_P16_DE_Report_Midday` 12:00 已跑**同一脚本**；脚本内已有幂等（T-20260903-023「TRAE/Windows 双触发保护」） |
| 7 | 盘后持仓复盘 | 15:40 | ⛔ 不建议迁 | `Quant_P16_DE_Report_Close` 16:20 已跑同一脚本 |
| 8 | G2日终对账 | 15:45 | ⚠️ 部分重叠 | 无同时刻冲突，但 `Quant_P16_DE_Reconcile_Review`（16:40）已做只读核验；迁入需明确二者分工 |
| 9 | 融合日终对账 | 15:50 | ⚠️ 部分重叠 | 同上 |
| 10 | Tushare刷新 | 19:30 | ⛔ 不建议迁 | `Quant_Tushare_Moneyflow_Refresh` 19:30 同时刻同目的；WorkBuddy 侧已有同名 PAUSED 条目 |
| 11 | 夜间检修 | 01:00 | ⛔ 不建议迁 | `Quant_P16_NightlyCheck_0100` 01:00；且 message 自述「先确认 Windows 任务已自动体检，勿重复 --fix」 |
| 12 | 夸克行情每周检查 | 周一 03:00 | 🚫 阻塞 | `quarkclouddrive` 为 TRAE 专有 skill，需先移植；且 CLI 有 >50MB 单文件下载上限（code 23018）需用户手动补 |
| 13 | 成本锚生成 | 09:00 | ⛔ 不建议迁 | `Quant_P16_CfgPremarket_0900` 09:00 已覆盖（文档已标 Paused） |
| 14 | 模拟账号盘后监控 | 15:05 | ✅ 可直接迁 | 只读行情查询，WorkBuddy `tdx-connector` 可承载；输出目录 `d:\Workspace` 存在。**唯一缺陷**：文档「工作目录：见 message」，而 message 内未写明，需补 |

---

## 四、风险与前置改造清单

### 风险 1（最高）：下单任务的"完成时间越界"竞态

**机理**：guard 的幂等判据是产物文件（`rebalance_<date>.json executed_live=true` / `rebalance_g2_<date>.json` / `orders_<date>.json`）。若 WorkBuddy 任务在 guard 触发时刻（10:05/10:07/10:08）**尚未完成落地**，guard 判定为"未执行"→ 补跑 `--live` → **重复下单**。

这比 AGENTS 红线 T-20260910-008 描述的"同时刻双调度"更隐蔽：两者并不并发，而是**先后顺序颠倒**。TRAE 侧实测余量为 7 分钟（09:58 完成 vs 10:05 检查），WorkBuddy 作为 LLM Agent 驱动的自动化，其"启动 + 多步取数 + 下单"耗时无实测数据，**不能假定与 TRAE 相同**。

**缓解（三选一，推荐 ①+②组合）**：
1. 把 WorkBuddy 触发时间提前：09:45→09:38、09:50→09:45、09:52→09:47，为 guard 留 ≥15 分钟余量；
2. 把三个 guard 后移至 10:20/10:22/10:24（需同步更新 AGENTS.md 与 guard 注释中的时点契约）；
3. 下单类任务**不迁**，维持 TRAE 主触发 + Windows guard 兜底的双层结构。

### 风险 2：WorkBuddy 自动化的可靠性等级低于 Windows 计划任务

官方文档与多篇实践记录一致：**WorkBuddy 自动化依赖客户端常驻运行**，客户端退出或系统休眠即不触发；而 Windows 计划任务为 `S4U` 会话模式，**开机即跑、不依赖用户登录**（AGENTS.md 已记录 39 项任务全部切换 S4U 的动因正是"消除桌面弹窗"）。

→ 对 09:45/09:50/09:52 这类**硬时点、不可补救**的动作，迁移属于可靠性降级。对只读/报告类任务（#2/#3/#8/#9/#14），该差异影响可接受（失败有 guard 兜底或次日重跑）。

### 风险 3：TRAE 专有数据源在 WorkBuddy 不存在

message 中的取数降级链引用 `mcp_tdx`、`mcp_wudao`、`sina-finance`、`iFind`、`东财curl`、`full-link`、`mcp_plugin_TDX_tdx`——均为 TRAE 插件。WorkBuddy 侧对应能力为 `tdx-connector` / `tushare` / `mx-ds-mcp` / `westock-mcp` / `wind-finance`。
→ 涉及多源交叉验证与降级的任务（#3 最重，#1 次之）**必须重写取数段与降级链**，否则 message 中显式点名的工具会逐一失败、耗尽重试预算。F2 主源（本地 Tushare parquet）不受影响。

### 风险 4：迁移清单不覆盖完整依赖链

文档 14 项**未包含**两条前置链路，而 `#3/#4/#5` 直接依赖其产物：
- `Quant_P16_G2Candidates_Night`（20:05，产出 G2/ENS 夜间候选）
- `quant_daily_update`（16:30，产出 `D_model_top10.csv` 与 16:30 盘后管道）

→ **这两条应继续保持 Windows 调度，不迁**。若日后 TRAE 侧整体下线，必须确认这两条的前置产物供给不变，否则 09:25/09:50/09:52 全部失去候选来源（届时 `rebalance_g2.py` 会因"候选缺失"中止并告警——**fail-loud 是设计内的，不会裸奔下单**）。

### 风险 5：文档内部不一致（迁移前需定稿）

| 位置 | 问题 |
|---|---|
| #11 夜间检修 | 表头写「每日 01:00」，明细写 `0 1 * * 1-5`（工作日）——**自相矛盾**。Windows 侧 `Quant_P16_NightlyCheck_0100` 为每日无限定，建议统一为每日 |
| #14 模拟账号监控 | 明细写「工作目录：见 message」，但 message 正文无工作目录 |
| #6 / #7 | 文档时间（11:35 / 15:40）与 Windows 实跑时间（12:00 / 16:20）不一致，且脚本幂等 → 若两者并存，先到的先执行、后到的跳过推送，**报告产出时间取决于谁先跑**，需拍板单一时间 |
| 通用 | cron 无"交易日"概念，节假日仍会触发；靠 message 内 `is_trade_day.py` 守卫（文档已保留 ✅）。Windows 侧 `Days=62`（Mon–Fri）同理，行为一致 |
| 通用 | 迁移方式段落写「全部重建并验证 OK 后，再暂停/删除 TRAE 侧」——**正确**，但需补充「删除 TRAE 侧前先确认 WorkBuddy 已连续 3 个交易日稳定产出」 |

### 风险 6：lark-cli 路径已失效

`...\lark\1.0.4\bin\lark-cli.exe` 不存在（实际 `1.0.5`）。所有 message 中的飞书推送段与 `push_alert_card.py` / `push_fill_card.py` / `push_daily_summary_card.py` 的调用路径需统一更正。此外 WorkBuddy 自带独立 lark CLI（`C:\Users\Administrator\.workbuddy\binaries\node\cli-connector-packages\lark-cli`）与飞书 connector，迁入任务**建议改用 WorkBuddy 原生推送通道**，避免双套凭据（TRAE 侧 message 还要求移除 `LARKSUITE_CLI_APP_ID` 等环境变量）。

---

## 五、建议执行路径

**第一阶段（零改造，可立即做）**
1. 迁 #2 候选预生成（09:15）作为冒烟验证对象——纯本地脚本、无外部依赖、幂等、失败不阻塞下游。
2. 迁 #14 模拟账号盘后监控（15:05）——需在 message 中补全工作目录与输出路径的绝对路径。

**第二阶段（改造后做）**
3. 重写 #3 集合竞价预判的取数降级链（映射到 WorkBuddy connector 体系），再迁 09:25。
4. 明确 #8/#9 与 `Quant_P16_DE_Reconcile_Review`（16:40）的分工后，再迁 15:45/15:50。

**第三阶段（需拍板）**
5. #1/#4/#5 下单任务：按 §四·风险 1 的方案 ①/② 改造 guard 契约后再迁；**或**维持现状不迁。建议先不迁。
6. #12 夸克检查：先移植 `quarkclouddrive` skill 到 WorkBuddy（触发 Skills 安全审计流程），再迁周一 03:00。

**明确不做**
7. #6/#7/#10/#11/#13：Windows 计划任务已覆盖，迁入只会产生第三层调度。

**每阶段收尾动作**
- 每个任务迁入后在 WorkBuddy 面板「立即执行」跑通一次，再开定时；
- 迁稳 3 个交易日后，才暂停 TRAE 侧对应任务（消除 T-20260910-008 双调度）；
- 同步更新 `全局控制台.md` 任务看板与 `.qoder/repowiki/`（AGENTS.md 硬规则）。

---

## 附录：cron → WorkBuddy rrule 映射

WorkBuddy 自动化调度支持 `DAILY / WEEKLY / MONTHLY / YEARLY / HOURLY`，不支持 5 字段 cron 直填；本文档 14 项**全部可无损表达**：

| 文档 cron | 含义 | WorkBuddy rrule |
|---|---|---|
| `45 9 * * 1-5` | 工作日 09:45 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=45` |
| `15 9 * * 1-5` | 工作日 09:15 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=15` |
| `25 9 * * 1-5` | 工作日 09:25 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=25` |
| `50 9 * * 1-5` | 工作日 09:50 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=50` |
| `52 9 * * 1-5` | 工作日 09:52 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=52` |
| `35 11 * * 1-5` | 工作日 11:35 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=11;BYMINUTE=35` |
| `40 15 * * 1-5` | 工作日 15:40 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=15;BYMINUTE=40` |
| `45 15 * * 1-5` | 工作日 15:45 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=15;BYMINUTE=45` |
| `50 15 * * 1-5` | 工作日 15:50 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=15;BYMINUTE=50` |
| `30 19 * * 1-5` | 工作日 19:30 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=19;BYMINUTE=30` |
| `0 1 * * 1-5` | 01:00（文档自相矛盾） | `FREQ=DAILY;BYHOUR=1;BYMINUTE=0`（建议按每日统一） |
| `0 3 * * 1` | 周一 03:00 | `FREQ=WEEKLY;BYDAY=MO;BYHOUR=3;BYMINUTE=0` |
| `0 9 * * 1-5` | 工作日 09:00 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0` |
| `5 15 * * 1-5` | 工作日 15:05 | `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=15;BYMINUTE=5` |

> 注意：WorkBuddy 每个自动化需单独配置工作目录（`cwds`），文档中「工作目录」一栏需在创建时显式填入；message 内使用相对路径的脚本（如 `python is_trade_day.py`）依赖此项正确。

---

## 附：评估过程中的一次操作失误（如实记录）

评估期间执行计划任务枚举时，命令中误带入 `Reset-ScheduledTask`。事后核查确认**未造成损伤**：

- 38 项任务注册时间（`RegistrationInfo.Date`）**均未变为 2026-09-23**，即无任何任务被重新注册；
- `LogonType` 全部保持 `S4U`；
- 动作与参数（`Execute` + `Arguments`）完整无误；
- 关键任务 `Enabled=True` 且 `NextRun` 时点正确（V13 guard 09-23 10:05、Tushare 19:30、NightlyCheck 01:00 等）。

**遗留观察项（与本次失误无关，但核查中发现）**：`Quant_P16_NightlyCheck_0100` 于 2026-09-22 01:00 退出码为 **1**（非 0），其余被抽查任务均为 0。建议核对 `data/schedules/nightly_check_task.log` 确认是"检出 FAIL 项"的正常返回，还是任务本身异常。
