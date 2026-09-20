# T-20260915-001 只读复核报告：V1.3 guard 自动补跑升级

> 委派 DE（GLM-5.3）只读复核，2026-09-15 14:2x 执行。DE 报告通过 stdout 返回（未落盘，TRAE 代为落盘），TRAE 独立核验见文末附录。

## ✅ 核实通过项

| 声明 | 复核结果 |
|---|---|
| `guard_backfill_v13.py` 链路逻辑 | 面板→候选→`write_review_json`→`review_full`→清单，exit code / JSON 契约与 ps1 解析（`Select-Object -Last 1` + `ConvertFrom-Json` 失败兜底）均正确；py_compile 通过 |
| BOM / 语法 | 三个 guard ps1 均 EF BB BF 在位；PSParser 0 错误 |
| 幂等 | 三标记（executed_live / 成交记录 / 锁）+ 锁写入时机正确；13:44 CheckOnly 日志实证锁生效 |
| 补跑产物 | `20260915_selection_full.csv` 存在，top2=000949/603225 均 54.0<58 → 不换仓，与看板一致 |
| 调度 | `Quant_V13_Rebalance_Guard` 指向升级后脚本，下次 09-16 10:05，Last Result 0 |
| T 档阈值 | 与 market_tier 口径一致（≤-1.5→0 / <-1.0→1）；DE guard 为 dry-run 只读，无双下单冲突 |

## ⚠️ 审计发现

**P1-1｜T=0 空仓日 guard 跳过 = 风控缺口（rebalance_daily_guard.ps1:119）**
`rebalance_daily.py:299` 中 `--top 0` 是**清仓信号**（T-20260904-003 全部卖出），但 guard 在 tier=0 时直接写锁退出、不跑 rebalance。若大盘暴跌日（hs300≤-1.5%）恰逢 TRAE 09:45 会话中断——正是最需要降仓的日子——guard 检测到未执行却**不清仓**。注释「--top 0 由业务处理」与兜底使命自相矛盾。属遗留设计，但本次升级未解决。

**P1-2｜F3=0 系统性压分 → 自动补跑几乎必然「不换仓」**
今日补跑清单 10 只候选 F3 全为 0.0（9:25 缓存只写 G2 候选，Tushare 业绩公告兜底也未覆盖）。F3 权重 20%，缺失即损失最高 ~14 分；top2 = 54 分，若 F3 正常（~6 分）约 66 分**会过红线**。即：补跑链路恢复了管线，但数据质量恢复不了——guard 自动补跑产出的清单大概率永远过不了 58 红线。方向是 fail-safe（宁缺毋滥），但升级的实际效果≈「guard 仍不交易」，与「可自动补跑」的预期有实质差距。

**P1-3｜rebalance --live 失败被静默上锁（ps1:123-127）**
guard 不检查 `$LASTEXITCODE` 就写锁并打印「完成」。若 `rebalance_daily.py --live` 中途崩溃（如 xtquant 连接失败），结果=不重试+不告警+当日锁死。

**P2-4｜告警通道当前是死的**
今晨 10:05 实证 `push_alert_card` 失败（lark-cli not_configured），而补跑失败路径的唯一上报手段就是它——补跑失败=完全无声。

**P2-5｜新分支从未经 guard 真实跑过**
10:05 跑的是旧版脚本（日志为旧告警文案）；13:44 CheckOnly 撞锁退出；且 CheckOnly 模式在缺清单时**先退出、不执行补跑**（ps1:104-107），即没有任何测试模式覆盖「backfill→tier→rebalance --live」完整新分支。看板「补跑链路实测跑通」实为 guard_backfill_v13.py 手工单跑。明日 10:05 是该分支首次生产执行。

**P3-6｜无新鲜度校验**：`latest_panel_date()` 不校验面板是否为上一交易日；面板更新失败时会静默用陈旧候选。另：guard 对 backfill/deploy_predict 无超时（挂起=无声僵死）；`--date 历史日` 会把**今天的实时价改签为历史日期**（`guard_backfill_v13.py:77-83`），是 look-ahead 脚枪。

