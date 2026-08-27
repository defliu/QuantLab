# 待执行清单

> 本清单记录暂时搁置、需要在合适时机执行的任务。每项含背景与执行方式，任何会话接手均可直接执行。

---

## [进行中] 审计修复（2026-08-22 第三方审计，用户批准开工）

> 开工依据：`TODO_PENDING.md` + `第三方审计报告_20260822.md` + `第三方审计验证报告_20260822.md`（均在本目录）。
> 审计结论：**NO-GO** —— 现有回测证据不足（P0-1 可执行口径下日均超额转负）。按下方修复路线执行，逐项完成后更新状态。
>
> **2026-08-22 用户决策**：重训放研发电脑（内存充足）跑，跑完同步到服务器。服务器端接收脚本已建好：`D:\quant_server\app\sync_from_dev.ps1`（用法见文件头注释）。任何会话接手：等用户同步后运行该脚本归位+校验，再继续 R1 重跑。
>
> **2026-08-23 用户决策**：QMT自动交易工作流加入备用池，当前继续按原工作流运行。工作流命名与完整方案见 `WORKFLOW_REGISTRY.md` / `QMT_AUTO_TRADE_WORKFLOW.md`。
>
> **2026-08-23 本机重跑（研发电脑，正式全市场面板）**：R1 可执行口径正式重跑、R2 复权重训已完成，见下方"已落地成果"。补数据测试发现"回测缺评分信号"，v3_enh 定为当前最优基线（可执行 0.1% 滑点 +0.056%）。

### 修复路线（来自审计报告第六节）

| # | 优先级 | 任务 | 状态 | 说明 |
|---|---|---|---|---|
| R1 | **P0 立即** | 回测改 open→open 口径 + 一字板/停牌过滤，重跑 `scan_rotate_cost` | ✅ **已完成**（2026-08-23 本机正式面板重跑） | 代码已改（--exec/--naive 双口径）；**本机全量重跑时发现并修复 2 个 bug**（见"已落地成果"）。可执行口径结果：未复权模型最优 N=3 0.1%滑点 **-0.108%**（NO-GO），详见 `data/scan_rotate_cost_report.md` |
| R2 | P1 短期 | 复权重建面板（close×adj_factor）并重训 v1/v2/v3 | ✅ **已完成**（2026-08-23 本机） | `build_features_v2.py` 复权版全量重建 475 万行 → 重生成 v3 → 重训。复权有效性验证：新旧标签差异>1% 占 0.17%，最大 131.7%（除权日假跳空消除）。复权后可执行口径最优 N=5 0.1%滑点 **-0.023%**（仍 NO-GO） |
| R3 | P1 短期 | 成交回报回填（query_stock_trades 回填真实成交价/量） | ⏳ 未开始 | qmt_trader/rebalance_daily 日志补状态字段 |
| R4 | P1 短期 | 移动止盈峰值跨日持久化（peak_state.json） | ⏳ 未开始 | qmt_monitor peak 不再每进程重置 |
| R5 | P1 中期 | 特征筛选去泄漏（只用训练期选特征） | ⏳ 未开始 | P1-3 经实证降级为 P2，影响极小但应修 |
| R6 | P1 中期 | 参数选择去数据窥探（预留 2026 纯净验证期） | ⏳ 未开始 | 红线58/TOP2/N=1 需按可执行口径重扫 |
| R7 | P2 持续 | 盯盘回退逻辑修复（load_positions_from_log 扣 SELL） | ⏳ 未开始 | 复用 rebalance_daily 净额逻辑 |
| R8 | P2 持续 | 报告产物自动校验（NaN/信号衰减标记） | ⏳ 未开始 | backtest_dual annual_return NaN 等问题 |

### 已落地成果（2026-08-23 本机重跑）

