# Project_16 通宵研究结果（2026-08-24 凌晨）— 真实资金流面板 v3.1

> 目的：用 a-stock-data skill 拉真实资金流/板块数据，构建增强面板 v3.1，重训 + 可执行口径回测，
> 对比能否超过当前最优 **v3_enh（可执行 0.1% 滑点 N=5 = +0.056%）**。
>
> **红线遵守**：全程未改动任何 V1.1 资产（`D:/QuantLab/models/lgb_model_v3.txt`、`deploy_predict.py`、
> `qmt_config.py`、`run_scheduled.ps1` 均只读未写）。所有新产物以 `*_v3.1`/`*_real` 后缀写入 `data/real/` 或新文件名。

---

## 一、结论速览（TL;DR）

**真实资金流特征（新浪，F2 补齐）显著提升模型排序 IC，但没有转化为可执行口径超额，反而拖累可执行表现。**

| 版本 | 特征数 | 训练标签 | test IC | ICIR | 可执行 N=5 0.1% 超额 | 可执行 N=5 胜率 |
|---|---|---|---|---|---|---|
| **v3_enh（当前最优基线）** | 33 | close→close | 0.0425 | 0.329 | **+0.056%** | 55.1% |
| **v3.1**（+4 真实资金流特征） | 37 | close→close | 0.0527 | 0.475 | **-0.222%** | 45.7% |
| **v3.1b**（+4 真实资金流特征） | 37 | **open→open** | 0.760* | 8.50* | **-0.086%**（N=10: +0.109%） | 48.4% |

\* v3.1b 的 IC 为 open→open 标签下被市场共同因子主导的「伪 IC」（原口径胜率≈99% 为共同因子假象），非真实截面选股 IC，仅作诊断。

**结论：v3.1 未超过 v3_enh。** 真实资金流作为模型特征，在标准 close→close 训练下，
模型倾向选「次日高开跳空」的强势股（隔夜跳空均值 +1.93% vs v3_enh 的 +0.55%），
可执行口径（开盘进场）吃不到跳空反而买在局部高点，导致可执行超额由正转负。
把标签改成 open→open（v3.1b）让模型去追市场共同因子，仍未获得干净的可执行截面超额。

---

## 二、数据能力实测（网络允许范围内）

| 数据 | 状态 | 说明 |
|---|---|---|
| ✅ **新浪资金流备胎 `fund_flow_backup`** | **可用** | 实测 `num=3000` 可回退到 2013 年，覆盖回测期 + 完整训练期（2020-2023）。曾触发 sina 限流（HTTP 456），停 5 分钟后恢复，降并发后稳定 |
| ✅ 东财 `eastmoney_fund_flow_minute` / `eastmoney_stock_news` | 可用 | 本次未用于面板（逐分钟/新闻为在线信号） |
| ⚠️ **东财 `board_fund_flow` / `industry_comparison`** | **持续被代理拦截** | 01:03/01:36/01:58 三次重试，全部 ProxyError（`push2.eastmoney.com`）。按任务约定改用**本地行业动量**（v3_enh 面板已有的 `industry_mom20` + `turnover_rank`）作为 F5 备胎 |
| ⚠️ 东财 push2his 日级资金流 | 被拦截 | 用新浪备胎替代（同上） |

> 关键说明：`board_fund_flow`/`industry_comparison` 只能返回**当前**板块快照（今日/5日/10日），
> 无法回填历史面板行。因此即使东财放行，它也不适用于本回测的历史面板构建；回测的 F5 板块信号
> 只能来自可回放的本地行业动量。已如实记录，未编造东财板块历史数据。

---

## 三、构建了什么面板 / 用了哪些真实数据

### 3.1 `feature_panel_v3.1.parquet`（主面板，4,750,508 行 × 41 列）
- **基础**：`feature_panel_v3_enh.parquet`（33 特征：27 量价 + F3 三列 + `ind_mom20` + `turnover_rank`，与 v3_enh 完全一致）
- **新增 4 个真实资金流特征**（新浪 `fund_flow_backup`，F2 真实主力净流入）：
  - `main_net`：1 日主力净流入（元）
  - `main_net_pct`：1 日主力净流入 / 当日成交额（归一化，剔除规模影响）
  - `main_net_ma5_pct`：5 日累计净流入 / 5 日累计成交额
  - `main_net_ma20_pct`：20 日累计净流入 / 20 日累计成交额
