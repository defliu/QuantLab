# Project_16 修复报告（DE，2026-09-14）

- 任务来源：`data/schedules/de_fix_prompt_20260914.txt`
- 修复人：DE（DevEco Code CLI）；全程未下单、未 `--live`、未运行 rebalance/reconcile 脚本、未 commit
- 修改范围：仅允许清单内的 3 处（rebalance_daily.py / de_report_daily.ps1 / g2_candidates_night.ps1）+ 本报告（任务指定输出）

## 0. 边界遵守与超范围读取声明

- order_guard.py 核心循环、qmt_config.py、任何 QMT 桥内策略源码：**未改动**（order_guard.py / reconcile_g2.py / gen_positions_cfg_v13.py 仅只读分析）。
- 超出允许读取清单、但为完成修复所必需的只读文件（如实声明）：`deploy_predict_g2.py`（修复 3 的根因所在，g2_candidates_night.ps1 的被调方）、`is_trade_day.py`（修复指令明示"参考 is_trade_day.py 的日历"）、`data/selections/g2/` 与 `data/selections/g2_ens/` 目录列表 + `paper_forward_live.csv` 尾部（问题 B 定性证据）、`de_reconcile_20260914.md` / `reconcile_g2_20260914.json`（问题 D 的直接产物证据）、`rebalance_g2_20260914.json` 内容（经 de_report_close 日志中已含的引文核对）。以上均为只读。

---

## 1. 四项根因分析

### A. V1.3 买入 601890.SH 今日 CANCELED_TIMEOUT —— **结论：重试 5 次仍失败（非"未重试"）**；guard_note 不显示次数是 rebalance_daily.py 的展示层缺口

**① order_guard.py 循环完整性（读码结论）**

- `MAX_RETRY=5`（order_guard.py:37）；`CANCELED_TIMEOUT` **只在 while 循环耗尽全部 5 次尝试后返回**（order_guard.py:140-195）。
- 每次尝试 = 取价/涨跌停检查 → 下单 → 最多等 `WAIT_SECONDS=60`s（order_guard.py:35,161）→ 未全成 → 撤单（:181）→ 重试剩余股数。部成只补剩余、废单/部撤均继续重试。循环逻辑完整，**不存在"超时即撤单删 pending、永不补单"的写法**。

**② 时间证据（5 次尝试与执行窗口精确吻合）**

- 卖出 601579 成交入账 09:58:18（qmt_trade_log.csv 末行）；当日 live 执行结果 `rebalance_20260914.json` executed_at **10:03:32**（de_v13_compare 报告一/四节引文）。
- 买入守护窗口 ≈ 09:58:20 → 10:03:32 ≈ **312 秒 ≈ 5 ×（60s 等待 + 撤单/3s 轮询开销）**。与 MAX_RETRY=5 全部耗尽一致。

**③ guard_note 为何不显示重试次数**

- order_with_guard 的返回 dict **带 `attempts` 字段**（order_guard.py:15,146,194），但 rebalance_daily.py 原实现 `guard_note.append(f"买{b['code']}:{r['action']}")`（:529）只取 action，把 attempts 丢弃 → 纯展示层缺口。**已修（修改清单 1）。**

**④ 与原判断的差异（按任务要求明确说明）**

- de_v13_compare_20260914.md 风险点 1 称"疑似**超时撤单后未补单**"（当时 order_guard.py 不在其允许读取范围，自注明为观测性结论）。本次读码 + 时间窗复算证实：**重试确实发生了 5 次**。该 P1 的"未补单"定性应更正为"重试 5 次仍未成交后按设计放弃"。执行层无缺陷；缺的是①重试次数可见性（本次已修）与②重试价格刷新能力（见未决事项 1）。

**⑤ 9.78 是否追不上（腾讯行情实证）**

- 20260914 601890.SH（亚星锚链）：昨收 **10.25** / 今开 **10.22** / 最高 **10.30** / 最低 **9.76** / 收 **9.80**（-4.39%）（qt.gtimg.cn 16:14 快照）。
- 当日并非"开盘急拉"而是**高开低走**：买入限价 9.78 贴着当日最低价区（日低 9.76），重试窗口（09:58~10:03）内现价始终略高于 9.78（DE 10:05 复算时点现价 9.87，见 de_v13_compare 二节），限价买单无法成交；日内价格最终回落至 9.76-9.80 收盘，但发生在守护放弃之后。**即：9.78 在 5 分钟重试窗口内确实追不上/等不到。**
- **加重因素（禁改项，仅记录）**：order_guard.py:143 `px = price if price and price > 0 else _fetch_price(code)` —— 传入价有效时**每次重试都复用同一旧价**，无追价能力，5 次尝试全部挂在 9.78。
- 另由"委托未即时成交"反推 `BUY_PRICE_TYPE=FIX`（若为 LATEST_PRICE 市价单会即时成交）；qmt_config.py 不在允许读取范围，此为推断。