**R1 修复的 2 个 bug（服务器版脚本）**
1. **持仓收益取价绑定 TOP10 快照**：原用每日选股快照的 `open_next` 盯市，持仓跌出 TOP10 即冻结为 0，导致胜率虚降到 9.7%、回撤 -65%~-95%。改用全市场 `open_map` 逐日盯市后胜率恢复 50.6%。
2. **持有期 N 未生效**：可执行口径卖出条件写死 N=1 的 `>=2`，导致 N=1/3/5/10 结果全相同。改为 `>= N+1` 后各持有期正确分化。

**R2 复权重训**：`build_features_v2.py` 复权版全量重建 v2 → 重生成 v3 → `train_optuna.py --panel v3` 重训（test IC 0.044）。复权有效性：除权日假跳空标签被消除（新旧 fwd_ret 差异最大 131.7%）。

**补数据测试（用户发现"回测缺评分信号"）**：核对 F1-F6 后发现——
- F3 三列（`ex_days_since`/`fc_pchange`/`dv_year_sum`）在 v3 精简面板**缺失**，回测中 F3 退化为二元信号；
- F2/F5 用的是离线代理（量比/相对动量），非真实资金流/板块涨幅；
- 补数据测试结论：F3 单独补入最优 +0.016%（微正）；F3+ind_mom20+换手分位（v3_enh）最优 N=5 0.1% 滑点 **+0.056%**（当前最优基线）；再堆 6 个板块动量特征（v3_enh2）反而崩坏（top2 次日 +0.12%，动量反转+特征冗余）。

**关键洞察（消融实证）**：板块动量是**反转因子**（IC 全为负：ind_mom20 -0.026 / rel_mom_20 -0.052），不是延续因子；单板块特征（ind_mom20）中性无害，堆叠高度相关板块特征会导致模型追高反转偏置（树数骤降到 31）。

### 当前最优基线（2026-08-23 定版）
- **v3_enh**（27 原特征 + F3 三列 + ind_mom20 + turnover_rank，1373 树）
- 面板 `data/feature_panel_v3_enh.parquet` / meta `features_v3_enh.json` / 模型 `D:/QuantLab/models/lgb_model_v3_enh.txt`
- 可执行 open→open 0.1% 滑点 N=5 日均超额 **+0.056%**、胜率 55.1%、回撤 -26.8%
- 判定门槛 +0.3% **未达到**，但方向已从"必然亏"转为"可执行正超额"；后续靠补 F2 真实资金流 + 参数重扫（R6）继续提升

### 注意
- 验证报告确认 P1-3 技术性泄漏存在但实证影响可忽略（训练期与全期 ICIR 判定一致），降级为 P2。
- 审计验证脚本（audit_bias.py/verify_executable.py 等）不在 GitHub 仓库，需重建。

---

## [待定] D:\tmp 数据源归档（用户决策：先放着）

- 状态：**用户暂不处理**（2026-08-22 决定"先放着吧"）
- 背景：用户从原开发机下载了完整数据源到 `D:\tmp`，用于完善服务器数据源、供未来其它策略复用。

### D:\tmp 现有内容（已核验）

