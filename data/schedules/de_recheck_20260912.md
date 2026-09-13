# 只读复查报告：2026-09-12 看板大扫除 + 三策略健康（G2/V13/ENS）

- 任务书：`D:\QuantLab\data\schedules\de_recheck_20260912_prompt.txt`
- 执行时间：2026-09-12（只读审计，未修改任何被审文件、无 git 操作）
- 证据原则：全部结论附原始文件路径+内容；未取得的证据标 MISSING

---

## 1. 结论摘要表

| # | 项目 | 判定 | 一句话 |
|---|---|---|---|
| A1-1 | G2 成本表 positions_cfg_20260911.json | **PASS** | account_id=70180771、仅 300475@170.1475×200、无 001266，与看板一致 |
| A1-2 | V13 成本表 positions_cfg_v13_20260911.json | **PASS** | account_id=67014907、601058@14.331×3100 + 601579@23.9662×1900，与看板一致 |
| A1-3 | 两 peak 文件非零 | **PASS** | G2 peak=300475:170.52；V13 peak=601058:14.4838/601579:24.51（09-12 08:55 仍在刷新） |
| A1-4 | 大QMT 0911 日志（G2/ENS） | **PASS** | [P16G2][INIT] 成本表 2 条（00:55/08:57）、09:46 SELL_STOP 001266 成交、ENS 09:54 两笔买入 FILLED 全部在日志 |
| A1-5 | 国金QMT 0911 日志（V13） | **PASS** | [P16V13][INIT] 外部成本表 2 条（08:57 回退读、14:57/20:56 直读当日表） |
| A1-6 | schtasks 12:05:29/0 | **数字属实，结论 FAIL** | schtasks 确显示 Last Run 12:05:29/0，但生成器日志实锤 **09:00:02 已准点运行且三生成器 exit=0**，12:05:29 是第二次运行——看板「迟 3h」结论错误，需更正 |
| A2-1 | T-20260904-004 TIER_RULES | **PASS** | g2_config.py L86-95 含 TIER_STOP_PCT=-1.5/TIER_HALF_PCT=-1.0 + tier 语义注释 |
| A2-2 | T-20260904-005 预算闭环 | **PASS** | rebalance_g2.py L421-427 `avail = max(capital - kept_invest, 0.0)` |
| A2-3 | T-20260907-004 BUILD 184724 | **PASS** | 0911/0912 两客户端日志多次出现 `BUILD_TAG=20260910-184724`（V13 与 G2 均在跑） |
| A2-4 | T-20260902-001 超买残留 | **PASS** | positions_20260910/20260911.json 均无 600262/300964 |
| A3 | 15 条陈旧裁决 | **全部不影响三策略** | 逐条核验无一条需回提；2 条共享账号项（T-20260814-003/T-20260824-001）已用当前持仓快照验证无残留/无幻影 |
| B-1 | 三桥心跳 | **PASS** | 09-12 08:55:42 / 08:55:32 / 08:59:04 三桥全部刷新，build_tag 三方一致，pending 全 0 |
| B-2 | 0912 两客户端日志 | **PASS** | 08:52 三策略全部 init（走 CFG-FALLBACK 回退 0911 表，周六非交易日属正常兜底） |
| B-3 | 持仓/资产/成交 | **PASS** | G2=300475；ENS=600409+300138（T-20260910-109 首日建仓实证）；账户总资产 9,991,318.06 |
| B-4 | 数据新鲜度 | **一致（缺口属实）** | enh 面板最新日期 **2026-08-14**（冻结）；基础 v3 面板最新 **2026-09-11**——T-20260912-001 描述准确 |
| B-5 | 资金池 | **PASS（附观察）** | G2 97,863.5（已滚动）/ V13 88,001.78（已滚动）/ ENS 100,000（**无滚动机制**，观察项） |
| C-1 | T-20260912-001 enh 无 writer | **一致** | 全库 7 处引用均为读取；refresh_panel_v3.py 只写 merged_daily_full + feature_panel_v3（27 特征） |
| C-2 | T-20260910-103/104 纸面管道 | **一致** | 5 个脚本/ps1 全部存在，09-11 两管道均 exit 0 |
| C-3 | T-20260910-105 每日刷新覆盖 | **一致（已覆盖）** | quant_daily_update（MON-FRI 16:30）每日串跑增量→merge→refresh_panel_v3 |
| C-4 | T-20260910-107 monitor 并行观察 | **一致** | 6 个 Quant_Monitor_* 任务 Active；184724 已在日志确认 |
| C-5 | T-20260904-006 五项 | **部分不一致** | ①**已修**（fail-safe 跳过+告警）但 TODO 文本仍列为未修——需更正；③无冷却、⑤卖出无跌停/停牌过滤：代码现状与 TODO 一致（未修） |
| C-6 | T-20260827-003 gpsj ST 反转 | **一致（未修）** | gpsj_reader.py L58 `"ST": "is_st"` 仍为直接映射，未反转 |