**上游疑点坐实**：`20260914_model_top10.csv` 时间戳 09-15 13:28（手工），同侪文件均为当日 16:4x 管道产出 → 09-14 的 16:30 管道确实未产出。明晨 guard 若补跑将消费**今晚 16:30 管道产出的 20260915 候选**，建议今晚核对。

## 建议（按优先级）
1. tier=0 改为执行 `rebalance_daily --top 0 --live`（清仓是兜底的核心场景）
2. guard 检查 rebalance exit code，非 0 时告警且**不写锁**
3. 9:25 缓存扩容覆盖 V1.3 top10（或 guard 补跑时对候选现场采催化分），否则明文档化「补跑清单 F3 恒 0、仅作管线保活」
4. 明晚前修复 lark-cli，或补跑失败增加第二告警通道

---

## 附录：TRAE 独立核验（以原始代码/数据为准）

（2026-09-15 14:30 TRAE 补充——DE 结论逐条对照原始文件核验，均属实）

| DE 发现 | TRAE 独立核验 | 处置 |
|---|---|---|
| P1-1 T=0 空仓日 guard 跳过 | ✅ 属实：`rebalance_daily_guard.ps1:119` `if ($tier -eq 0) { ... exit 0 }`，不清仓。属**遗留设计**（注释「--top 0 由业务处理」）非本次升级引入；清仓为重大不可逆动作，guard 自动清仓需**诚哥拍板** | 待拍板 |
| P1-2 F3=0 系统性压分 | ✅ 属实：补跑清单 `20260915_selection_full.csv` 10 只 F3 全 0.0；根因=9:25 任务 F3 缓存 `review_20260915.json` 只写 G2 候选（605098/688029/688528），未覆盖 V1.3 top10；Tushare 业绩公告兜底未覆盖。F3 权重 20%，总分被压至 54<58 → 安全阀不换仓（fail-safe 正确，但补跑实际效果≈保活不交易） | 上游问题，已登记 T-20260915-001 遗留；建议 9:25 任务缓存扩容覆盖 V1.3 top10 |
| P1-3 rebalance --live 失败静默上锁 | ✅ 属实：原 L124-127 `& $py rebalance_daily.py ... 2>&1` 后不看 `$LASTEXITCODE` 直接写锁 → live 崩溃=不重试+不告警+当日锁死 | **✅ 已修复（2026-09-15 14:27）**：`$rc=$LASTEXITCODE`，非 0 时飞书告警+不写锁（人工可重跑）；BOM/语法/幂等复验通过 |
| P2-4 告警通道死（lark-cli not_configured） | ✅ 属实：今晨 guard 日志实证 `push_alert_card` 失败（lark-cli not_configured），9:45 monitor 同病。补跑失败=完全无声 | 独立问题，另行修复（lark-cli 配置/通道） |
| P2-5 新分支未真实跑过 | ✅ 属实：10:05 跑的是旧版（日志旧文案）；13:44 CheckOnly 撞锁退出；CheckOnly 缺清单时先退出不执行补跑 → 无测试模式覆盖完整新分支。明日 10:05 首次生产执行 | 已备注：明日 10:05 核对 |
| P3-6 无新鲜度校验 + 历史日改签 | ✅ 属实：`latest_panel_date()` 不校验面板为上一交易日；`--date 历史日` 会把今天实时价改签为历史日期（`guard_backfill_v13.py:77-83`）。但 guard 每次 `--date`=当天，正常路径不触发 | 低风险，记录不阻塞 |

**核验方法**：逐行读取 `rebalance_daily_guard.ps1`（L108-131）、`guard_backfill_v13.py`（write_live_review L70-83）、`20260915_selection_full.csv`（F3 列）、`review_20260915.json`（catalysts 键）、guard 今晨日志；对照 DE 报告每条的引用行号与数据，全部命中。

**TRAE 结论**：DE 复核质量合格（引用行号/数据与实际一致），P1-3 已闭环，P1-1 待拍板，P1-2/P2-4 为上游/独立问题已登记。