| 内容 | 位置 | 规模 |
|---|---|---|
| 日线主库 | `D:\tmp\行情数据更新至2026.8.14\stock_daily.parquet` | 1.24GB，1454 万行，5820 只，到 2026-08-14 |
| 分钟线(1min) | `D:\tmp\行情数据更新至2026.8.14\stock_1min\` | 68.3GB |
| 分钟线(5min) | `...\stock_5min\` | 17.9GB |
| 分钟线(15min) | `...\stock_15min\` | 7.3GB |
| 分钟线(30min) | `...\stock_30min\` | 4.2GB |
| 分钟线(60min) | `...\stock_60min\` | 2.5GB |
| 财务数据 | `D:\tmp\财务数据更新至2026.8.21\`（8 张表） | 0.56GB，到 2026-08-21 |

> 分钟线合计约 99GB（2009-2026 全历史，格式：ts_code+OHLCV+adj_factor，MultiIndex 时间）。

### 后续可选动作（任何会话接手可执行）
- 日线/财务与现有主库一致，无需重复迁移；如需覆盖校验可对比行数与末日期。
- 分钟线按需迁入 `D:\quant_server\astock\minute\`（D 盘当前空闲约 44GB，无法全量放入 99GB；建议只迁 60min+30min+15min 约 14GB，或先腾盘）。
- 待办（未完成）：原 `lgb_model_v3.txt` + 2 个 `feature_panel` 正式资产仍需从原开发机补传（见下方条目）。

---

## [待办] 补传 3 个正式资产（模型 + 2 面板）并恢复选股链路

- 状态：**待用户补传**（2026-08-22 建立；用户出门，回原开发机后传输）
- 背景：2026-08-22 在服务器 `D:\quant_server` 验证「周一重训」链路时，用小样本（`--limit 300` + `--n-trials 2`）跑通了 `build_features_v2.py → 生成 v3 面板 → train_optuna.py --panel v3`，但该次运行**覆盖**了服务器上 3 个正式资产，现为测试版，需从原开发机恢复。

### 需补传的 3 个文件

| # | 文件 | 原开发机来源 | 服务器目标位置 | 大小 |
|---|---|---|---|---|
| 1 | `lgb_model_v3.txt` | `D:\QuantLab\models\` | `D:\quant_server\models\lgb_model_v3.txt` | ~5.7 MB（1962 棵树） |
| 2 | `feature_panel_v2.parquet` | `Project_16\data\` | `D:\quant_server\app\data\feature_panel_v2.parquet` | ~694 MB（全市场，475 万行） |
| 3 | `feature_panel_v3.parquet` | `Project_16\data\` | `D:\quant_server\app\data\feature_panel_v3.parquet` | ~684 MB（全市场） |

> 传输方式：局域网共享（此前用 `\\192.168.31.131\...`）或其它方式，传到服务器后告知路径即可。

### 接收后执行步骤（任何会话接手照做）

```powershell
cd D:\quant_server\app
# 1. 校验：模型可加载（应为 1962 棵树）、两个面板可读（行数应为全市场级）
python -c "import lightgbm as lgb; m=lgb.Booster(model_file='D:/quant_server/models/lgb_model_v3.txt'); print('trees', m.num_trees())"
python -c "import pandas as pd; d=pd.read_parquet('D:/quant_server/app/data/feature_panel_v3.parquet'); print('rows', len(d), 'stocks', d['ts_code'].nunique() if 'ts_code' in d.columns else 'n/a')"