---

## 2. Part A 核验明细

### A1. T-20260910-101 的 09-11 开盘三核对

**① 两表落盘内容 —— PASS**

`D:\QMT_POOL\g2_bridge\cmd\positions_cfg_20260911.json`（mtime 2026-09-11 15:49:00）：
```json
{"account_id": "70180771", "strategy": "Project_16_g2", "date": "20260911",
 "generated_at": "2026-09-11 15:49:00",
 "positions": [{"code": "300475.SZ", "cost": 170.1475, "vol": 200}]}
```
✅ account_id=70180771、仅 300475（200 股@170.1475）、不含 001266（已于 09:46 止损卖出，15:49 再生成时自动剔除）。

`D:\QMT_POOL\p16_v13_risk\cmd\positions_cfg_v13_20260911.json`（mtime 2026-09-11 16:14:58）：
```json
{"account_id": "67014907", "date": "20260911", "updated_at": "2026-09-11 16:14:58",
 "positions": [{"code": "601058.SH", "vol": 3100, "cost": 14.331},
               {"code": "601579.SH", "vol": 1900, "cost": 23.9662}]}
```
✅ 与看板完全一致（601058 为除权调整后成本 14.331）。

**② 盘中 peak 非零 —— PASS**

- `D:\QMT_POOL\g2_bridge\state\peak.json`：`"peaks": {"300475.SZ": 170.52}`，updated_at 2026-09-12 08:55:42
- `D:\QMT_POOL\p16_v13_risk\state\peak_v13.json`：`"peaks": {"601058.SH": 14.4838, "601579.SH": 24.51}`，updated_at 2026-09-12 08:55:32

✅ 与看板逐位一致，且 09-12 早晨仍刷新（两桥存活）。

**③ 两客户端 0911 公式日志 —— PASS**

`D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20260911.log`（12893B）：
- 00:55:13 `[P16G2][INIT] 外部成本表 2 条` + `BUILD_TAG=20260910-184724`，positions=2
- 08:57:09 `[P16G2][CFG-FALLBACK] 当日成本表缺失，回退最近表 date=20260910` + `[P16G2][INIT] 外部成本表 2 条`
- 09:46:30 `[P16G2][RISK] SELL_STOP P16_20260911_RSK0001 001266.SZ 卖1300股 ret=0 | 现价31.68 跌破止损位31.69`，09:46:33 `[P16G2][FILLED] ... deal=1300`
- 09:54:50 `[P16ENS][ORDER] BUY 600409.SH 7400股 @6.370`、`BUY 300138.SZ 4100股 @11.440`；09:55:00 `[P16ENS][FILLED] ... 600409.SH deal=7400`；09:55:45 `[P16ENS][FILLED] ... 300138.SZ deal=4100`
- 13:00:00 `[P16ENS][CFG-RELOAD] 300138.SZ 孤儿校准 cost=11.4491 / 600409.SH cost=6.3852`（孤儿自校准机制对 ENS 同样生效）

`D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20260911.log`（1496B）：
- 08:57:09 `[P16V13][CFG-FALLBACK] 当日成本表缺失，回退最近表 date=20260910` + `[P16V13][INIT] 外部成本表 2 条`
- 14:57:12 / 20:56:23 两次 init 均直接读到 `外部成本表 2 条`（无 FALLBACK）

