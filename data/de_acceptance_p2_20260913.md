# DE 独立验收报告：2026-09-13 晚 P2 技术债修复（T-20260913-001 收尾）

- **验收日期**：2026-09-13（周日晚，继 DE 审查报告 `de_audit_self_iteration_20260913.md` 之后）
- **验收对象**：09-13 晚批 P2 修复 10 项（P2-2/3/4/5/7/8/9/10/11/12/15），另对 P2-1/6/13/14 做证据性核对
- **验收方式**：只读静态审查 + 逐行号核对 + BOM 首字节实测 + PS5.1 语法解析（Parser::ParseFile，不执行脚本）。未运行任何训练/回测/下单，未修改除本报告外任何文件。
- **结论速览**：**10 项验收对象全部真实落地（8 修复/注记核实 + 1 待办登记核实 + 1 接受核实），无 P0/P1 新问题**；另发现 2 项 P2 级残余/盲区（N1 降级闸评估路径静默解冻、N2 互斥告警不对称）与 3 项轻微观察，建议列入下批。**本批 P2 可关闭**。

---

## ① 验收结论摘要表（P2-1~15 逐项）

| # | 问题（原审查③节） | 本验收结论 | 关键证据（文件:行号） |
|---|---|---|---|
| P2-1 | M3「连续 3 日」深度劣化才自动回退 | **已修复（上轮，本验收证据复核 PASS）** | rollback_check_g2.py:37 `AUTO_STREAK_DAYS=3`；:56-63 `update_streak`（按 promoted_at 隔离、非深度归零）；:182-187 `streak >= AUTO_STREAK_DAYS` 才触发 |
| P2-2 | gate 回滚后漂移复查仍用被拒候选 | **已修复（PASS）** | run_scheduled.ps1:331-338（注释 331-332、重读指针 333-336、live≠候选即跳过 337-338） |
| P2-3 | enh 新鲜度 exit 1 日志文案误导（「视为新鲜」） | **已修复（PASS）** | run_scheduled.ps1:213-215（exit 1 → 「无法判定（merged_daily_full 缺失，fail-safe 放行）」告警）；check_enh_freshness.py:9 退出码语义已文档化 |
| P2-4 | 冻结标记消费端 fail-open 静默 | **已修复（PASS，带残余 N1）** | paper_forward_downgrade.py:112-123（is_frozen 损坏/解析失败 → :122 打印告警，保留 fail-open） |
| P2-5 | ①G2 读 ENS 账本 fail-open 静默；②ENS hold_dates 无陈旧清理 | **已修复（PASS，带新发现 N2）** | ①rebalance_g2.py:434-438（缺失 :435 告警 / 读取失败 :437-438 告警）；②rebalance_ens.py:112-117 补陈旧清理，对齐 rebalance_g2.py:111-117 |
| P2-6 | E6 快照新鲜度未校验 | **已修复（上轮；本验收未复核）** | PROJECT_MEMORY.md:1021 记载「generated_at >7 天 WARN」；nightly_check.py 不在本验收允许清单，未直接核对 |
| P2-7 | 漂移复查 argmax 无显著性容差 | **已修复（PASS，边际判定合理）** | config_drift_recheck.py:123-129（:125 `DRIFT_MARGIN=0.0005`；:129 需 config 变化 且 hist_now 可得 且 差>边际） |
| P2-8 | V1.3 臂 N10 vs 实盘 N5 口径不匹配 | **已注记（PASS）** | paper_forward_downgrade.py:38-41（ARMS 口径注记：V1.3 臂为候选口径，与 ab_stats 三臂同源，有意为之） |
| P2-9 | 回滚 CSV 回填 --hold 10 vs G2 live N15 | **已注记（PASS）** | rollback_check_g2.py:16-18（docstring：N10 回填 vs N15 已知近似，对比同源公平，待统一前向底座分臂 hold） |
| P2-10 | TW 调度写 QMT_POOL 与 owner_boundary 表述张力 | **已注记（PASS）** | tw_schedule_snapshot.json:19-22（「TW 触发 QuantLab 脚本 ≠ 双写，仅直接写同一产物才冲突」+ conflict_with×Active 才 FAIL 的判定规则） |
| P2-11 | feature_health --check 仍写报告文件 | **已修复（PASS，带观察 N5）** | feature_health_weekly.py:124-127（json 门控）、:160-162（md 门控）、:170-174（推送门控）、:168（「json/md 不落盘，未推送」提示） |
| P2-12 | ENS 与 G2 预存分歧（预算基数/大盘门控/hold_dates fail-open） | **已列待办（PASS）** | PROJECT_MEMORY.md:1038 三项齐列；代码佐证：rebalance_ens.py:381（预算未扣保留投入）、build_plan 无门控段（对照 rebalance_g2.py:409-424）、:328-335（hold_dates 缺失即卖） |
| P2-13 | _do_rollback 指针写入非原子 | **已修复（上轮，本验收证据复核 PASS）** | rollback_check_g2.py:113-121（tmp + os.replace 原子写，失败打印并 return None） |
| P2-14 | 资金池 realized 不含费（G2/ENS 同口径近似） | **接受（无代码改动，登记一致）** | PROJECT_MEMORY.md:1039「两桥一致可接受」 |
| P2-15 | 降级闸标记「每日幂等重写」描述不精确 | **已注记（PASS）** | 纸面自动降级闸_设计_20260913.md:76（「标记文件每日幂等重写……幂等设计，重跑无副作用」） |