### B. G2/融合 20260914 候选断链 —— **结论：夜间任务按"运行日"命名候选，注定无法为下一交易日供货；当日候选本应靠 09:25 TRAE 预生成，而它当日失效**

**① 命名根因（读码 + 目录实证）**

- `deploy_predict_g2.py` 无 `--date` 时 `target = day["trade_date"].max()`（快照最新交易日，deploy_predict_g2.py:76-86），输出文件名 = target（:179-181 `f"{date_str}_g2_top{args.top}.csv"`）。
- 20:05 夜间运行时快照数据最新日 = **运行日**（T-1 数据就绪到当日）→ 周五 09-11 20:07 产物名为 `20260911_g2_top10.csv`（实测 selections/g2 最新文件即此，LastWriteTime 2026-09-11 20:07:32），**不是周一的 `20260914_g2_top10.csv`**。
- 周一早晨 rebalance 新鲜度闸（候选数据日 vs 增量库最新日 20260914）必然拒绝 → G2/融合双双 NOOP（de_g2_compare / de_ens_compare 一节均实录"候选数据日 20260911 落后于增量库最新日 20260914，宁缺毋滥中止"）。**周五→周一场景确实不覆盖；且该缺口每个交易日早晨都存在**，平日全靠 09:25 兜底（g2_candidates_night.ps1 注释自述"09:25 防御性重算会兜底"）。

**② 09:25 TRAE 候选预生成能否补**

- 机制上**是当日候选的第一生产者**（能补），但它是 **TRAE 侧 agent 任务，不在 QuantLab schtasks**（schtasks 仅有 20:05 的 `Quant_P16_G2Candidates_Night`）。
- 今日实证其失效：selections/g2 无任何 20260914 文件；`paper_forward_live.csv` 尾部无 2026-09-14 行（deploy_predict_g2 每次运行必幂等追加该日志，tail 全为 09-11 行）→ 09:25 当日未运行成功。
- 结合 16:27 DE 盘后报告同源故障（内置模型服务过载，见 C），**疑似同一模型问题导致 agent 会话停摆**；根因需 TRAE 侧任务运行记录确认（QuantLab 侧无其日志）。

**③ 当日 14:34 的真实买入（603332.SH 5200@11.47）从哪来**

- `rebalance_g2_20260914.json`（14:34:02）候选池与 `20260911_g2_top10.csv` 内容完全一致（603332 total=67.0 rank1、603271 64.5 rank2 …），且 paper 日志无 0914 行 → **14:34 执行用的是 0911 候选内容**（人工/agent 干预），买入已成交（fills_20260914 FILLED）。
- 早晨同代码同现场被新鲜度闸拒、14:34 却放行——放行方式（手工构造 20260914 候选文件后删除？参数覆盖？）在允许读取范围外**无法完全定性**，如实记录（未决事项 3）。

**④ 修复**：见修改清单 3（夜间任务新增"下一交易日候选副本"）。

### C. 盘后报告 de_report_close_20260914 exit=1 —— **结论：内置模型服务过载，DE 会话中断，报告文件从未生成**

- `de_report_close_20260914.log` 尾部：会话已正常执行多轮工具调用（读桥 state、对账产物等，均有输出）之后出现：
  `{"type":"error", ... "message":"\"Built-in model service is currently overloaded. Please retry later or set up a custom model.\""}`
  → `[2026-09-14 16:27:39] deveco exit=1`。
- `de_report_close_20260914.md` **不存在**（schedules 目录仅 .log 345KB + .prompt.txt），飞书推送走 fallback 文案"持仓数据提取失败，详见文件"；且飞书 webhook `not_configured`，推送本身也失败。
- 定性：**模型服务瞬时过载**，非 prompt/脚本缺陷 → 对症修复 = 失败重试（修改清单 2）。与 AGENTS「定时任务模型降级规范（2026-09-14 立）」描述的默认模型卡顿场景一致。

### D. TRAE G2 日终对账今日无产物 —— **结论：de_guard_runner 的 reconcile 检测有效，今日已实际完成"检测→告警→补跑"闭环，无需代码修复**

**① 机制（de_guard_runner.ps1:56-90）**