✅ 全部与看板一致。
*小注记：看板称「G2 00:55 经回退」——00:55 时 0911 表尚未生成，读到 2 条逻辑上只能来自 0910 回退表，结论成立；但该次 init 的日志块里没有 [CFG-FALLBACK] 行（08:57 进程才有），推测回退提示按进程打印存在输出时序差异，不影响结论。*

**④ schtasks —— 数字属实，但看板结论错误（FAIL，需更正）**

`schtasks /query /tn Quant_P16_CfgPremarket_0900 /v`：
```
Last Run Time: 2026/9/11/周五 12:05:29
Last Result:  0
Next Run Time: 2026/9/14/周一 9:00:00
Schedule: Weekly MON-FRI 09:00
```
数字与任务书预期一致。**但** `D:\QuantLab\projects\Project_16_LightGBM股票大师\data\schedules\gen_positions_cfg_premarket.log` 实锤 09-11 当天有**两次**运行：

- **09:00:02（准点）**：`==== P16 风控成本锚盘前生成开始 ====` → `[FALLBACK] 持仓快照缺失回退 positions_20260910.json` → 生成 G2 表（001266 1300@34.0766 + 300475 200@170.1475）`[09:00:02] gen_positions_cfg_g2 exit=0` → V13 表（601058@14.4838 + 601579@23.9662）`gen_positions_cfg_v13 exit=0` → ENS `[SKIP] g2_ens_hold_dates 无当日快照 空仓不生成` `gen_positions_cfg_ens exit=0`
- **12:05:34（第二次）**：再次全量生成（此时 ENS 已 09:54 建仓，ENS 表含 300138/600409），12:05:35 三生成器 exit=0

**结论**：看板「预生成任务 09-11 实际 12:05 才运行（比 09:00 晚约 3h）」「桥端 08:57 init 因此走 CFG-FALLBACK」两句中——前半句**错误**（任务 09:00:02 准点运行过，schtasks Last Run 只显示最近一次=12:05 的重复运行）；后半句归因**错误**（08:57 init 走 FALLBACK 的真实原因是 **init（08:57）早于 09:00:02 生成约 3 分钟的结构性时序**，与 3 小时延迟无关，且该 3 分钟缺口每天都会存在，靠 CFG-FALLBACK/CFG-RELOAD 兜底，无实际影响）。
*附带事实：同日 Quant_Monitor_0945 的 schtasks Last Run 也是 12:05:29，且 monitor_20260911_094502.log（09:45 正常触发）与 monitor_20260911_120534.log（12:05）并存——12:05:29 存在一次跨任务的批量二次触发事件（手动补跑或调度器事件），具体原因日志无法判定。*

### A2. 完成判定抽查

**T-20260904-004（G2 大盘门控）—— PASS**
`projects\Project_16_LightGBM股票大师\g2_config.py` L86-95：
```python
# ---- 大盘门控（TIER_RULES，2026-09-11 补上，对齐 V1.3 口径 T-20260904-004）----
#   T=0：<= TIER_STOP_PCT (-1.5) → 停买 ... T=1：<= TIER_HALF_PCT (-1.0) → 半仓 ...
# 数据缺失 → fail-safe 按 T=1 半仓 + 醒目告警
TIER_STOP_PCT = float(os.environ.get("G2_TIER_STOP", "-1.5"))
TIER_HALF_PCT = float(os.environ.get("G2_TIER_HALF", "-1.0"))
DEPLOY_PCT = 0.95; HALF_DEPLOY_PCT = 0.50
```
且 rebalance_g2.py L398-404 实装（tier=None → fail-safe T=1 半仓并打印告警）。

**T-20260904-005（G2 仓位闭环）—— PASS**
`rebalance_g2.py` L421-427：
```python
kept_invest += float(ld.get("cost", 0) or 0) * int(ld.get("vol", 0))
avail = max(capital - kept_invest, 0.0)
budget_each = avail * deploy_pct / float(n_slots)
```
✅「剩余可用资金 − 保留仓成本」逻辑在位。