**横切硬规则复核**：run_scheduled.ps1 首字节实测 `EF BB BF`（UTF-8 BOM 完好）；PS5.1 语法解析（`Parser::ParseFile`，未执行）**0 errors**。P2-2/P2-3 编辑未破坏 BOM、未引入语法错误。

---

## ② 逐项核对明细

### 1. P2-2：gate 回滚后漂移复查重读 live 指针 —— PASS

**修复位置**：run_scheduled.ps1:329-353（原审查指认 L329-343 区段，实际修复完整覆盖 L331-352）。

**逻辑链核对**（验收重点 a）：
- gate 段（:311-318）FAIL 时 `gate_strategy_layer.py --rollback` 会把 live 指针改写为 prev_model；
- 复查段执行前**重读指针**：:333-336 从 `g2_live_model.json` 取 `$g2CurFinal`；
- :337 判定 `$g2New` 为空 / `$g2CurFinal` 为空 / **两者不等** → :338 跳过复查并打出说明日志（含当前 live 值）。

**验证场景推演**：
| 场景 | $g2New | $g2CurFinal | 行为 | 正确性 |
|---|---|---|---|---|
| gate FAIL 已回滚 | 候选路径 | prev 路径（≠） | 跳过 + 告警 | ✓ 复查被拒候选无意义 |
| gate PASS 保留 | = live | = live | 执行复查（:341 重读指针 meta_path，与 live 同源） | ✓ |
| gate 运行异常（exit≠0/2） | = live（未回滚） | = live | 执行复查 | ✓ live 仍是 promote 态 |
| enh 阻断未重训（$skipG2） | $null | — | 跳过（:337 第一支路） | ✓ 行为正确（文案归因见 N3） |
| promote 后指针损坏/读不出 | 候选 | $null | 跳过 + 告警 | ✓ fail-safe |

meta 取用亦修正：:340-341 改从**指针文件**读 `meta_path`（原审查指出的 model/meta 错配路径已消除——meta 与实际 live 指针绑定，不再从候选文件名推导后再错配）。

**审查重点④（PowerShell 陷阱）复核**：`$g2New` 跨 if 块可见（PS 不限块作用域）；`-or` 链短路顺序正确；try/catch 包裹 ConvertFrom-Json，$null 初值先赋（:333/:340），无旧值残留。与原审查「$g2New 作用域本身无 bug」的结论一致，本修复未引入新陷阱。

### 2. P2-3：enh 新鲜度 exit 1 与 exit 0 日志区分 —— PASS

