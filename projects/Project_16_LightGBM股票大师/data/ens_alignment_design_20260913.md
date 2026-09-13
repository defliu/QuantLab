# P0-4 · ENS 对齐 G2 三项差异 · 修复设计文档（只设计，未改代码）

- 日期：2026-09-13（执行 DE 意见书 `data/de_opinion_auto_iteration_20260913.md` ⑥ P0-4；来源 PROJECT_MEMORY L1038 P2-12「列入下批对齐清单」）
- **性质声明：本文件是设计文档。`rebalance_ens.py` 本次未做任何修改**——ENS 已实盘建仓（600409/300138），涉及换仓预算/卖出行为的改动属实盘口径变更，按红线须单独批次实施 + dry-run 验证 + 人工拍板后部署。
- 对照基准：`rebalance_g2.py`（G2 桥换仓，已含三项对应修复）。

---

## 差异一：买入预算基数未扣保留持仓投入（敞口超资金池）

### 现状证据
- **ENS**（`rebalance_ens.py:384`）：
  ```python
  budget_each = capital * (1 - G.RESERVE_CASH_PCT) / float(n_slots)
  ```
  基数 = 资金池总额（`load_ens_capital()`，当前滚动值 98,469.21），**未扣保留持仓（kept）已投入成本**。
- **G2 已修**（`rebalance_g2.py:443-452`，T-20260903-019 口径修复 / DE 体检 P1-4）：
  ```python
  kept_invest = Σ(kept 持仓 cost × vol)
  avail = max(capital - kept_invest, 0.0)
  budget_each = avail * deploy_pct / float(n_slots)
  ```
- **爆仓场景**（TOP_N=2，HOLD_DAYS=10 部分到期常见）：1 只保留 + 1 个空位时，新仓预算 = 98469×0.95 ≈ 93,546 元，而保留持仓已占约 45,000 元成本 → 总敞口 ≈ 138,000 > 资金池 98,469，**超额约 40%**。浮亏时资金池缩水，超额比例进一步放大（当前 ENS 持仓浮亏 -1,531，资金池已缩水，若明日恰好单票到期补仓即触发）。
- 注意：桥内下单前有可用资金校验兜底（QMT 端），超额会变成「废单/部分成交」而非真实爆仓——但那是**事故依赖兜底**，不是设计正确。

### 目标
与 G2 同口径：`budget_each = (capital − kept_invest) × (1 − RESERVE_CASH_PCT) / n_slots`，任何场景总敞口 ≤ 资金池 × 95%。

### 改法（3 行级改动）
`rebalance_ens.py` L384 前插入 kept_invest 计算（照抄 rebalance_g2.py:446-451），L384 改为 `avail * (1 - G.RESERVE_CASH_PCT) / n_slots`；`plan` 增记 `kept_invest`/`avail_cash` 两字段（对齐 G2 的 plan 记录，便于对账核对）。

### 风险
- **行为变化方向只会更保守**（预算变小），不会漏买优质票以外的新增风险；但若极端场景 avail 过小（保留持仓浮盈推高 kept_invest 逼近 capital），新仓预算趋零 → 属正确行为（没钱就少买），需接受「空位补不满」的日志。
- 与 `gen_positions_cfg_ens.py`（positions_cfg 成本侧）无联动——该文件只读桥回报，不受预算口径影响。

### dry-run 验证方案
1. `python rebalance_ens.py --date <最近交易日> --dry-run`（现有 dry-run 入口），构造保留持仓场景核对 plan JSON 的 `kept_invest`/`avail_cash`/buys.volume 与手算一致；
2. 对拍 G2：同日跑 `rebalance_g2.py --dry-run`，两桥同参数（临时 --capital 等额）下 budget 公式逐字段对照；
3. 历史重放：用 09-11 建仓日账本（2 票满仓）+ 模拟「1 票到期」分支，断言总敞口 ≤ capital×0.95。

### 可否独立验收
**可以**。单项改动、行为单向保守、dry-run 可完全覆盖，验收不依赖差异二/三。**建议三项中先做本项**（唯一正在产生实际敞口偏差的）。

---

## 差异二：无大盘门控（极端行情日裸奔建仓）