**T-20260907-004（V1.3 内置风控 184724 在跑）—— PASS**
- 国金QMT 0911 日志 08:57/14:57/20:56 三次 `[P16V13][BUILD] BUILD_TAG=20260910-184724 account=67014907`
- 国金QMT 0912 日志 08:52:37 同样输出（`[P16V13][INIT] date=20260912 pending=0 positions=2`）
- 心跳 heart_v13_20260912.json `build_tag=20260910-184724`

**T-20260902-001（超买残留清除）—— PASS**
- `g2_bridge\state\positions_20260910.json`：仅 001266(1300@34.0766) + 300475(200@170.1475)
- `g2_bridge\state\positions_20260911.json`：600409(7400)/300138(4100)/300475(200)/001266(vol=0 已清)
- 两日均无 600262/300964 ✅（09-09 到期减持消除的结论属实）

### A3. 15 条陈旧裁决逐条判断（是否仍影响 G2/V13/ENS）

| ID | 内容 | 判断 | 依据 |
|---|---|---|---|
| T-20260814-007 | QMT 模拟盘同跑核对（ATR/V2 门禁） | 不影响 | ATR/V2 专属门禁；三策略为 P16 体系。它曾是 P13 上载阻塞（P13 非三策略） |
| T-20260814-003 | P10 脏仓位清理 | 不影响（已验证） | 与 G2/ENS 共享账号 70180771——当前 positions_20260911.json 仅含 600409/300138/300475/001266(0)，全部为 P16 策略持仓，无 V2/ATR 残留 |
| T-20260814-005 | ATR 8只集中部署 | 不影响 | ATR 策略，与三策略无交集 |
| T-20260814-006 | 双策略生产委托审计 | 不影响 | ATR/V2 审计收尾，R1-R8 已落地 |
| T-20260810-001 | ATR+VT overlay | 不影响 | 已证伪不部署 |
| T-20260809-001 | P10 模拟盘复跑 | 不影响 | 已执行完毕 |
| T-20260809-003 | P10 与 SellStrategyEngine 对接 | 不影响 | P10 专属排期；三策略风控不走 SellStrategyEngine |
| T-20260809-004 | P10 组合层配置 | 不影响 | P10 专属 |
| T-20260809-005 | ATR 杠杆增强 | 不影响 | 并入 T-20260810-001 证伪 |
| T-20260809-010 | P10 恢复调试时间窗 | 不影响 | P10 遗留 |
| T-20260811-003 | 容量约束落真实策略 | 不影响 | P2 研究项，容量已部分覆盖 |
| T-20260811-004 | 引擎静态宇宙前视告警 | 不影响 | backtest/engine.py 研究设施；P16 链路不用该引擎 |
| T-20260824-001 | ATR 远程 config 覆盖旧号 67014907 | 不影响（已验证，留观察） | 67014907=V13 账号。V13 0911/0912 日志 `positions=2`（仅 601058/601579），本机无幻影持仓；V13 孤儿纳管对 cost=0 持仓跳过评估，远程 ATR 即使残留在账也不改 V13 风控行为。若远程机 ATR 仍打 67014907 只影响账户总资产口径 |
| T-20260823-003 | ATR 选股口径验证 | 不影响 | ATR 专属 |
| ⚠️ 附注 | 并发 agent ATR build 编码腐蚀 | 不影响 | 损坏的是 `Project_ATR_lowvol/build/strategy_atr_lowvol_equalweight.py` 工作区副本；P16 产物独立（`build/strategy_p16_v13_risk.py` mtime 09-10 18:47 正常） |

**A3 结论：15 条均无需回提。** 与三策略共享账号的两条（T-20260814-003@70180771、T-20260824-001@67014907）已用当前账户持仓快照验证无残留，归档合理。

---

## 3. Part B 三策略运行健康（G2/V13/ENS）

### B-1 三桥心跳（全部健康）

