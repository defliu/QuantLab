# G2 / ENS 定时任务链路检查报告（T-20260915-001 延续）

> 委派 DE 检查 G2/ENS 换仓链路，DE 检查进程第四次卡死（16:28 启动，31 分钟 CPU 72.5s、利用率 3.9%、零产出），按 AGENTS.md 判定终止。**检查由 TRAE 接管完成**（以原始代码逐行核对 + 定时任务配置核对）。

## 检查范围

- G2：TRAE 任务 30344e79（09:50）→ `rebalance_g2.py --live` 写桥 cmd/orders_<date>.json
- ENS：TRAE 任务 051bc2d9（09:52）→ `rebalance_ens.py --live` 写桥
- 兜底：`de_rebalance_guard_daily.ps1`（DE 只读核验 + dry-run 对比，10:10/10:12/10:15，**绝不下单不补跑**）
- 参照：V1.3 已修复的 `rebalance_daily_guard.ps1`（Windows 10:05 自动补跑）+ `guard_backfill_v13.py`

## 对照 V1.3 的 6 类问题检查结果

| # | V1.3 问题 | G2 | ENS | 证据 | 需修复 |
|---|---|---|---|---|---|
| P1-1 | T=0 跳过不清仓 | ✅ 无问题 | ✅ 无问题 | rebalance_g2.py L412-427：内置 `_calc_tier` 门控，T=0 停买只卖不买 / 数据缺失 fail-safe T=1；V1.3 是 guard 外层补清仓，G2/ENS 是脚本内置语义，无"最需降仓日跳过"风险 | 否 |
| P1-2 | F3=0 系统性压分 | ✅ 无问题 | ✅ 无问题 | G2/ENS 候选为预生成 top10（deploy_predict_g2 产出），评分已含 F3，**不依赖 9:25 缓存**；load_g2_selection L236-249 宁缺毋滥 + 新鲜度=增量库最新日 | 否 |
| P1-3 | live 失败静默上锁 | ⚠️ 部分 | ⚠️ 部分 | rebalance 写桥失败返回非 0（ABORT/异常）；但 **de_rebalance_guard_daily.ps1 只核验不下单、不看 rebalance exit**；TRAE 任务靠 agent 判断 | 是（低危） |
| P2-4 | 告警通道死 | ✅ 已覆盖 | ✅ 已覆盖 | qmt_card.py `_write_feishu_fallback` 修复全局生效（G2/ENS 同用 qmt_card/push_alert_card） | 否 |
| P2-5 | 缺自动补跑兜底 | ⚠️ **核心差异** | ⚠️ **核心差异** | G2/ENS 换仓仅 TRAE 任务 + DE 只读核验（**不下单不补跑**）；TRAE 会话中断时**无 Windows 兜底能补跑**（V1.3 已有 rebalance_daily_guard.ps1 自动补跑） | **是（核心）** |
| P3-6 | 历史日改签 look-ahead | ✅ 无问题 | ✅ 无问题 | 候选按「数据日」命名 + 新鲜度校验，无 --date 改签逻辑 | 否 |

## 结论

**G2/ENS 无 P1-1/P1-2/P2-4/P3-6 四类问题**（架构优于 V1.3：门控内置、候选预生成、无历史日改签）。
**存在 2 项差异**：
1. **P2-5（核心）**：G2/ENS 换仓无 Windows 自动补跑兜底——TRAE 会话中断（今天 V1.3 同类事故）时无法补跑，只能 DE 核验告警。**可行性评估：G2/ENS 补跑比 V1.3 更易脚本化**（候选已预生成、无实时 F2/F3/F5 依赖，直接 `rebalance_g2.py --date <d> --live` 即可）。
2. **P1-3（低危）**：de_rebalance_guard_daily.ps1 不检查 rebalance exit code（但它只核验不下单，风险有限）。

## 修复方案（待诚哥拍板后实施）

**方案 A（推荐）**：新建 `rebalance_g2_guard.ps1` / `rebalance_ens_guard.ps1`（或合并通用脚本），Windows schtasks 10:08/10:10 兜底：
- 幂等：当日 `rebalance_g2_<date>.json`（written=true）或 `orders_<date>.json` 已存在 → 跳过（对齐 V1.3 guard 三标记）
- 未换仓 → 跑 `rebalance_g2.py --date <d>` dry-run 校验（桥存活/候选新鲜/门控）→ 通过后 `--live` 写桥
- exit code 检查：非 0 → 飞书告警 + 不写锁（人工可重跑）；成功写锁
- 触发时点：TRAE 任务 ~10 分钟跑完，10:08/10:10 可区分已执行/未执行（对齐 V1.3 10:05 设计）

**方案 B（保守）**：暂不新建补跑兜底，仅给 de_rebalance_guard_daily.ps1 加"检测到未换仓时飞书告警"，补跑仍靠人工。

**低危修复（无论 A/B 都应做）**：de_rebalance_guard_daily.ps1 对 deveco exit 已检查，补充"当日 orders 文件缺失时告警"。

## 建议

- G2/ENS 候选管道（16:30 deploy_predict_g2）若有 Windows 任务且健康，P2-5 补跑兜底可立即落地；若候选管道也靠 TRAE（16:45），需先确认候选就绪（同 V1.3 R1b 根因）。
- 与 V1.3 一致原则：新建兜底必须带 UTF-8 BOM、幂等锁、exit code 检查、飞书告警。
