# G2 换仓 20260918 · DE 独立核验报告（DE vs TRAE 对比实验，只读）

- 核验人：DE（DevEco Code CLI），任务指令：`data/schedules/de_g2_prompt_20260918.txt`
- 性质：**只读对比实验**。未加 `--live`、未写桥、未下单/撤单/改仓、未改任何源文件、未 commit、未触碰 V1.3(67014907) 与融合(p16_ensemble_bridge) 文件。
- 运行命令：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe rebalance_g2.py --date 20260918`（dry-run，工作目录 `projects/Project_16_LightGBM股票大师/`）
- 现场备份（运行前已做）：
  - 当日实际执行快照 `rebalance_g2_20260918.json` → `rebalance_g2_20260918.de_bak.json`（对比基准）
  - TRAE 早间 dry-run（09:53:00）→ `rebalance_g2_20260918.dryrun.trae_bak.json/.md`（DE 重跑会覆盖 .dryrun 文件，按 0914/0917 惯例先保现场）

## 一、计划摘要（DE dry-run 输出）

- 账号 70180771（G2 大QMT 桥，资金池 97,539.60 元 = 10 万滚动 realized -1,637 / float -823）
- 桥心跳正常（build=20260910-184724，pending=0）
- 大盘门控：沪深300 当日 +0.6572% → **T=2 满仓**（> -1.0% 半仓线）
- 候选池（红线 60）：688010.SH（total 60.5）、600303.SH（total 60.0），共 2 只
- G2 账本：300475.SZ 200股（成本 170.1475，20260904 建仓，已 10 交易日）、605098.SH 1000股（成本 54.8899，20260916 建仓，已 2 交易日）
- **结果：[NOOP] 无买卖指令**——两只持仓均未满 15 交易日 → SKIP；账本已满 TOP_N=2 → 无买入空位

## 二、四条规则独立核验结论

| 规则 | 结论 | 独立依据 |
|---|---|---|
| a) 目标持仓 top2 等权 | **PASS** | `g2_config.TOP_N=2`；账本恰持 2 只 → 空位=0，不触发买入；等权预算公式 `avail×deploy_pct/n_slots`（rebalance_g2.py:455）在代码层对齐回测口径。今日无买入，等权分配未被触发，结构正确 |
| b) 候选评分 ≥ 红线 60 | **PASS** | 直接核对原始 CSV `20260918_g2_top10.csv`：688010.SH total_new=60.5、600303.SH total_new=60.0，均 ≥60（代码 `<60` 才过滤，边界 60.0 含）。注意 600303.SH 恰好踩线 |
| c) 卖出只来自 G2 自己账本 | **PASS** | 今日 sells=[] 平凡满足。账本来源仅 `cmd/positions_cfg_20260918.json`（account_id=70180771、strategy=Project_16_g2，与账本完全一致）+ fills FIFO；账户全量持仓（state/positions_20260918.json）共 4 只，他人策略持仓 600409.SH(7400股)/300138.SZ(4100股) **未被纳管**，隔离正确 |
| d) 持仓满 15 交易日才到期，不足 SKIP 属正常 | **PASS** | 独立按日历数交易日（9/4起，排除周末，9/18 前不含当日）：9/4,7,8,9,10,11,14,15,16,17 = **10 日 < 15** → SKIP 正确；605098.SH 自 9/16 起 = **2 日 < 15** → SKIP 正确。两只 SKIP 与脚本输出完全一致 |

补充一致性核对：资金池 97,539.6 与 `g2_strategy_capital.json` 一致；账本投入 88,928.50 元（34,029.50+54,899.00），隐含可用现金 8,611.10 元（今日未动用）；`g2_hold_dates.json` 的 account_id 戳（70180771）匹配；桥 cmd 下无 `orders_20260918`（NOOP 当日不写单，符合预期）。

## 三、与当日实际执行对比

三方对比（live 09:53:44 实际执行 vs TRAE dry-run 09:53:00 vs DE dry-run ~10:20），对全部 22 个字段做扁平化 diff：

- **唯一差异仅 2 项**：`hs300_pct`（live 0.4798 / trae 0.5105 / de 0.6572，盘中自然漂移，三者均在 T=2 区间 > -1.0%，**对决策零影响**）与 `written`（live=true / dry-run=false，设计内旁路标记）。
- pool、ledger、hold_dates、sells、buys、skips、orders、tier、deploy_pct、capital **逐字段一致**。

**结论：DE 的独立核验结论与当日实际执行完全一致——今日 NOOP（持仓未满 15 日、无买入空位）是正确且唯一的合规输出，当日执行无任何违规或漏单。**

## 四、风险点与提示（只记录，不擅动）

1. **300475.SZ 到期预告**：按 15 交易日推算，9/24 计满 14 日仍 SKIP，**9/28（中秋 9/25-27 后首个交易日）计满 15 日将触发到期卖出**。届时依赖 `is_trade_day` 的节假日 fallback 日历，建议当日执行前复核其计数。
2. **科创板最小申报量潜在隐患（未触发）**：候选 688010.SH 为科创板（单笔最小 200 股），而 G2 `MIN_ORDER_VOL=100`（整手 100）。若未来买入该票且预算不足 200 股，可能生成被拒订单。今日无买入未触发，仅登记为潜在隐患，需策略 owner 裁决是否加板块差异化整手规则。
3. **候选池偏薄 + 踩线**：≥60 红线仅 2 只，恰等于 TOP_N；600303.SH 恰在 60.0 边界。若持仓腾出空位，可买池冗余为零，且需过 ENS 重叠互斥与低开拦截两道闸。
4. **ENS 互斥未演练**：今日无买入，G2↔ENS 重叠互斥逻辑未被触发；按任务硬约束 DE 未读取任何融合（ens）文件，该项不在本次核验范围。
5. **DE dry-run 飞书通知失败**（`not_configured`）：对本只读任务无副作用（无任何订单动作）；live 执行的通知送达情况未核验，不在只读范围。
6. `hs300_pct` 为盘中实时值，三次读取漂移 0.48→0.66%，正常；但若某日读数落在 ±0.5pp 的档位边界附近，不同时刻跑出的 T 档可能不同——建议执行时点固定（当前 09:45 后约定已满足）。

## 五、文件清单（本次读取/产出）

- 读取：`g2_config.py`、`rebalance_g2.py`、`data/selections/g2/20260918_g2_top10.csv`、`20260918_g2_selection.md`、`data/rebalance_g2/{g2_hold_dates.json, rebalance_g2_20260918.json/.md, .dryrun.json/.md}`、`D:/QMT_POOL/g2_bridge/{g2_strategy_capital.json, cmd/positions_cfg_20260918.json, state/{positions,heart,asset}_20260918.json, state/fills_20260916.json}`
- 产出（本次任务仅新增以下文件，无源文件改动）：`rebalance_g2_20260918.de_bak.json`、`rebalance_g2_20260918.dryrun.trae_bak.json/.md`、DE dry-run 覆盖重写的 `rebalance_g2_20260918.dryrun.json/.md`、本报告。

**缺失项标注**：无关键缺失。唯一未核验项为 ENS 账本互斥（受硬约束禁触，且今日逻辑未触发）与 live 飞书通知送达（超出只读范围）。