| 桥 | 文件 | last_heartbeat | build_tag | pending |
|---|---|---|---|---|
| G2 | `D:\QMT_POOL\g2_bridge\state\heart_20260912.json` | 2026-09-12 08:55:42 | 20260910-184724 | 0 |
| V13 | `D:\QMT_POOL\p16_v13_risk\state\heart_v13_20260912.json` | 2026-09-12 08:55:32 | 20260910-184724 | 0 |
| ENS | `D:\QMT_POOL\p16_ensemble_bridge\state\heart_20260912.json` | 2026-09-12 08:59:04 | 20260910-233536 | 0 |

三桥 build_tag 与部署记录（G2/V13=184724、ENS=233536）及 0911 日志三方一致，risk_sold 空、last_cmd_seq 正常。

### B-2 两 QMT 客户端 20260912 日志（三策略今日均有输出）

- `D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20260912.log`：08:52:37 G2 `[P16G2][CFG-FALLBACK] 回退 0911 表` + `[P16G2][INIT] 外部成本表 1 条 positions=1`；ENS `[P16ENS][INIT] 外部成本表 2 条 positions=2`
- `D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_20260912.log`：08:52:37 `[P16V13][CFG-FALLBACK] 回退 0911 表` + `[P16V13][INIT] 外部成本表 2 条 positions=2`

（09-12 为周六非交易日，客户端 08:52 启动后走 FALLBACK 兜底属正常路径；心跳 08:55-08:59 刷新确认三策略进程存活。）

### B-3 最新持仓、资产与成交

**G2（70180771 子账）**：
- `g2_bridge\state\positions_20260911.json`：300475.SZ 200股@170.1475（市值 33,538）——001266 vol=0 已清
- `g2_bridge\state\fills_20260911.json`：`SELL_STOP 001266.SZ 1300股@31.68 FILLED reason=SELL_STOP`（09:46:33）
- `g2_bridge\state\asset_20260911.json`：total_asset 9,991,318.06 / cash 9,865,967.06 / market_value 126,240

**ENS（70180771 子账，对照 T-20260910-109 融合首日建仓）**：
- `p16_ensemble_bridge\state\positions_20260911.json`：600409.SH 7400股@6.3852（45,880）+ 300138.SZ 4100股@11.4491（46,822）✅ 首日两笔建仓在账
- `p16_ensemble_bridge\state\fills_20260911.json`：两笔 BUY FILLED（600409@6.37、300138@11.44）✅
- `p16_ensemble_bridge\cmd\positions_cfg_20260911.json`：成本锚 300138@11.4491 + 600409@6.3852 已落盘（15:51:50）
- `p16_ensemble_bridge\state\peak.json`：300138=11.4491 / 600409=6.3852（09-12 08:59 刷新）
- 账户级市值 126,240 = G2 33,538 + ENS 92,702 ✅ 两策略持仓可完整解释，无孤儿

**V13（67014907）**：QMT_POOL\p16_v13_risk 下无 positions/asset 快照（V13 桥设计上不做快照，风控只认成本表）——持仓即成本表两票：601058.SH 3100@14.331、601579.SH 23.9662（0911/0912 日志 `positions=2` 交叉确认）。fills_v13 最新为 20260909（09-10 起无成交，正常）。

### B-4 数据新鲜度

- **enh 面板**：`projects\Project_16_LightGBM股票大师\data\feature_panel_v3_enh.parquet`（mtime 2026-08-23 20:36:33，4,750,508 行）——**最新数据日期 2026-08-14**（pandas 直读 trade_date.max()）✅ 与 T-20260912-001「冻结 2026-08-14」精确一致，缺口属实
- **基础面板**：`data\feature_panel_v3.parquet`（mtime 2026-09-11 16:40:09，4,807,456 行）——最新数据日期 **2026-09-11**，日更链路活着

### B-5 资金池当前值