### 现状证据
- **ENS**（`rebalance_ens.py:351-394` 买入段）：无任何 hs300/tier 逻辑，只要 `n_slots > 0 and pool` 即按预算买入。
- **G2 已有**（`rebalance_g2.py:409-424`，T-20260904-004 / 2026-09-11 补上）：沪深300 当日涨跌幅三档门控——T=0（≤-1.5%）停买 / T=1（<-1.0%）半仓 / T=2 满仓；数据缺失 fail-safe 按 T=1 半仓（`rebalance_g2.py:412-414`）。`load_hs300_pct()`（L168-192，腾讯行情，5 秒超时）与 `_calc_tier()`（L195-205）均为**模块级可复用函数**。
- **ENS 参数缺失**：`g2_ens_config.py` 无 TIER_STOP_PCT/TIER_HALF_PCT/DEPLOY_PCT/HALF_DEPLOY_PCT（G2 的在 `g2_config.py:95-98`）。
- 暴露场景：大盘急跌日（如 hs300 ≤ -1.5%），G2 停买、V1.3 有对应门控，**ENS 照常满额建仓**——同账户三策略中唯一无刹车的。

### 目标
对齐 G2 口径：T=0 停买（只卖不买）/ T=1 半仓 / T=2 满仓；数据缺失 fail-safe 半仓；阈值与环境变量注入（`ENS_TIER_STOP`/`ENS_TIER_HALF`）与 G2 同构。

### 改法
1. `g2_ens_config.py` 增 4 个常量（值照抄 g2_config.py:95-98，环境变量名改 ENS_ 前缀）；
2. `rebalance_ens.py` 从 `rebalance_g2` import `load_hs300_pct`/`_calc_tier`（**复制逻辑或 import 复用二选一，倾向 import 复用**——rebalance_g2 与 rebalance_ens 同目录、同进程空间，无循环依赖风险；若担心 G2 模块 import 副作用，可把两函数上移 g2_ens_config.py 或新开共享小模块，实施时定）；
3. 买入段插入 tier 计算（插在降级闸之后、预算计算之前），`budget_each` 基数乘 `deploy_pct`；plan 增记 `hs300_pct`/`tier`/`deploy_pct`。

### 风险
- **阈值是否适配 ENS**：门控阈值（-1.5%/-1.0%）源自 V1.3 体检口径，ENS 网格寻优（+0.211% 候选）**未含门控**——加门控后 ENS 行为偏离其纸面验证口径。这属于「防御性偏离」，方向保守，但须在实施 PR 里声明：**纸面 ens 臂自此与实盘口径分叉（实盘多了门控），季评解读时注意**（P1-1 季评报告需带此注记）。
- ENS 换仓在 10:05 时段执行，hs300 当日涨跌幅为盘中实时值——与 G2 同口径（G2 同样盘中取），无新增风险。
- import 复用方案下，`rebalance_g2.py` 未来改动（如换数据源）会同时影响两桥——属期望行为（口径同源），非风险。

### dry-run 验证方案
1. `G2_HS300_PCT=-2.0`（注入环境变量，load_hs300_pct:173-175 支持）跑 `rebalance_ens.py --dry-run` → 断言 buys 为空、plan.tier=0；
2. 注入 `-1.2` → 断言 tier=1 且 budget = avail×0.50/n_slots；
3. 注入非法值/断网 → 断言 fail-safe tier=1；
4. 不注入（真实行情，非交易时段数据为昨收基准）→ 仅核对打印行格式与 G2 一致。

### 可否独立验收
**可以**。纯增量逻辑（原满仓行为=T=2 等价），环境变量注入可穷举三分支+缺失分支。与差异一的合并实施顺序：先一后二（预算基数是更基础的口径错误），但同批实施也安全（两者只在 budget_each 一行交汇：`avail × deploy_pct`，rebalance_g2.py:452 已示范合并形态）。

---

## 差异三：hold_dates 缺失 fail-open 即卖（误清仓风险）

### 现状证据
- **ENS**（`rebalance_ens.py:328-335`）：
  ```python
  since = hold_dates.get(code)
  if since:                      # ← 缺失时不进 continue，直接落到下方卖出逻辑
      held_days, cal_ok = ...
      if held_days < G.HOLD_DAYS: continue
  # since 为 None → 视同到期 → SELL
  ```
  即：账本有票但 `g2_ens_hold_dates.json` 无该票建仓日 → **立即卖出**（fail-open）。