- **覆盖**：3353 只回测期模型 top-100 池并集股票，7,547,625 行，日期 2013-01 ~ 2026-08-21；
  面板中 `main_net` 系特征非空率 **88.5%**（该并集约占面板可交易股票 4303 只的 78%，且都是活跃票，行覆盖更高）
- 已修正：`amount` 为 E:/astock「千元」口径，归一化前已 ×1000 转元（避免净流入占比虚高 1000 倍）

### 3.2 `feature_panel_v3.1b.parquet`（诊断面板，4,750,508 行 × 41 列）
- 与 v3.1 同特征，但 **`label`/`fwd_ret` 改为可执行口径 open→open**：
  `fwd_ret = adj_open_{T+1}/adj_open_T - 1`（复权开盘价，剔除隔夜跳空）
- 用途：验证「把训练目标对齐到可执行口径后，资金流特征是否具备可执行价值」

### 3.3 数据文件
- `data/real/fund_flow_real.parquet`：7,547,625 行 × 5 列（date/ts_code/main_net/close/turnover），3353 只
- `data/real/candidate_pool_top100.json`（3353 只）/ `candidate_pool_top10.json`（1775 只）：回测期候选池并集

---

## 四、重训 IC（train_optuna.py，默认切分：train 2020-01~2023-06 / valid 2023-07~2024-06 / test 2024-07~2026-08，20 trials）

| 指标 | v3_enh | v3.1（close→close 标签） | v3.1b（open→open 标签） |
|---|---|---|---|
| 最优验证 IC | 0.0802 | **0.0857** | 0.7596* |
| test IC | 0.0425 | **0.0527** | 0.7596* |
| 日均 IC | 0.0439 | **0.0536** | 0.7064* |
| ICIR | 0.329 | **0.475** | 8.50* |
| best_iteration | 1373 | 2192 | 5526 |
| 分位收益 Q1→Q5 | 0.04%→0.22% | -0.02%→**0.30%** | -2.56%→3.28%* |

* v3.1b 的 IC 是 open→open 标签下被市场共同因子主导的伪高值（共同因子≈所有股票次日开盘同向运动），不代表截面选股能力。

**v3.1 的资金流特征被模型真正使用**：Top20 特征重要性中 `main_net_pct` 排第 11（3365）、
`main_net_ma5_pct` 排第 20（2273）；v3.1b 中 `main_net_pct` 升至第 3、`main_net_ma5_pct` 第 6、`main_net` 第 8。
真实资金流不是「死特征」。

---

## 五、可执行口径回测（scan_rotate_cost.py，测试期 2024-07-01 ~ 2026-08-14，open→open 开盘进出 + 一字板/停牌过滤 + 真实费率 + 滑点敏感性）

### 5.1 各滑点/持有期 日均超额 汇总（可执行 open→open）

**v3.1 vs v3_enh（同一 close→close 面板口径，超额可直接对比）：**

| 持有期 | 滑点 0% | 滑点 0.1% | 滑点 0.2% |
|---|---|---|---|
| N=1 | v3_enh -0.088% / **v3.1 -0.264%** | v3_enh -0.180% / **v3.1 -0.359%** | v3_enh -0.273% / **v3.1 -0.457%** |
| N=3 | v3_enh +0.005% / **v3.1 -0.193%** | v3_enh -0.043% / **v3.1 -0.242%** | v3_enh -0.091% / **v3.1 -0.291%** |
| **N=5** | v3_enh +0.089% / **v3.1 -0.187%** | **v3_enh +0.056% / v3.1 -0.222%** | v3_enh +0.022% / **v3.1 -0.258%** |
| N=10 | v3_enh +0.024% / **v3.1 -0.073%** | v3_enh +0.004% / **v3.1 -0.096%** | v3_enh -0.017% / **v3.1 -0.119%** |

→ **v3.1 可执行口径在全部 12 个（N×滑点）组合下都差于 v3_enh，且全部转负。**

**v3.1b（open→open 标签，诊断；超额基准为 open→open 市场均值，与 v3_enh 的 close→close 基准略有差异）：**

| 持有期 | 滑点 0% | 滑点 0.1% | 滑点 0.2% |
|---|---|---|---|
| N=1 | -0.234% | -0.334% | -0.436% |
| N=3 | -0.140% | -0.189% | -0.237% |
| N=5 | -0.051% | -0.086% | -0.120% |
| N=10 | +0.129% | **+0.109%** | +0.088% |