- run_scheduled.ps1:209-218 三分支：exit 2 → 阻断（:210-212 `$skipG2=$true`）；**exit 1 → :213-215 「无法判定（merged_daily_full 缺失，fail-safe 放行）——继续 G2 重训，但需核查数据链」**；else → 「新鲜，继续」。原「视为新鲜」误导文案已消除。
- check_enh_freshness.py:9 退出码语义文档化（0 新鲜 / 2 陈旧阻断 / 1 无法判定 fail-safe），与 ps1 分支一一对应；:52-54 `latest is None` → 明确打印「fail-safe 放行但告警」并 return 1。
- **边界确认**：Python 未捕获异常也 exit 1 → 落入 fail-safe 放行分支且**有告警日志**（不静默）；exit 2 分支对 `$skipG2` 的门禁接线（:291 `if (-not $skipG2)` → :326-327 else 日志）保持原审查确认的正确结构。fail-open 但可见，符合拍板语义。

### 3. P2-4：is_frozen 损坏告警 —— PASS（fail-open 可见）

- paper_forward_downgrade.py:112-123：文件存在但 `json.load` 抛异常 → :122 打印 `「[降级闸] !! 冻结标记文件损坏/解析失败（<异常类型>），fail-open 视为未冻结——请核查 <路径>」` → return False。
- **可见性**（验收重点 c）：三个消费方（rebalance_g2.py:399-408 / rebalance_ens.py:356-365 / rebalance_daily.py，均 try/except 包裹 import）在 build_plan 执行路径内调用 is_frozen，stdout 告警随调度日志落盘（Run-Py 管道逐行 Log；TW 协调任务日志同理）。无飞书推送——满足「打印告警」的验收口径，但见 ③-N1 的评估路径残余与建议。
- 文件不存在 → 仍静默 return False（:115-116）：合理，首次运行前文件本就不存在，非损坏场景。

### 4. P2-5：互斥读告警 + ENS 陈旧条目清理 —— PASS（带新发现 N2）

**①rebalance_g2.py 读 ENS 账本**（:426-441）：
- 文件缺失 → :435 `「!! [重叠规避] ENS 账本文件缺失……无法互斥，双买风险回归，需核查」`；
- 读取/解析失败 → :437-438 同级告警（含异常信息）；
- 告警位于买入段（`n_slots>0 and pool and deploy_pct>0` 门内，:425）——冻结/T=0 无买入时不需要互斥，不产生误告警；告警后 fail-open 继续买入但**双买风险已被显式声明**。可见性同 P2-4（stdout → 调度日志）。

**②rebalance_ens.py `_update_hold_dates` 陈旧清理**（:96-119）：
- :112-117 补齐「账本（plan.ledger）已不存在的 code → 移除」清理，与 rebalance_g2.py:111-117 **逐行同构**（仅注释差异，:113 注明 P2-5b 修复动机「防 ENS 陈旧条目让 G2 持续过度排除」）；
- 清理仅在 `--live` 写单路径落盘（:460-461），dry-run 预览不落盘（:474-476）——与 G2 版一致，无副作用。
- 效果链确认：ENS 桥内 STOP/TP/TRAIL 出场后，陈旧条目次日在 `_update_hold_dates` 被清 → G2 反向互斥读到的 ENS 账本不再含已清仓票 → 过度排除消除。

### 5. P2-7：漂移判定 DRIFT_MARGIN=0.0005 —— PASS（边际判定合理）

**实现**（config_drift_recheck.py:123-129）：
- :125 `DRIFT_MARGIN = 0.0005`（0.05pp/日）；
- :129 漂移三条件：`config_changed`（扫描最优 ≠ 历史最优配置）**且** `hist_now is not None`（历史最优点本次确有有效回测值）**且** `(best["excess"] - hist_now) > DRIFT_MARGIN`。