- **G2 已修**（`rebalance_g2.py:367-373`，T-20260904-006①）：
  ```python
  if not since:
      plan["skips"].append({... "建仓日缺失，fail-safe 跳过卖出（防误清仓）"})
      print("    !! %s hold_date 缺失，跳过卖出（fail-safe，需人工核查账本）" % code)
      continue
  ```
- 触发场景：hold_dates 文件损坏/半写（写一半断电）、人工补账本漏记建仓日、`_update_hold_dates` 的陈旧条目清理误删（P2-5 已修但属同文件敏感区）。一旦触发，**正常持有的票被当到期清仓**，且随后可能被当空位回补——一卖一买白交两轮成本，更坏情况是卖在低点。
- 历史案例背书：G2 该修复正是从事故教训来的（T-20260904-006），ENS 是同源代码 fork 时未同步。

### 目标
对齐 G2：hold_date 缺失 → skip 卖出 + 告警日志 + 人工核查提示（fail-safe），**宁可少动也不误卖**。桥内风控（止损/止盈/回撤）不依赖 hold_dates，fail-safe 跳过不影响风控生效。

### 改法（1 处分支改写）
`rebalance_ens.py:329-330` 改为 G2 形态：
```python
since = hold_dates.get(code)
if not since:
    plan["skips"].append({"code": code, "vol": ld["vol"],
                          "reason": "建仓日缺失，fail-safe 跳过卖出（防误清仓）"})
    print("    !! %s hold_date 缺失，跳过卖出（fail-safe，需人工核查账本）" % code)
    continue
```

### 风险
- **残留风险**：跳过后该票永不到期（每次都 skip），若无人工核查会变长期僵尸持仓。缓解：skip reason 已带「需人工核查账本」，且 `reconcile_ens.py` 日终对账会暴露「账本 vs 账户」不一致；实施时建议同批在 reconcile_ens 的 md 报告加一条「hold_dates 缺失票」巡检行（可选增强，非验收必需）。
- 与到期卖出的语义边界：本改动只动「since 缺失」分支，「日历缺失（cal_ok=False）」分支（L331-335）维持原样（G2 同）。

### dry-run 验证方案
1. 沙箱构造账本 2 票 + hold_dates 只记 1 票 → `--dry-run` 断言：缺失票进 skips（reason 含 fail-safe）、orders 无其 SELL、正常票照常；
2. 反向用例：hold_dates 全缺失 → 断言零卖出 + 2 条 skip 告警；
3. 真实账本回归：当前实际 hold_dates（600409/300138 均有记录）→ dry-run 输出与改动前 diff 为空（证明无意外行为变化）。

### 可否独立验收
**可以**。分支级改动、dry-run 沙箱可构造全覆盖、真实账本回归可证明零意外变化。**风险最低、建议与差异一同批实施**。

---

## 实施批次建议（汇总）

| 批次 | 内容 | 理由 |
|---|---|---|
| **批次 1（下批，优先）** | 差异一（预算基数）+ 差异三（fail-safe） | 差异一是唯一正在产生实际偏差的口径错误；差异三改动最小风险最低；两者互不干扰可同批验收 |
| **批次 2（批次 1 验收后）** | 差异二（大盘门控） | 需新增 config 常量 + import 复用决策 + 纸面口径分叉声明，独立验收 |
| 部署纪律 | 每批次：改码 → dry-run 三件套 → 真实账本回归 → 人工拍板 → 部署 → 次日 reconcile 日志核对 | ENS 属实盘执行红线范畴（AGENTS「涉及真实下单、卖出重试…的改动必须格外保守」） |

## 红线对照

- 本文档零代码改动，未触碰 rebalance_ens.py / rebalance_g2.py / 任何配置（遵守任务白名单）；
- 实施批次明确要求 dry-run + 人工拍板 + 部署后核对，符合「先纸面/演练后实盘」与 QMT 实盘保守红线；
- 差异二的纸面口径分叉显式登记（防季评误读），符合「按策略隔离评估」红线。

---

*引用行号以 2026-09-13 晚工作区状态为准。资金池数值 98,469.21 来自 `reconcile_ens.py --date 20260913` 实跑（PROJECT_MEMORY 09-13 ENS 段）。*