→ v3.1b 的 N=5 仍为负（-0.086%），未超过 v3_enh 最优 +0.056%；仅 N=10 转正（+0.109%），
但该配置非 v3_enh 最优，且其「原口径」胜率≈99% 表明模型在追市场共同因子而非截面选股。

### 5.2 原口径（close→close，仅对比，高估超额）

| 持有期 | v3_enh N=1/5 | v3.1 N=1/5 |
|---|---|---|
| N=1 0.1% | +0.251% | **+1.276%** |
| N=5 0.1% | -0.031% | **+0.287%** |

→ v3.1 的 close→close 原口径大幅改善（N=1 由 +0.25% → +1.28%），
印证「模型排序 IC 确实更强，但超额主要来自隔夜跳空，可执行口径吃不到」。

### 5.3 可执行口径恶化的根因（实证归因）

对两版模型每日被选 TOP2（前10池 + 红线≥58 + 可执行过滤）统计**次日开盘隔夜跳空（close→T+1 open）**：

| 模型 | 被选样本 | 隔夜跳空均值 | 中位数 |
|---|---|---|---|
| v3.1 | 975 | **+1.93%** | +0.36% |
| v3_enh | 944 | +0.55% | 0.00% |

- 资金流特征（`main_net_pct` 等）引导模型偏好「主力大幅净流入」的强势股，这类股次日普遍**高开跳空**；
- 可执行口径在 T+1 **开盘**买入，跳空已发生、无法捕获，且常在跳空高点进场后回落；
- 于是：排序 IC 提升（close→close 捕获跳空）↔ 可执行 open→open 超额转负（吃不到跳空）同步出现。

---

## 六、与 v3_enh 对比结论

1. **未超过。** v3.1（真实资金流特征 + close→close 训练）可执行 N=5/0.1% = **-0.222%**，明显差于 v3_enh 的 **+0.056%**；12 个（N×滑点）组合全部落败。
2. **资金流有真实信息但方向与可执行目标错位。** 它显著提升排序 IC（0.043→0.053）与 close→close 原口径（N=1 +0.25%→+1.28%），但信息集中在「隔夜跳空」成分，恰是可执行口径（开盘进场）无法变现、且进场成本最高的部分。
3. **对齐标签（v3.1b）不解决。** 改 open→open 标签后模型转去拟合市场共同因子（伪高 IC / 原口径胜率≈99%），N=5 仍为负，未获得干净的可执行截面超额。
4. **建议（后续可验证方向）**：
   - 资金流作为**排序分位/过滤信号**而非模型特征（例如剔除高 `main_net_pct` 的隔夜跳空高风险票）再入池；
   - 用「剔除跳空后的可执行收益」做标签 + 对共同因子做截面中性化（demean by date）后再训练；
   - 等待东财板块接口解封后补真实板块资金流（当前仅能回放本地行业动量）。

---

## 七、产物文件路径

**面板 / 元数据（data/ 下，`*_v3.1` 新文件名，未覆盖任何既有面板）**
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\feature_panel_v3.1.parquet`
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\features_v3.1.json`
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\feature_panel_v3.1b.parquet`
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\features_v3.1b.json`

**模型（D:/QuantLab/models/ 下，新文件，未覆盖 lgb_model_v3.txt）**
- `D:/QuantLab/models/lgb_model_v3_v3.1.txt`
- `D:/QuantLab/models/lgb_model_v3_v3.1b.txt`

**data/real/（本次通宵全部产物）**
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\real\fund_flow_real.parquet` — 新浪资金流 7.55M 行
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\real\candidate_pool_top100.json` / `candidate_pool_top10.json`
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\real\fund_flow_pull.log` / `fund_flow_done.json` — 拉取日志与断点
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\real\eastmoney_retry.log` / `eastmoney_retry_result.json` — 东财重试记录（均被拦截）
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\real\optuna_v3.1_report.json` / `optuna_v3.1b_report.json` — 训练报告
- `d:\trae_workspace\projects\Project_16_LightGBM股票大师\data\real\scan_rotate_cost_report_v3.1.md` / `scan_rotate_cost_report_v3.1b.md` / `scan_rotate_cost_report_v3enh_check.md` — 可执行回测报告（v3.1 / v3.1b / v3_enh 复核）

**复核**：`data\real\scan_rotate_cost_report_v3enh_check.md` 用当前代码重跑 v3_enh，N=5/0.1% = +0.056%，与既有基线完全一致，保证对比口径一致。

---

*仅供个人量化研究使用，不构成投资建议；市场有风险，决策需谨慎。*
