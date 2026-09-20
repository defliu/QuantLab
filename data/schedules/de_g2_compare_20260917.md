# G2 换仓 DE vs TRAE 独立核验对比报告 · 20260917

- 任务性质：**只读对比实验（DE 独立核验）**，全程未加 `--live`、未下单/撤单/改仓、未修改任何源文件、未 commit、未触碰 V1.3(67014907) 与融合(p16_ensemble_bridge) 任何文件。
- 解释器：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe`
- 执行命令：`python rebalance_g2.py --date 20260917`（工作目录 `projects/Project_16_LightGBM股票大师`，dry-run）
- DE dry-run 运行时间：2026-09-17 10:13 前后；桥存活：True（心跳 3s 前，build=20260910-184724，pending=0）
- 备份清单（运行前后落盘，防覆盖现场）：
  - 当日实际执行：`data/rebalance_g2/rebalance_g2_20260917.json` → `rebalance_g2_20260917.de_bak.json`（对比基准）
  - TRAE 上午 dry-run 产物：`rebalance_g2_20260917.dryrun.json/.md` → `*.dryrun.trae_bak.json/.md`（沿用 20260914 先例；DE dry-run 会覆写 `.dryrun.*` 旁路文件）

---

## 一、计划摘要（DE dry-run 输出）

| 项 | 值 |
|---|---|
| 账号 / 资金池 | 70180771 / 97,347.10 元（`g2_bridge/g2_strategy_capital.json`，account_id 戳校验通过） |
| 大盘门控 | 沪深300 当日 -0.4968% → **T=2 满仓**（买入预算=资金池×95%） |
| 候选池 | 2 只过红线60：688636.SH（total_new=60.5，prob=0.6394）、920981.BJ（total_new=60.5，prob=0.6366） |
| G2 账本持仓 | 300475.SZ（200股，成本170.1475，src=cfg）、605098.SH（1000股，成本54.8904，src=cfg） |
| 卖出 | **0 条** |
| 买入 | **0 条**（两只持仓均保留 → 空位数 n_slots = TOP_N(2) − 2 = 0） |
| 跳过 | 300475.SZ：持有未满15日(20260904起,已9日,日历OK)；605098.SH：持有未满15日(20260916起,已1日,日历OK) |
| 结果 | **[NOOP] 无买卖指令**，计划写入 `rebalance_g2_20260917.dryrun.json`（written=false） |

## 二、四条规则独立核验结论

| # | 规则 | 核验方法 | 结论 |
|---|---|---|---|
| a | 目标持仓 top2 等权 | `g2_config.TOP_N=2`；`build_plan` 中保留持仓 {300475.SZ, 605098.SH} 占满 2 个名额 → n_slots=0，无新增买入；新仓等权预算公式 `budget_each = avail×deploy_pct/n_slots`（rebalance_g2.py:455）与回测 `scan_rotate_cost_real` 口径一致 | **PASS**（今日无新仓可验，机制核验通过：两仓位满、无双买、无超额预算） |
| b | 候选评分 ≥ 红线 60 | 读 `data/selections/g2/20260917_g2_top10.csv`：仅 2 行，total_new 均 60.5 ≥ REDLINE=60.0（g2_config.py:34）；排序 688636.SH > 920981.BJ 与计划 pool 一致 | **PASS** |
| c | 卖出只来自 G2 自己账本 | sells=[] 平凡满足；账本源 = `cmd/positions_cfg_20260917.json`（account_id=70180771、strategy=Project_16_g2，仅 300475.SZ/605098.SH）+ `g2_hold_dates.json` 交叉一致；账户全量持仓（state/positions_20260917.json）另有 600409.SH、300138.SZ 两只**他策略持仓，未被纳管/未被触碰** | **PASS** |
| d | 持仓满 15 交易日才到期，不足则 SKIP | `_count_trade_days` 为 [start, end) 交易日口径；独立复算：300475.SZ 自 20260904 起 9 个交易日、605098.SH 自 20260916 起 1 个交易日，均 <15 → SKIP 属正常 | **PASS** |

补充推演（用项目 `is_trade_day` 模块复算）：300475.SZ 最早到期卖出交易日为 **2026-09-28**（9/25 中秋法定休市顺延，9/25、9/26、9/27、10/1~10/7 休市判定均已验证正确）；605098.SH 为 **2026-10-15**。

## 三、与当日实际执行对比

| 维度 | TRAE dry-run（09:52:54） | 当日实际执行（09:53:29，live） | DE dry-run（10:13） |
|---|---|---|---|
| written | false | **true** | false |
| 候选池 / 账本 / skips | 完全一致 | 完全一致 | 完全一致 |
| sells / buys / orders | 0 / 0 / [] | 0 / 0 / [] | 0 / 0 / [] |
| hs300_pct | -0.2020 | -0.1696 | -0.4968（实时行情时点差异） |
| tier / deploy_pct | 2 / 0.95 | 2 / 0.95 | 2 / 0.95 |

- 当日实际执行虽 written=true，但走的是 NOOP 分支（orders 为空 → 不写 `cmd/orders_20260917.json`，已核实桥 cmd 目录**无**当日 orders 文件），即**今日无任何委托写入桥、无下单**。
- **结论：DE 独立核验与当日实际执行完全一致（NOOP，持仓 2 只未到期、候选 2 只无空位可买），三次运行（TRAE dry-run / live / DE dry-run）在候选、账本、跳过、门控档位上零分歧。**

## 四、风险点与观察项（只记录，不擅自行动）

1. **hs300_pct 时点敏感**：三次运行值 -0.17% ~ -0.50% 浮动（腾讯实时行情快照），本次远离 -1.0% 半仓线无影响；若某日贴边，dry-run 与 live 相隔分钟级可能判不同 T 档。当前 09:52 dry-run → 09:53 live 间隔 35 秒，风险可控。
2. **候选评分贴红线**：两只候选 total_new=60.5，距红线 60 仅 0.5 分余量；数据源微扰即可能跌破 → 候选池归零。属"宁缺毋滥"设计内，非违规，但需知晓当日候选质量偏薄。
3. **dry-run 也会触发飞书通知**：NOOP 分支无条件调 `_notify_feishu`（rebalance_g2.py:520），本次推送失败（bot 未配置 not_configured），无实际影响；重复 dry-run 会产生重复通知尝试，属流程卫生问题。
4. **选股报告标题日期歧义**：`20260917_g2_selection.md` 标题写"2026-09-16"（生成日），而文件名/CSV `trade_date=20260917`（目标交易日），人工阅读易误读，建议后续统一为目标交易日。
5. **交易日历覆盖到 2026-09-16**：之后日期走「默认交易日+节假日表」fallback（本次抽验 9/25 中秋、10/1 国庆判定正确）；主库日历快照更新前，9/28 起的到期计算依赖 fallback，建议届时复核。
6. **9/28 关注点**：300475.SZ 到期将触发卖出+从候选池补仓，届时预算按 `avail = 资金池 − 保留持仓成本` 计算（T-20260903-019 口径），属下个换仓日的正常动作。

## 五、核验数据来源

- 脚本/配置：`rebalance_g2.py`（只读）、`g2_config.py`（只读）
- 计划产物：`data/rebalance_g2/rebalance_g2_20260917.dryrun.json`（DE）、`rebalance_g2_20260917.de_bak.json`（实际执行备份）、`*.dryrun.trae_bak.*`（TRAE 备份）
- 候选：`data/selections/g2/20260917_g2_top10.csv`、`20260917_g2_top2.csv`、`20260917_g2_selection.md`
- 桥文件：`D:/QMT_POOL/g2_bridge/` 下 `cmd/positions_cfg_20260917.json`、`state/positions_20260917.json`、`state/asset_20260917.json`、`state/heart_20260917.json`、`g2_strategy_capital.json`、`data/rebalance_g2/g2_hold_dates.json`
- 缺失项：无（所有目标文件均成功读取）

---

**总结论：DE 独立核验通过，与当日实际执行一致（NOOP），四条规则全部 PASS，无风险动作、无越界操作。**