**合理性评估**（验收重点 b）：
- **比较基准正确**：`hist_now` 是**同一新模型、同一回测区间**下历史最优配置的当次 excess（:127 从本次 results 中取），`best` 是同批次邻域点 argmax——同模型同区间纯配置效应对比，无跨批次噪声，比对比登记串解析值（hist_excess，仅显示用）科学；
- **边际量级合理**：G2 登记最优 excess ≈ +0.213%/日（0.00213），边际 0.0005 约为其 23%——邻域点须显著更优（≥0.05pp/日）才判漂移，足以滤除 3 点小网格的随机微差（原审查举例的 0.0002 级噪声被正确抑制）；反向不会把**显著**漂移误判为未漂移——真实有行动价值的配置漂移（值得走纸面前向 30 笔流程的）幅度通常 ≥0.05pp/日，低于此边际的「漂移」按制度本就不值得登记；
- **方向保守**：漂移仅登记不改实盘（:137、:153），边际误判的代价只是「少登记一个候选」，无实盘风险。
- 防御性缺口见 ③-N4（hist 点不在扫描集/回测失败时静默永不判漂移）。

### 6. P2-8/9：口径注记 —— PASS

- **P2-8**（paper_forward_downgrade.py:38-41）：ARMS 定义处注明「G2 臂 N15=live N15 一致；ENS 臂 N10=live N10 一致；**V1.3 臂 N10=纸面候选口径……与实盘 rebalance_daily HOLD_DAYS=5（到期制）不一致——评估窗口比实盘长，属候选口径有意为之（与 paper_forward_ab_stats 三臂同源），降级判定按候选口径统一」。与原审查 P2-8 的要求（确认是否有意并文档注明）完全对齐。
- **P2-9**（rollback_check_g2.py:16-18）：docstring 注明「ret 由 paper_forward_daily.ps1 以 --hold 10 回填（N10 口径），而 G2 live 实盘持有期 N15——已知近似（prev_top2_ret 同为 N10 口径，对比同源公平；待统一前向数据底座时按臂分 hold）」。同源公平性论证成立（对比双方同口径，不构成方向性偏差）。

### 7. P2-10：tw_schedule_snapshot owner_boundary 澄清 —— PASS

tw_schedule_snapshot.json:19-22：
- :20 明确「TW 侧任务多为协调/下指令（调用 QuantLab 脚本、核对告警），**不算产物写入者**……**TW 触发 QuantLab 脚本 ≠ 双写，只有 TW 任务直接写同一产物文件才构成冲突**（如已删的 eda9b0c3）」，并给出可执行判定规则「conflict_with 字段标记 + Active 才 FAIL」；
- :21 保留 schtasks_exclusive 清单。原审查指出的「表述张力」（18c0c8d2 等协调任务 vs 唯一 owner 声明）被该边界细化消解——nightly_check E6 按此规则巡检时不会对协调类任务误报。

### 8. P2-11：feature_health_weekly --check 严格只读 —— PASS

feature_health_weekly.py 逐门核对：
- json 落盘：:124-127 `if not args.check` 门控；
- md 落盘：:160-162 同门控；
- 飞书推送：:170-172 `alerts and not args.check` 门控；
- 路径打印：:164-168 check 模式改打「[CHECK] 仅评估：json/md 不落盘，未推送」。
原审查 P2-11a（--check 仍写文件）彻底修复；--check 下 ALERT 返回 0 的语义（原 P2-11b）未改、docstring 仍未注明，见 ③-N5（可接受，dry-run 为人工场景）。

### 9. P2-12：ENS 预存分歧待办登记 —— PASS

- PROJECT_MEMORY.md:1038：「P2-12 已列待办（非本批引入，涉口径一致，不碰实盘）：**ENS 预算基数未扣保留持仓投入**（G2 P1-4 修复未同步）、**ENS 无大盘门控**、**ENS hold_dates 缺失 fail-open 即卖**（G2 fail-safe 跳过）——列入下批对齐清单」。三项齐备。
- 代码佐证三项分歧真实存在（待办登记不是空话）：
  - 预算基数：rebalance_ens.py:381 `budget_each = capital * (1 - RESERVE) / n_slots`，未扣 kept_invest（对照 rebalance_g2.py:446-452 已修）；
  - 大盘门控：rebalance_ens.py build_plan 全段无 hs300/tier 逻辑（对照 rebalance_g2.py:409-424）；
  - hold_dates 缺失即卖：rebalance_ens.py:328-335 `if since:` 缺失时**穿透到卖出**（对照 rebalance_g2.py:367-373 fail-safe 跳过+告警）。