# 2. 重新生成当日快照并重跑选股链路（确认恢复正式模型）
python merge_live_features.py --date 2026-08-21
python deploy_predict.py --model v3 --top-k 5
```

### 验收标准
- `lgb_model_v3.txt` 加载后 `num_trees() == 1962`
- `deploy_predict.py --model v3` 输出正常，数据源显示"当日快照"

### 注意事项
- GitHub 上**无**这 3 个文件（`.gitignore` 忽略 `*.parquet`，模型从未提交），只能从原开发机本地补传。
- 服务器上当前 `lgb_model_v3.txt` / `feature_panel_v2.parquet` / `feature_panel_v3.parquet` 为小样本测试版，恢复前请勿再用其出正式选股结论。
- 飞书推送仍 `not_configured`（lark-cli 登录态未配置），属待办事项，不影响选股/盯盘脚本本身。

---

## [待办] QMT 客户端内置风控策略（止损/止盈/移动止盈内嵌 QMT，脱离定时任务）

- 状态：**待实施**（2026-08-24 建立，用户要求把方案落盘任务清单）
- 背景：当前风控依赖 Windows 计划任务（`Quant_Monitor_0945/1030/1100/1330/1400/1430` 共 6 个）→ `run_scheduled.ps1 -Mode monitor` → Python 连 miniQMT，链路外部依赖多、触发精度低（约 30 分钟一次，间隙可能漏触发止损/止盈）。用户希望把风控直接做成 **QMT 客户端内置策略脚本**，粘贴到 QMT「模型交易/策略交易」模块运行，交易时段内每 tick 触发，最小化外部依赖。

### 方案要点

- 载体：QMT 客户端策略框架（`ContextInfo`：`init`/`handlebar`/`order_callback`/`deal_callback`，`passorder` 下单），本机 `xtquant/qmttools` 已确认该框架可用。
- 规则复刻现有 `qmt_monitor.py`：止损 -7% / 止盈 +15% / 移动止盈 8%（成本价 + 当日最高价）。
- 涨跌停跳过：`get_instrument_detail` 的 `UpStopPrice`/`DownStopPrice` 判断封板，封板不成交则跳过。
- 委托保障：QMT 原生 `order_callback`/`deal_callback` 回报确认；`passorder` 市价卖出。
- 策略持仓来源：本地 `data/qmt_trade_log.csv`（BUY 且未清空的代码 + 成本价），每次 `handlebar` 刷新，与 9:45 换仓自动同步。
- 唯一依赖：QMT 客户端本身保持登录在线（数据与下单都在其本地，无法避免）。

### 与现状依赖对比

| 依赖点 | 当前(计划任务) | QMT 内置策略 |
|---|---|---|
| Windows 计划任务 | 6 个 | 无 |
| PowerShell 调度 | 依赖 | 无 |
| 外部 Python 进程 | 每次临时拉起 | 无（QMT 内嵌 Python） |
| 触发精度 | 约 30 分钟一次 | 每 tick（毫秒级） |

### 实施步骤

1. 编写 `risk_guard.py`（QMT 策略格式：`init`/`handlebar`/`order_callback`/`deal_callback` + `passorder`）。
2. 在 QMT 客户端「模型交易/策略交易」模块粘贴运行，选 tick 或 1m 周期 + 模拟/实盘交易模式。
3. 模拟盘验证 1-2 天（触发、下单、回报链路）。
4. 确认后切实盘；9:45 换仓、9:25 预判等决策类任务保留现有定时链路，**风控执行与定时任务彻底解耦**。

### 验收标准

- 交易时段内无需任何外部定时，`handlebar` 自动检查止损/止盈/移动止盈并触发市价卖出。
- 涨跌停封板时自动跳过、不无限重试。
- `order_callback`/`deal_callback` 可确认委托与成交回报。

### 注意事项

- QMT 客户端必须保持登录在线（无人值守最大风险点，建议后续加保活守护）。
- 策略持仓变动后需同步 HOLDINGS：优先实现每次 `handlebar` 读本地 `qmt_trade_log.csv` 自动刷新。
- 非交易时段 `query_stock_positions`/撤单可能不稳定（见 `QMT避坑指南.md`），策略内注意重试与兜底。

---

## [待办] v4 面板：接入资金流数据（补 F2/F5 真实数据缺口）

- 状态：**待实施**（2026-08-24 建立，用户要求把 v4 规划落盘）
- 背景：当前 F2（资金）用 `volume_ratio` 量比做离线代理、F5 用 `rel_mom_20` 相对动量做代理（见 `deploy_predict.py` 注释），无真实资金流字段。外部数据源提供 7 套 A 股资金流数据（通用口径 2007-01 至今 / 同花顺 2024-12 起 / 东财 2023-09 起 / 概念板块 / 行业板块 / 大盘），可弥补回测本地数据源不足。

### 数据源要点（外部供应商）

| 数据集 | 口径 | 时间范围 | 用途 |
|---|---|---|---|
| `moneyflow` | 通用 | 2007-01-04 至今 | **唯一能进长历史回测**，F2 个股资金 |
| `moneyflow_ths` | 同花顺 | 2024-12-24 起 | 短线/量化因子（近端） |
| `moneyflow_dc` | 东财 | 2023-09-11 起 | 多口径对比（近端） |
| `moneyflow_cnt_ths` | 同花顺概念 | 2024-09-10 起 | 板块轮动 |
| `moneyflow_ind_ths` | 同花顺行业 | 2024-09-10 起 | F5 行业资金 |
| `moneyflow_ind_dc` | 东财行业+概念 | 2023-09-12 起 | F5 双维度 |
| `moneyflow_mkt_dc` | 东财大盘 | 2023-04-17 起 | 大盘资金/择时 |

格式：CSV / Parquet / DuckDB 三选一，每周更新，字段规整。

### 接入成本评估

- **格式选 Parquet**：与现有 `stock_daily.parquet` / `feature_panel_v2.parquet` 一致，零新依赖；放 `E:/astock/moneyflow/`。
- **数据费用**：待确认（订阅制）。
- **开发**：写 `moneyflow_ingest.py` 归一化入库 + 字段核对脚本。
- **重训**：复用 R2 已跑通的全量重建流程（475 万行，研发电脑约 1 小时）。
- ⚠️ **最大风险：周更 vs 日更错位**。资金流每周更新，实盘当日/昨日资金流缺口无法由它补 → v4 离线资金流特征主要用于提升模型/回测；**盘中实时资金流仍靠 TDX**（`review_full.py` 的 `main_net_inflow`），两者定位不同、不可互相替代。

### 实施步骤（Phase A 核心优先）

1. **数据接入**：`moneyflow_ingest.py` 把 `moneyflow`（2007-今）归一化存入 `E:/astock/moneyflow/moneyflow.parquet`（`ts_code + trade_date + 主力/超大单/大单/中单/小单 净流入+占比+成交额`）。
2. **特征设计**（新增 6-8 个，替代量比代理）：
   - `mf_main_net`：当日主力净流入 ÷ 当日成交额（归一化防市值偏差）
   - `mf_main_net_5d` / `_20d`：主力净流入 5/20 日滚动和
   - `mf_super_ratio`：超大单净流入占比
   - `mf_main_trend`：主力净流入 5 日均 vs 20 日均（趋势方向）
   - `mf_inflow_days`：近 10 日主力净流入为正的天数
3. **建面板**：新写 `build_features_v3.py`（v2 基础上 + 上述特征，`merge_asof backward` 防未来函数），输出 `feature_panel_v4.parquet`。
4. **重训**：`train_optuna.py --panel v4`，研发电脑跑；对比 v3_enh（+0.056%）是否接近/超过判定线 +0.3%。
5. **评分卡 F2 升级**：`deploy_predict.py` 的 `_score_f2` 从量比代理 → 用 `mf_main_net` 真实资金流打分（F2 权重 0.20）。

### Phase B（可选）

`moneyflow_mkt_dc`（大盘，2023-04 起）→ 大盘资金风控二次确认：叠加现有沪深300涨跌幅两档（-1.5%/-1.0%），加"全市场主力净流出"信号，共振时更保守。

### Phase C（可选）

`moneyflow_ind_dc`/`moneyflow_ind_ths` → F5 真实板块资金：F5 从 `rel_mom_20` 相对动量升级为"所属行业当日主力净流入"打分，与 `review_full.py` 的行业涨幅 F5 互相印证。

### 验收标准

- `moneyflow.parquet` 入库行数/时间范围与供应商声明一致。
- v4 面板回测（open→open + 0.1% 滑点）优于 v3_enh（+0.056%）。
- F2 评分卡用真实资金流后，选股清单的 F2 列与 TDX 实时口径方向一致。

### 注意事项

- 先拿 `moneyflow` 通用口径样品核对字段/质量，再全量接入。
- 历史覆盖只有 `moneyflow`（2007-今）能进长回测；ths/dc 口径仅限近端验证。
- 周更数据的当日缺口由 TDX 实时通道补，v4 不替代盘中复核。

---

## [已关闭] 核对模拟盘交易费用与真实费率是否一致

- 状态：**已关闭**（2026-08-22，用户决策）
- 关闭原因：模拟盘只是验证流程，**收益结算统一按实盘交易数据（真实费率）**，无需与模拟盘费率对齐。

### 结算口径（已落地）
统一按实盘费率结算（用户 2026 实测）：佣金 万2 双边最低5元 / 印花税 万5 仅卖出 / 过户费 万0.1 仅沪市双边。已配置在 `qmt_config.py`（COMM_RATE/COMM_MIN/STAMP_RATE/TRANS_RATE），并应用到 `strategy_capital.py` 与 `scan_rotate_cost.py`。

---