| 策略 | 文件 | 值 | 更新时间 | 滚动机制 |
|---|---|---|---|---|
| G2 | `D:\QMT_POOL\g2_bridge\g2_strategy_capital.json` | **97,863.5**（note: realized=-1637 float=-500） | 09-11 15:49:01 | reconcile_g2 `_roll_g2_capital` 每日滚动 ✅ |
| ENS | `D:\QMT_POOL\p16_ensemble_bridge\ens_strategy_capital.json` | **100,000.0**（初始值） | 09-10 23:36:08 | **无**——reconcile_ens.py 无 capital/_roll 相关代码（grep 0 命中）⚠️ 观察项 |
| V13 | `D:\QuantLab\projects\Project_16_LightGBM股票大师\data\strategy_capital.json` | **88,001.78**（realized -9,156.23 / float -2,841.99） | 09-11 16:41:11 | run_scheduled.ps1 daily 模式 L132 挂 strategy_capital.py 每日滚动 ✅ |

注：`D:\QMT_POOL\p16_v13_risk\` 目录下**无**资金池文件——V13 资金池唯一事实源在 Project_16\data\strategy_capital.json，QMT_POOL 侧不存在副本，无歧义。
ENS 观察项说明：ENS 09-11 建仓市值约 92,702（占池 92.7%，在 95% 部署线内），当前浮盈小、失真尚小；但 G2/V13 均有每日滚动而 ENS 没有，随盈亏累积买入预算会逐渐失真（与 DE 体检 P1-2 资金池滞后同类问题），建议补 reconcile_ens 滚动或挂 strategy_capital 机制。

---

## 4. Part C 三策略相关未完成任务状态核对

### C-1. T-20260912-001（enh 面板刷新链路缺失）—— **与代码事实一致**

- 全库 `*.py` 搜索 `feature_panel_v3_enh.parquet`：7 处命中，**全部为读取**——
  - `build_g2_daily.py` L31（实盘推理 asof 读）、`train_g2.py` L44（训练 asof 读）、`build_panel_v3_sc.py` L26（读 enh，L75 写出的是 `feature_panel_v3_sc.parquet`）、`overnight_opt_20260825.py` L31（读 enh，L172-173 写出的是 `feature_panel_v3_enh2_n3.parquet`）、`_tmp_g2check.py`（检查脚本）
- `scripts\` 目录：0 命中
- → **全库无 writer** 结论成立 ✅
- `refresh_panel_v3.py`：只写 `data_live/merged_daily_full.parquet`（L29/L74）与 `data/feature_panel_v3.parquet`（L121），切片特征来自 `features_v3.json`——实测 `feature_cols` 长度 = **27** ✅「只重建 27 特征基础面板、不写 enh」描述准确
- 面板最新日期 2026-08-14（见 B-4）与任务书预期一致 ✅

### C-2. T-20260910-103/104（纸面前向三臂管道）—— **一致，文件齐全**

`projects\Project_16_LightGBM股票大师\` 下存在：`paper_forward.py`、`paper_forward_ab_stats.py`、`paper_forward_exit.py`、`g2_candidates_night.ps1`、`paper_forward_daily.ps1`。
两管道调度存活：
- `Quant_P16_G2Candidates_Night`：MON-FRI 20:05，Last Run 2026/9/11 20:05 exit 0（g2_candidates_night.log 20:08:40）
- `paper_forward_daily`：MON-FRI 16:45，Last Run 2026/9/11 16:45 exit 0

### C-3. T-20260910-105（merged_daily_full 每日刷新覆盖）—— **已覆盖**

- **quant_daily_update**（MON-FRI 16:30，`run_scheduled.ps1 -Mode daily`，Last Run 09-11 16:30 exit 0）：daily 模式串跑 `增量更新 → merge_live_features.py --date $latest → refresh_panel_v3.py（L29/L74 写 merged_daily_full + L121 写 v3 面板）→ deploy_predict.py`——**每日刷新已覆盖**（run_scheduled.ps1 L110-114 注释明确「此前面板只在周更重训时更新，周中面板落后」已改为 daily 内跑）
- **quant_weekly_retrain**（MON 17:00，`-Mode retrain`）：L179 再跑 refresh_panel_v3.py（双保险）
- 佐证：feature_panel_v3.parquet 最新日期 2026-09-11 = 09-11 16:30 daily 链路产物

### C-4. T-20260910-107（qmt_monitor 并行观察）—— **一致**

- schtasks 中 6 个 monitor 任务全部在册 Active（Ready）：`Quant_Monitor_0945/1030/1100/1330/1400/1430`，均执行 `run_scheduled.ps1 -Mode monitor`（qmt_monitor.py --once，仅预警）；09-11 五个盘中时段日志齐全（monitor_20260911_094502/103002/110002/120534/133002/140002.log）
- V1.3 内置风控 184724 已在 0911/0912 日志确认（见 A2-3）→ 「稳定 ≥1 周后停 qmt_monitor」的观察期计时起点成立
- ⚠️ 附带发现：看板声称的心跳告警任务 **`Quant_P16_Heartbeat_Alarm` 不存在**（`schtasks /query` 报「系统找不到指定的文件」；全任务清单无 heartbeat/bridge/alarm 项）。脚本 `check_bridge_heartbeat.py` 存在（09-11 15:33:01）但无任何调度引用。→ 见第 5 节回提清单

### C-5. T-20260904-006（G2 卖出侧五项）—— **代码现状**

| 子项 | 代码现状 | 与 TODO 一致？ |
|---|---|---|
| ① hold_date 缺失处理 | **已修**：rebalance_g2.py L368-373 `if not since: plan["skips"].append(...fail-safe 跳过卖出...); print("!! ... fail-safe，需人工核查账本"); continue`（源码标注 T-20260904-006①，与 T-20260903-023④ 完成记录对应） | **不一致**——TODO L127 仍把 ① 列为未修（「建仓日缺失即卖出（fail-open，应改为 skip+告警）」），需划掉 |
| ② 成本锚当日缺失 | 已修（T-20260908-002 双层修复，TODO 内已划） | 一致 |
| ③ 卖出冷却期 | **确认无**：全文件无 cooldown/冷却逻辑；卖出仅看持有天数（L365-392），09-03 卖 09-04 买回类震荡仍可能发生 | 一致（未修） |
| ④ V1.3 峰值持久化 | 已修（peak_v13.json 持久化，TODO 内已划） | 一致 |
| ⑤ 卖出侧跌停/停牌过滤 | **确认无**：卖出段 L365-392 无任何跌停/停牌检查（买入侧 L440 仅有低开 gap 过滤，不对称仍在） | 一致（未修） |

另：`load_sellable` 返回 None 时**不校验可卖量**（L381 `if sellable is not None: vol = min(vol, sellable)`，None 直接按 ledger vol 下单）——TODO ①中提到的这一半现状未变，如实记录。

### C-6. T-20260827-003（gpsj_reader ST 语义反转）—— **一致（未修，TODO 状态正确）**

`D:\QuantLab\data\gpsj_reader.py` L58：
```python
_COL_MAP = {
    ...
    "ST": "is_st",
```
仍为直接映射，无 0→1/1→0 反转归一化。TODO 保持未修状态与代码事实一致 ✅

---

## 5. 需要回提 / 更正的清单

### 更正（看板判定与事实不符）

1. **【P1·更正】T-20260910-101 / DONE 段「预生成任务 09-11 实际 12:05 才运行（比 09:00 晚约 3h）」结论错误**
   - 证据：`Project_16\data\schedules\gen_positions_cfg_premarket.log` 含 `[2026-09-11 09:00:02]` 完整会话（g2/v13/ens 三生成器均 exit=0，G2/V13 表 09:00:02 即落盘，ENS 当时空仓 SKIP 属正常），12:05:34 为第二次运行（schtasks Last Run 只显示最近一次）。
   - 08:57 init 走 CFG-FALLBACK 的真实根因 = **桥 init（08:57）早于 09:00 生成约 3 分钟**的结构性时序（每个交易日都会出现），非 3 小时调度延迟；CFG-FALLBACK→09:00 落表→盘中 CFG-RELOAD 的兜底链实测有效，无实际风险。
   - 建议改写为：「任务 09:00:02 准点运行 exit=0；12:05 存在第二次触发（与 Quant_Monitor_0945 同秒 12:05:29，原因不明，建议查任务历史）；遗留小缺口=桥 init 早于 09:00 生成 3 分钟，靠 FALLBACK/RELOAD 兜底」。

2. **【P1·更正】T-20260904-006 子项①已修，TODO 描述未更新**
   - rebalance_g2.py L368-373 已实现 fail-safe 跳过+告警（源码自带 T-20260904-006① 标注，属 T-20260903-023④ 落地）；看板 TODO L127 仍列为未修。③⑤仍真实未修，保留。

### 回提（看板声称已部署/已完成但事实缺失）

3. **【P0/P1·回提】`Quant_P16_Heartbeat_Alarm` 计划任务未注册**
   - 看板 T-20260903-022「①心跳告警 check_bridge_heartbeat.py + 任务 Quant_P16_Heartbeat_Alarm（盘中每 30 分钟巡检三桥，超 600s 飞书告警）」与 T-20260903-023 B 段均声称已部署；T-20260903-023 还写「待验（下周一自动首跑）：…心跳告警…」。
   - 事实：`schtasks /query /tn Quant_P16_Heartbeat_Alarm` → ERROR 系统找不到文件；全量任务清单（含所有 Quant_*/QuantLab_* 任务）无 heartbeat/bridge/alarm 任务；`check_bridge_heartbeat.py` 脚本存在但无任何 .ps1/任务引用（grep 仅自身文档命中）。
   - 后果：09-11 V13 宕机 3.5h 类事件再发生时**无自动告警**。建议立即注册任务并核对 T-20260903-023 待验清单。

### 观察项（非看板不符，建议排期）

4. **ENS 资金池无滚动机制**：`ens_strategy_capital.json` 停在初始 100,000（09-10 23:36），09-11 建仓后未滚动；`reconcile_ens.py` 无 capital 逻辑（对照 G2 `_roll_g2_capital`、V13 daily 挂 `strategy_capital.py`）。失真会随盈亏累积。
5. **09-11 12:05:29 跨任务批量二次触发**（CfgPremarket_0900 与 Monitor_0945 同秒 Last Run，且 Monitor_0945 当日 09:45 已正常跑过）：原因不明，若为人工补跑无碍，若为调度器异常需关注（Windows 任务历史可查证，本次只读审计未深挖）。
6. **G2 00:55 init 日志缺 [CFG-FALLBACK] 行**（08:57 进程有）：回退结论成立（00:55 读到 2 条只能来自 0910 表），疑回退提示打印时序问题，优先级低。

---

## 附：本次审计覆盖的证据文件清单（全部只读）

- 看板：`D:\QuantLab\全局控制台.md`（L100-221 DONE/TODO/陈旧段）
- QMT_POOL：g2_bridge/{cmd,seq}/positions_cfg_20260911.json、state/{peak,heart_20260912,positions_20260910,positions_20260911,asset_20260911,fills_20260911}.json、g2_strategy_capital.json；p16_v13_risk/{cmd/positions_cfg_v13_20260911.json, state/{peak_v13,heart_v13_20260912}.json}；p16_ensemble_bridge/{ens_strategy_capital.json, cmd/positions_cfg_20260911.json, state/{heart_20260912,positions_20260911,asset_20260911,fills_20260911,peak}.json}
- QMT 日志：`D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_2026091{1,2}.log`、`D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_2026091{1,2}.log`
- 调度：schtasks /query（Quant_P16_CfgPremarket_0900 /v、quant_daily_update /v、quant_weekly_retrain /v、Quant_Monitor_0945 /v、paper_forward_daily /v、Quant_P16_G2Candidates_Night /v、全量清单、Heartbeat_Alarm 定向查询）
- 代码/数据：g2_config.py、rebalance_g2.py、refresh_panel_v3.py、merge_live_features.py、run_scheduled.ps1、build_panel_v3_sc.py、overnight_opt_20260825.py、train_g2.py、build_g2_daily.py、reconcile_ens.py、gpsj_reader.py、features_v3.json、feature_panel_v3{,_enh}.parquet（pandas 只读列读取）、strategy_capital.json、gen_positions_cfg_premarket.log
- 面板日期读取使用 miniqmt venv Python（`C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe`，pandas 只读，无写入）
