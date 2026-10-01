# market_phase 移植与全历史回填验证报告

日期：2026-09-27 | 执行：op（GLM 会话）| 状态：✅ 完成并通过锚点验证

## 一、任务定位

将 TSP（`shy3130/tick-stock-panel`，MIT，审计报告 `research_external/TSP_audit_DE_20260926.md`，评级 ⭐⭐⭐）的
市场情绪周期 6 阶段模块移植为 QuantLab 研究资产：

- 模块：`factors/market_phase.py`（约 365 行，纯 pandas 向量化）
- 回填脚本：`scripts/backfill_market_phase.py`（约 260 行，含 `--calibrate` 标定模式）
- 输出：`research_study/market_phase_backfill/daily_phase.csv`（4298 交易日，2010-04 ~ 2026-09-11）

**定位声明（重要）**：本模块是**描述性 regime 标签**（研究侧资产），不是 P17「情绪温度计反转择时」已证伪假设
（2026-08-26 归档）的翻案。P17 证伪的是「情绪 z 分 → 宽基反转择时」这一假设；本模块提供的是另一套口径
（阈值法连板梯队阶段），其价值在于环境描述与未来研究的状态变量，**未做任何收益预测力检验**。
消费红线：连板梯队/封板率是收盘截面信息，**T 日标签只能 T+1 使用**（防 look-ahead）。

## 二、移植内容（与 TSP 原版逐项对齐）

| 组件 | 说明 |
|---|---|
| 涨停判定 | tushare `up_limit` 列（`abs(close-up_limit)<=0.005`），不自实现取整规则；触板 `high>=up_limit-0.005` |
| streak 连板 | run 边界法向量化：`change = code_change \| v翻转`，`consec = idx - run_start + 1` |
| 5 档 state | 4 维（profit .35 / speculation .25 / resilience .20 / trend .20）→ score → strong/lean_strong/range/lean_weak/weak（阈值 70/55/45/30） |
| 6 阶段 | ice/ignite/rally/climax/ebb/repair，驱动量 EMA(α=1/3) 平滑 + 2 日确认；优先序 climax > rally > **ice > ebb** > ignite > repair |
| 否决 | 正面阶段（climax/rally/ignite）遇 state ∈ {weak, lean_weak} 降级 repair |
| index_pct | 510300 ETF 日涨幅（TSP 用指数行情），缺失日记 0；above_ma20 用全 A 后复权价 |

## 三、开发过程中发现并修复的 bug（3 个）

1. **streak off-by-one**：初版单日涨停被计为 2 连板 → climax 假象 60.3%。改为 run 边界法后单票手工序列测试通过。
2. **`_prev_consec` 置 NaN 时机**：在 roll 之前置空导致串票/丢值，移到 roll 之后。
3. **segment_stats 两个报错**：`reset_index` 撞列名、`.size()` vs `.sum()` 混用。

## 四、重标定（本轮核心发现）

### 4.1 TSP 原阈值在 astock 上不可用（实证）

首轮全量回填（TSP 原阈值）：repair 61.4% / **rally 29.3%** / ignite 7.8% / climax 1.5% / **ebb 0% / ice 0%**。

根因：**astock 的 promo_rate（晋级率）分布系统性高于 TSP 数据**——

| 分位 | astock promo | TSP promo（其文档口径） |
|---|---|---|
| p10 | 0.154 | ~0.13（其 EBB_STRICT 附近） |
| p20 | 0.181 | 0.15（=其 EBB_PROMO） |
| p60 | 0.270 | 0.23（=其 RALLY_PROMO） |

→ TSP 的 ebb/ice 阈值整体落在 astock 分布的 p10 以下，**永不触发**；rally 阈值（0.23）只相当于我们的 p55，过度触发。
2024-01 微盘崩被误标 rally 即为例证。first_board 高度吻合（p10 26 vs 24、p90 87 vs 88），说明差异主要在晋级率口径
（可能与 TickFlow 停牌行处理/涨停判定边界有关，未深究）。

### 4.2 重标定方法与结果

按 TSP 自己的标定配方（分位数规则，见模块常量块注释）在 astock 2020-08-24 ~ 2026-09-11（1468 交易日）重算，
`--calibrate` 一键生成，硬编码进模块（TSP 原值保留为注释对照）。重标定后分布：

| phase | 占比 | 段数 | 平均段长 | 最长 |
|---|---|---|---|---|
| repair 修复 | **74.9%** | 71 | 15.5 | 52 |
| rally 主升 | 12.7% | 34 | 5.5 | 24 |
| ignite 启动 | 8.3% | 33 | 3.7 | 9 |
| ebb 退潮 | 2.5% | 9 | 4.0 | 7 |
| climax 高潮 | 1.0% | 2 | 7.5 | 11 |
| ice 冰点 | 0.6% | 2 | 4.5 | 7 |