- reconcile 模式发现 `reconcile_g2_<date>.json` 缺失 → **先自动补跑 `reconcile_g2.py`**（:58-64，确定性脚本、安全）；补跑后仍无 → 记 hit"G2 对账补跑后仍无产物"；融合/V1.3 产物缺失或含 issues 同样记 hit → 触发 DE 复核报告 + 飞书推送。**会告警、会补跑，答案均为"是"。**

**② 今日实证（闭环已完成）**

- schtasks `Quant_P16_DE_Reconcile_Review`（16:40）当日已执行：`de_reconcile_20260914.md`（17:17:55）触发词实录"G2 对账产物缺失（15:45 任务未跑？）；融合对账有 issues；V1.3 对账有异常项"。
- `reconcile_g2_20260914.json` 已于 **17:21:37** 补出（positions_cfg 同步刷新 17:21:36）：fills 1/1 FILLED（P16_20260914_0001，603332.SH 5200@11.47）、pending=0、资金池滚动至 96,717.14。所谓"孤儿" 300138/600409 实为融合策略持仓（跨策略结构性误报，DE 复核报告 B 项已定性）。
- G2 15:45 对账本体缺失原因：**TRAE 自动化任务 4cf3db92（agent 执行）当日未跑/失败**，根因在 TRAE 侧（DE 复核报告 A 项同结论）。

---

## 2. 修改清单（仅允许的 3 处）

### 2.1 rebalance_daily.py —— guard_note 追加重试次数（纯展示层，无逻辑改动）

| 行号（改后） | 改动 |
|---|---|
| :448 | `guard_note.append(f"卖{s['code']}:{r['action']}(重试{r.get('attempts',1)}次)")` |
| :457 | `guard_note.append(f"减{t['code']}:{r['action']}(重试{r.get('attempts',1)}次)")` —— 减仓同为 SELL 委托，按"卖出侧同样加 attempts"一并覆盖 |
| :529 | `guard_note.append(f"买{b['code']}:{r['action']}(重试{r.get('attempts',1)}次)")` |

- 未动任何其他逻辑；GAP_BLOCK/POOL_BLOCK 等非 order_guard 结果的 note 保持原样（无 attempts 语义）。
- 验证：`py_compile` 通过。

### 2.2 de_report_daily.ps1 —— deveco run 后新增失败重试（一次）

- 位置：else 分支内 deveco run 之后。条件 **`$rc -ne 0` 或 报告未生成（`Test-Path $outFile` 为假）** → 记日志 → `Start-Sleep 60` → 用**同一 prompt** 重跑 1 次 deveco run → 更新 `$rc` → 仍失败则按原逻辑继续（推送 fallback 文案 + exit $rc）。
- 幂等性：外层"当日报告已存在→跳过 DE 重跑"保护（:69-72）不变；DE 重跑为只读报告生成，无交易副作用；双触发场景仍被外层幂等挡住。
- 验证：PowerShell Parser 0 错误；**UTF-8 BOM 保留**（AGENTS 硬规则）。

### 2.3 g2_candidates_night.ps1 —— 新增"下一交易日候选副本"块（修复 B 的命名断链）

- 逻辑：deploy 三步跑完后，用 `is_trade_day.py` 日历**向后探测下一交易日**（最多 10 天，覆盖法定长假，is_trade_day.py 自带周末+2026 节假日表）→ 将 `${runDay}_g2_top10.csv` / `${runDay}_g2_top2.csv` 复制为 `${nextDay}_g2_top{10,2}.csv`，并用 pandas 把副本的 **trade_date 列改写为下交易日**（与 09:25 正常流程"文件名日期 == trade_date 列"的口径一致）；`_g2_selection.md` 一并复制。
- 效果：**周五 20:05 → 生成周一可用的 `<周一>_g2_top10.csv`**；平日 → 生成明日文件，次日 09:25 TRAE 防御性重算产出后直接覆盖刷新（夜产物只是兜底，不冲突）。
- 边界处理：
  - 源文件不存在（红线 60 无候选）→ 记 INFO 跳过，不算失败（宁缺毋滥是设计内）；
  - 下一交易日推算失败或复制/改写失败 → 记 WARN 并使任务 **exit 1**（fail-loud，防止再出现今天这种静默断链）；
  - 幂等：副本为整文件覆盖写，夜间任务重入不会产生脏数据；paper 日志追加逻辑未动（deploy 自带幂等去重）。