- 判定：登记如实、边界声明（不碰实盘、下批处理）合理。

### 10. P2-15：设计文档幂等重写澄清 —— PASS

纸面自动降级闸_设计_20260913.md:76：默认模式行补注「**注（P2-15）：标记文件每日幂等重写**（save_mark 无条件写，含当前统计字段 n/mean_excess），非『仅新触发才写』——幂等设计，重跑无副作用」。与实现（paper_forward_downgrade.py:228-229 `if not check_only: save_mark(mark)`）一致，文档措辞对齐完成。

### 11. P2-1 / P2-13（上轮修复，本验收顺手证据复核）—— 均 PASS

- P2-1：rollback_check_g2.py:36-38（AUTO_THRESHOLD/AUTO_STREAK_DAYS 常量）、:56-63（streak 按 promoted_at 隔离、非深度日归零、原子写）、:180-187（仅观察期结束+样本充足才计数，连续 ≥3 且 --auto 才回退）。与 M3 规格一致。
- P2-13：:113-121 `_do_rollback` 指针写入改 tmp + `os.replace` 原子写，失败打印异常并 return None（配合上轮 P1-2 的 exit 2 + 飞书，失败路径 fail-loud 链完整：:194-197）。

### 12. 横切：BOM + PS5.1 语法（验收重点 d）—— PASS

- **BOM**：run_scheduled.ps1 首三字节实测 `EF BB BF`（UTF-8 BOM 完好）。P2-2/P2-3 编辑未重蹈 P0-2 覆辙，AGENTS「编辑后必校验 BOM」硬规则本次被执行。
- **PS5.1 语法**：`[System.Management.Automation.Language.Parser]::ParseFile` 解析 0 errors（仅解析未执行）。含中文内容在 PS5.1 下可正常解码（BOM 在位前提）。

---

## ③ 新发现问题清单（P0/P1/P2）

**P0：无。P1：无。**

| # | 级别 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| N1 | **P2** | **降级闸评估路径仍可「静默解冻」**：`load_mark()`（paper_forward_downgrade.py:104-109）解析失败仍静默返回默认 dict；`_eval_all` 非检查模式随后 `save_mark` **覆写损坏文件**（:228-229）。若某臂此前 frozen=true、损坏时点其样本恰好不再满足触发条件（如 mean_excess 回升破 -0.0002），评估不会重新置 frozen → 冻结态被静默清除——恰是 P2-4 要防的语义，但 P2-4 只修了 is_frozen 消费路径，评估写入路径未同步。触发前提是标记文件损坏（save_mark 原子写，概率低）+ 冻结中 + 指标恰好回升，属低概率高危害组合。 | :104-109 vs :112-123（同文件内两路径行为不对称）；:214 `setdefault` 在 mark 重置后 frozen=False | load_mark 解析失败同样打告警；_eval_all 检测到「标记不可读」时 fail-safe：不覆写原文件（或先备份 .bak_corrupt 再写）+ 打印/飞书告警，与 is_frozen 的可见性对齐 |
| N2 | **P2** | **互斥告警修复不对称（审计盲区）**：rebalance_g2.py 读 ENS 账本缺失/失败已告警（:434-438），但 **rebalance_ens.py 读 G2 账本（:369-375）仍是静默 `except Exception: pass`、文件缺失也不告警**。同账户双桥，ENS 候选撞 G2 持仓的双买风险同样存在且不可见。原审查 P2-5 只列了 G2→ENS 单向，本批按清单修复亦只修该向——非本批引入的回归，而是审计清单本身的盲区。 | rebalance_ens.py:369-375 vs rebalance_g2.py:429-438 | 下轮把 G2 版 :429-438 的告警逻辑对称复制到 rebalance_ens.py:369-375（约 5 行） |
| N3 | P2（轻微，日志文案） | **run_scheduled.ps1:337-338 跳过文案归因不精确**：$skipG2（enh 阻断未重训）时 $g2New=$null → 落入「G2 promote 被 gate 回滚或指针异常」文案，实际原因既非 gate 回滚也非指针异常。行为正确（确实该跳过复查），且 :327 已有正确原因日志，仅影响事后日志审计的归因效率。 | run_scheduled.ps1:337-338 vs :326-327 | 文案拆两类：$g2New 为空 → 「本轮未 promote/被阻断」；指针不匹配 → 「被 gate 回滚」 |
| N4 | P2（轻微，防御性缺口） | **config_drift_recheck 漂移判定隐含依赖「历史最优配置点 ∈ 邻域扫描集」**（:127 从 results 找 hist 点；:129 `hist_now is not None` 才可能判漂移）。若未来 cfg_lib 历史最优 N/EXIT 调到 NEIGHBORHOOD 之外（如 N=12 或 EXIT 改 fixed），或 hist 点本次回测失败（excess=None），drift 将**静默恒 False** 且无告警。当前三策略 hist 点均在扫描集内（G2 15∈[10,15,20]、融合/V1.4 10∈[5,10,15]，EXIT 均为 live_trail/fixed 单点匹配），今日无实害。 | config_drift_recheck.py:31-35、:126-129 | hist_result 为 None 时打 WARN（「历史最优点不在扫描集/回测失败，漂移判定不可用」），防未来配置演化后机制静默失效 |
| N5 | 观察（可接受） | feature_health_weekly --check 模式下 ALERT 仍返回 0（:175 `return 2 if (alerts and not args.check) else 0`），docstring（:17）未注明 check 分支退出码——原审查 P2-11b 的「语义含糊」仅落盘部分被处理。dry-run 为人工核查场景，可接受。 | feature_health_weekly.py:17、:175 | docstring 补一句「--check 下恒返回 0（含 ALERT）」即可 |