与 TSP 参考口径（修复 ~74%、平均段长 ~10 天）吻合。

## 五、锚点验证

| 锚点 | 预期 | 结果 |
|---|---|---|
| **A1 九二四行情**（2024-09-24~10-31） | 启动→主升→高潮 | ✅ 09-27 rally → **10-08 climax**（fb=423/ge2=433 峰值）→ 修复 → 10-23 ignite → 10-25 rally → **10-31 climax** |
| **A2 微盘崩**（2024-01-02~02-08） | 退潮/冰点 | ⚠️ 部分：崩盘日无误标（rally 假阳性清除、否决生效、全落 repair），但 ebb/ice 未在窗口内触发——1 月连板生态实际靠中字头/ST 维持中等热度（ge2 5~35、promo EMA >0.20），EMA 滞后 + 阈值未至，落 repair 属可辩护而非 bug |
| **A3 ST 重组潮 17 板**（2024-10~11） | climax | ✅✅ 10-31~11-12 climax 段，height 在 11-06 恰好 = 17 板（我们口径该波最高 21 板，11-12） |

ebb/ice 落点抽查：ebb 集中在 2020-11（妖股潮退潮）、2021-10（9 月周期潮后死水）、2026-06~08；ice 落在
2023-03 末与 2023-08（公认死水期）。均合理。

**climax 全期仅 2 段 = 2024-10-08 与 2024-10-31~11-12**，与 TSP 文档「6 年仅 2 段」完全一致。

## 六、已知限制与后续

1. **streak 跨停牌不断链**（无行即延续）：与 TSP 的停牌行处理可能有差异，妖股停牌核查场景会抬高 height；
2. **A2 类崩盘不触发冰点**：价格崩≠连板生态冰点（中字头对冲），若用于风控需与 P10 大盘 MA200 门控互补而非替代；
3. **无上市 5 日无涨跌幅窗口排除**（TSP 有）：次新首日 44% 板会计入 first_board，影响待评估；
4. 重标定窗口含 2024-10 极端值，climax 阈值（ge2≥62）本质由九二四行情定义——样本仅 2 段，属已知小样本限制；
5. **未做收益预测力检验**——如未来要用于择时，必须按 P17 教训先预注册判据再检验，禁止先看结果再定标准。

## 七、A 方案落地：每日标签管道（2026-09-27 同日完成）

模块从研究资产升级为每日运行资产（诚哥拍板 A 方案）：

- **脚本**：`scripts/daily_market_phase.py` — astock 主库优先 + Tushare 在线补缺（daily/stk_limit/
  adj_factor/index_daily，2000 积分档实测全可用；主库常态滞后，在线路径是事实主路径）；
  80 交易日滚动窗口（MA20 预热 + EMA 收敛 + 5 日回看 + 2 日确认），口径与回填脚本逐行一致；
  非交易日自跳过；tushare 单日失败重试 3 次后 fail-loud。
- **产物**：`D:/QMT_POOL/market_phase.csv`（原子写整文件覆盖，含逐日 `src` 来源列）+ 飞书日报卡片
  （复用 `qmt_card.send_lark_card`，S4U 环境清扫继承 T-20260923-004 修复）。
- **调度**：`Quant_MarketPhase_Daily` 周一~五 20:30（S4U + Register-ScheduledTask 对象方式，
  Args 回读核验 + ps1 BOM `EF BB BF` 合规）；S4U 会话端到端实测 exit=0（含真实推送）。
- **新发现（P1 级数据质量）**：astock 主库 **2026-07-27 up_limit 全列 NaN**（单日数据空洞），
  回填产物该日静默缺失；每日脚本已按「有效涨停价行数 ≥100」判有效覆盖并从 Tushare 补齐——
  live 产物在该日比全量回填更完整。全量回填 CSV 的这一缺口待下次离线包升级后重跑修复。
- **口径一致性实测**：与全量回填重叠 71 日中 9 日不一致，全部位于窗口头 15 天（EMA/MA20/连板
  预热区，设计内）；07-27 起（含全部近期与末日标签）逐日一致。**末 日 标 签（推送对象）可靠**。
- **首次自然运行**：2026-09-28（周一）20:30；核对点 = 飞书卡片到达 + CSV 末日 = 当日 +
  任务 LastResult=0。

## 八、复现命令

```powershell
# 全量回填 + 锚点验证（~60s）
C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe D:\QuantLab\scripts\backfill_market_phase.py
# 阈值重标定（打印常量块）
C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe D:\QuantLab\scripts\backfill_market_phase.py --calibrate
# 每日标签（已由计划任务 Quant_MarketPhase_Daily 20:30 自动运行；手动补算示例）
C:\Users\Administrator\.workbuddy\binaries\python\envs\miniqmt\Scripts\python.exe D:\QuantLab\scripts\daily_market_phase.py --date 20260924
```