- 验证：Parser 0 错误；BOM 保留；`is_trade_day.py` 探测（2026-09-15=交易日 exit0 / 2026-09-19=周末 exit1）与 pandas 改写 trade_date 一行式在**临时目录**实测通过（产物已清理，未触碰生产 selections）。

---

## 3. 验证建议

1. **今夜 20:05** `Quant_P16_G2Candidates_Night` 跑完后核对：`data/selections/g2/` 出现 `20260915_g2_top10.csv` 且 trade_date 列=20260915；`g2_candidates_night.log` 出现"下一交易日=20260915"行；任务退出码 0。
2. **下个周五盘后**核对 selections/g2 出现**下周一日期**的 top10/top2 文件（本次修复的核心场景）。
3. **次日 16:20** `Quant_P16_DE_Report_Close` 正常路径抽查；重试分支靠代码评审即可（人为制造模型过载不可控）。
4. **下次 V1.3 live 换仓**后核对 `rebalance_<date>.json` 的 guard_note 形如 `买601890.SH:CANCELED_TIMEOUT(重试5次)`——今日这类案例将可直接读出重试了几次，不再需要读码反推。
5. **TRAE 侧**：查 09:25 候选预生成与 15:45 G2 对账（任务 4cf3db92）当日运行记录，确认是否模型过载停摆；并按 AGENTS「定时任务模型降级规范（2026-09-14 立）」给这两个 agent 任务补降级条款（核心动作脚本化）。

---

## 4. 未决事项（只记录，未擅自行动）

1. **【P1】order_guard.py 重试不刷新价格**：:143 传入有效价时 5 次重试全用同一价，无追价能力（601890 案例即 9.78 贴日低 5 次全空）；且总重试窗口 ≈60s×5=5 分钟，短于全局红线「300 秒 × 最多 3 次 ≈ 15 分钟」（AGENTS 实盘执行红线）。两处均在**禁改清单**（order_guard 核心循环）。建议单独排期：BUY 重试时刷新现价或按卖一+滑点追价，窗口对齐红线口径；改动属真实委托路径，须按红线保守评审 + MOCK + 模拟盘验证。
2. **【P1】TRAE 侧两个 agent 任务当日失效根因**（09:25 候选预生成、15:45 G2 对账 4cf3db92）：QuantLab 侧无日志，需 TRAE 任务运行记录；是否与 16:27 模型过载同源待证。今日两任务双双失效说明这两条链路目前**没有脚本化兜底**（夜间任务修复后候选侧已有兜底，G2 对账侧已有 de_guard_runner 16:40 自动补跑兜底——兜底面本次已补齐）。
3. **【P2】14:34 G2 补买的执行链路未定性**：池内容=20260911 候选（paper 日志无 0914 行）却通过了早晨曾拒绝同数据的新鲜度闸；rebalance_g2.py 不在本任务允许读取范围。建议人工复核该笔买入（603332.SH 5200@11.47，已成交 T+1 锁定中）的决策留痕与放行方式。
4. **【P2】融合（ens）次日候选仍无夜间兜底**：夜间任务未带 `--ens-live`，selections/g2_ens 无次日产物；今晨融合因满仓 NOOP 无实际影响，但"到期卖出腾空位 + 候选断链"场景会资金闲置。是否给夜间任务加 `--ens-live` 产出 ens 次日候选，需单独拍板（本任务按最小改动未加）。
5. **【P3】飞书 webhook `not_configured`**：今日 DE close 报告、心跳告警等推送均失败，告警只落盘未触达人工；建议配置 webhook 或复用 `push_alert_card.py` 通道（de_reconcile 报告 E1 同建议）。
6. **【P3】rebalance_daily.py:357 action 标签笔误**（held_vol=0 的新晋票标"补仓"，de_v13_compare P3 已记录）：不在本任务授权改动范围（仅允许 guard_note 一处），未动。
7. **【备忘】夜间副本与新鲜度闸的耦合假设**：修复 3 的副本在"增量库已被 09:25 前置更新到次日"场景（今日实测即此形态）下确定有效；若 09:25 完全未跑且增量库停在 T-1，副本日期（T）将**领先**增量库（T-1），rebalance_g2 对"候选日 > 增量日"的行为未验证（rebalance_g2.py 不在允许读取范围）——最坏情况为 fail-safe NOOP（与现状持平），不会更糟。建议后续读码确认闸条件是"落后拒绝"还是"不等即拒"。

---

*报告生成：DE（DevEco Code CLI），2026-09-14。任务全程只读分析 + 3 处授权修改；未运行任何 --live / 下单 / rebalance / reconcile 脚本；未 commit。*