**附带说明**：P2-4/P2-5 的告警均为 stdout 打印，依赖调度日志（Run-Py 逐行 Log / TW 任务日志）留存可见，未接飞书——满足本批「告警=可见」的验收口径；若要与「动作失败同级 fail-loud」的体系方向完全对齐，可考虑下批统一接飞书（非必须）。

---

## ④ 总体结论

1. **本批 10 项验收对象（P2-2/3/4/5/7/8/9/10/11/12/15）全部真实落地，无一虚报**：6 项代码修复逐行核实（P2-2/3/4/5/7/11），4 项注记/登记如实且与代码现状一致（P2-8/9/10/15 注记、P2-12 待办三项均在代码中佐证存在）。顺手复核的上轮 P2-1/P2-13 亦有直接代码证据。
2. **四项验收重点全部通过**：a) P2-2 重读指针逻辑在 gate 回滚/异常/阻断/指针损坏四场景推演下行为全部正确，被拒候选不会再被复查；b) P2-7 边际 0.0005/日基于同模型同区间的 hist_now 对比，量级约为典型日超额的 23%，滤微差不遮显著漂移，判定合理；c) P2-4/5 告警在 fail-open 前提下经 stdout→调度日志可见（N2 指出 ENS 反向仍盲）；d) run_scheduled.ps1 BOM 完好（EF BB BF）+ PS5.1 解析 0 错误。
3. **新问题均为 P2 级及以下，无阻断**：N1（评估路径静默解冻，低概率高危害，建议下批优先）、N2（ENS 侧互斥读静默，约 5 行对称补齐）、N3/N4/N5 轻微。均不构成本批修复的回退理由。
4. **拍板建议**：**本批 P2 技术债标记关闭（DONE）**；N1/N2 并入 P2-12 所列「下批对齐清单」（N1 优先级建议高于 N2，因涉及冻结态可被静默清除）；N3/N4/N5 随手顺带或挂低优先级。下批对齐清单汇总：ENS 预算基数 / ENS 大盘门控 / ENS hold_dates fail-open / N1 评估路径 fail-safe / N2 ENS 侧互斥告警。

*（本报告为独立验收产物，全部结论基于真实文件内容与实测（BOM 首字节、PS Parser 解析），引用行号以 2026-09-13 晚验收时点工作区状态为准；未修改除本报告外任何文件。）*
