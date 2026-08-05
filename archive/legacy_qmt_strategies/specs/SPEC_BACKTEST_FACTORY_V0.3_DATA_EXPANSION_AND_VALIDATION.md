# SPEC_BACKTEST_FACTORY_V0.3_DATA_EXPANSION_AND_VALIDATION

日期：2026-06-14
作者：Hermes
执行方：CC
状态：v0.3 启动版 SPEC，先进入 CC writing-plans，不直接写交易逻辑
前置基线：`SPEC_BACKTEST_MVP_OFFLINE_FACTORY_v0.2.md` 已由 Hermes 复核为“工程 MVP 内部可用基线”

---

## 1. Objective

### 1.1 背景

回测工厂 v0.2 已完成工程 MVP：DuckDB 只读数据通道、strategy_core、DailyBacktestEngine、标准 6 文件输出、batch runner、真实数据 smoke 均已跑通。

但 v0.2 只能作为工程基线，不能作为策略业绩结论，原因：

```text
数据样本短：金策智算 DuckDB 当前只到 2026-02-27
真实 smoke 窗口仅 67 个交易日
universe 仅 10 只大盘蓝筹
benchmark 缺失
sector_heat 为 zero 近似
```

因此 v0.3 的目标不是重构工厂，而是围绕 v0.2 基线做：

```text
P0 数据补齐
→ P1 扩 universe
→ P2 真实业绩验证
→ P3 处理 OQ-1/OQ-2/OQ-D/OQ-E
```

`OQ-F target_volume 接口变更` 暂缓，不破坏 v0.2 已签字接口。

### 1.2 v0.3 目标

v0.3 需要回答：

1. 如何把数据补到可用于策略验证的时间范围？
2. 用更大 universe 后，6+2 当前策略表现如何？
3. 回测工厂能否稳定支撑更长样本、更大股票池、更多参数组？
4. 哪些 v0.2 open questions 需要在 v0.3 中解决？
5. 是否可以进入后续“策略研究”而不是仅工程 smoke？

### 1.3 v0.3 不做

v0.3 仍然不做：

1. 不接 QMT 实盘/模拟交易。
2. 不调用 `passorder`。
3. 不修改 `release/v1.0`。
4. 不修改生产 `strategy_main.py`。
5. 不把回测结果当作实盘上线依据。
6. 不改变 `target_volume=0 由 engine 折算` 的已签字接口。
7. 不把 IMA 主升浪并入 6+2 主线。
8. 不写入 `F:\金策智算\` 任何文件。

---

## 2. v0.3 关键决策

### 2.1 OQ-1：项目自管 DuckDB 路径

v0.2 Future Work 中曾写：

```text
D:\QMT_STRATEGIES\data\duckdb\qmt_market_data.duckdb
```

但 v0.2 已明确：D 盘仅放代码/配置/SPEC/测试小产物，大产物落 F 盘。

v0.3 决策：项目自管 DuckDB 应落 F 盘。

推荐路径：

```text
F:\backtest_workspace\data\duckdb\qmt_market_data.duckdb
```

目录：

```text
F:\backtest_workspace\data\duckdb\
F:\backtest_workspace\data\sync_reports\
```

### 2.2 OQ-2：data backend/source 命名统一

v0.3 起 summary/config 使用两层字段：

```json
"data_backend": "duckdb",
"data_source": "jince_zhisuan" | "qmt_self_owned" | "merged"
```

含义：

| 字段 | 示例 | 说明 |
|---|---|---|
| data_backend | duckdb | 技术存储后端 |
| data_source | jince_zhisuan | 金策智算库 |
| data_source | qmt_self_owned | xtquant 同步到项目自有库 |
| data_source | merged | 多源合并视图 |

v0.3 应保持兼容 v0.2 的 `data_source`，但新增 `data_backend`。

### 2.3 OQ-D：涨停阈值分板块

v0.2 使用统一：

```text
LIMIT_UP_PCT = 9.95
```

v0.3 需要至少设计并测试板块区分：

| 类型 | 代码特征 | 涨跌停近似 |
|---|---|---|
| 主板 | 600/601/603/605/000/001/002 | 9.95% |
| 创业板 | 300 | 19.9% |
| 科创板 | 688 | 19.9% |
| 北交所 | 8xx / 4xx / 9xx（如存在） | 29.9% |
| ST | 名称含 ST/*ST 时 | 4.95% |

若当前 DuckDB 无名称/ST 字段，则 ST 分流先记录为不可用，不阻塞。

### 2.4 OQ-E：sector_heat historical/realtime

v0.2：

```text
sector_heat_mode = zero
```

v0.3 不强制实现 historical sector_heat，但需要明确：

1. 是否已有可用历史板块热度数据。
2. 若没有，继续 `zero`，并在 report 中保留近似警告。
3. 可选实现 `static` 模式：指定一个静态 sector_heat 文件做敏感性实验。
4. `historical` / `realtime` 留到 v0.4，除非现有数据源已经具备。

### 2.5 OQ-F：target_volume 接口变更暂缓

v0.3 不改：

```text
strategy_core 输出 target_volume=0
engine 根据 next_open/close 成交价折算股数
```

原因：该接口已签字并通过 v0.2 测试。v0.3 以数据与验证为主，不做破坏性接口调整。

---

## 3. P0 数据补齐

### 3.1 数据补齐优先级

优先级：

1. 先尝试让金策智算自身 `history_sync` 更新现有 DuckDB 到最新交易日。
2. 若无法补齐，才做 `xtquant/MiniQMT → 项目自管 DuckDB`。
3. 不直接写金策智算内部库。

### 3.2 P0-A：金策智算同步验证

目标：判断 `F:\金策智算\_internal\databases\duckdb\quantifydata.duckdb` 是否可通过金策智算自身流程补到最新。

CC 允许做：

```text
只读检查，不触发 GUI，不杀进程，不写该目录。
```

诚哥需要手动做：

```text
打开金策智算 / 触发 history_sync / 确认同步完成
```

CC/Hermes 可读验证：

```bash
py -3.10 -S -c "... validate_data ..."
```

输出：

```text
D:\QMT_STRATEGIES\agent_hub\2026-06-14_backtest_v03\01_data_sync_status.md
```

内容：

1. 当前 DuckDB max_date。
2. 当前 n_codes / n_rows。
3. 是否存在 `.wal`。
4. 与 v0.2 基线相比是否更新。
5. 是否足以进入 P1/P2。

### 3.3 P0-B：xtquant → 项目自管 DuckDB 设计

仅当 P0-A 无法满足数据补齐时启动。

目标脚本：

```text
backtest/data_tools/sync_xtquant_to_duckdb.py
```

目标库：

```text
F:\backtest_workspace\data\duckdb\qmt_market_data.duckdb
```

输入：

```text
universe
start_date
end_date
period=1d
adjustment=hfq
```

输出：

```text
F:\backtest_workspace\data\sync_reports\sync_report_<sync_id>.json
```

强制边界：

1. 只写项目自管 DuckDB。
2. 不写金策智算库。
3. 回测过程中不自动补数。
4. 必须先 sync → validate → backtest。
5. 单只失败不中断批量。
6. 输出 failed_codes。
7. 记录 xtquant/MiniQMT 状态。

### 3.4 P0 通过标准

P0 通过条件至少满足一项：

A. 金策智算 DuckDB 已补齐到最新交易日附近，且覆盖足够策略验证；

或：

B. 项目自管 DuckDB 已建成并包含可用于 12 个月以上验证的数据。

---

## 4. P1 扩 universe

### 4.1 universe 分层

v0.3 需要定义多级 universe：

```text
smoke_10.csv        # 当前 10 只，保留用于快速测试
core_100.csv        # 100 只核心池，用于快速策略验证
broad_500.csv       # 500 只，用于中等规模验证
all_available.csv   # DuckDB 当前有数据的全体股票，用于压力/全量研究
```

路径：

```text
D:\QMT_STRATEGIES\backtest\data\universe\
```

### 4.2 universe 来源

首选：从 DuckDB 当前有日线数据的 code 列表生成。

可选筛选：

1. 剔除历史不足 120 日。
2. 剔除价格/成交额异常。
3. 可选按成交额排序取 top N。
4. 若无 ST/上市日期字段，不强行过滤，记录限制。

### 4.3 universe 验收

每个 universe 必须有 validate report：

```text
F:\backtest_workspace\logs\validate_universe_<name>_<timestamp>.json
```

报告需包含：

1. universe_size。
2. codes_with_data。
3. missing_count。
4. min/max date。
5. bars_per_code 分布。
6. 行业分布（若 sector 字段可用）。

---

## 5. P2 真实业绩验证

### 5.1 验证目标

P2 才开始看策略表现。目标是用更长数据、更大 universe 验证 6+2 策略。

### 5.2 最小验证矩阵

若数据可用，至少跑：

| universe | 时间 | 目的 |
|---|---|---|
| smoke_10 | 当前 smoke 区间 | 回归验证 |
| core_100 | 12 个月 | 快速业绩初评 |
| broad_500 | 12 个月 | 稳定性验证 |
| all_available | 12 个月或最大可用 | 压力/全量验证 |

参数矩阵：先沿用 v0.2 baseline，不扩大 grid。

### 5.3 输出

每组输出：

```text
summary.json
report.md
trades.csv
equity_curve.csv
positions.csv
logs.txt
```

并生成：

```text
D:\QMT_STRATEGIES\agent_hub\2026-06-14_backtest_v03\03_real_performance_summary.md
```

必须回答：

1. 总收益、年化、最大回撤、Sharpe/Calmar。
2. trade_count 是否足够。
3. 是否高度依赖短样本/少数股票。
4. 是否值得进入策略调参。

---

## 6. Commands

### 6.1 数据状态检查

```bash
py -3.10 -S -c "import sys; sys.path.append(r'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\Lib\site-packages'); import runpy; runpy.run_module('backtest.scripts.validate_data', run_name='__main__')"
```

### 6.2 universe 验证

```bash
py -3.10 -S -c "import sys,runpy; sys.path.append(r'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\Lib\site-packages'); sys.argv=['validate_universe','--universe','backtest/data/universe/core_100.csv','--start-date','YYYY-MM-DD','--end-date','YYYY-MM-DD']; runpy.run_module('backtest.scripts.validate_universe', run_name='__main__')"
```

### 6.3 单次回测

```bash
py -3.10 -S -c "import sys,runpy; sys.path.append(r'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\Lib\site-packages'); sys.argv=['run_backtest','--config','backtest/configs/v03_core100.yaml']; runpy.run_module('backtest.scripts.run_backtest', run_name='__main__')"
```

### 6.4 batch

```bash
py -3.10 -S -c "import sys,runpy; sys.path.append(r'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\Lib\site-packages'); sys.argv=['run_batch','--experiment','backtest/configs/experiments/v03_validation_grid.yaml']; runpy.run_module('backtest.scripts.run_batch', run_name='__main__')"
```

---

## 7. Boundaries

### 7.1 仍然禁止

1. 不接 QMT 实盘/模拟交易。
2. 不调用 passorder。
3. 不修改 release/v1.0。
4. 不修改生产 strategy_main.py。
5. 不写金策智算内部库。
6. 不把大产物写 D/C 盘。
7. 不改 target_volume 接口。
8. 不把 IMA 并入 6+2。

### 7.2 允许

1. 新建 v0.3 configs。
2. 新建 universe CSV。
3. 新建 v0.3 agent_hub 报告。
4. 如 P0-B 获批，可新建 xtquant sync 脚本和项目自管 DuckDB。
5. 读取金策智算 DuckDB。
6. 写 F 盘 workspace。

---

## 8. Phase Plan

### Phase 0：CC writing-plans

输出：

```text
D:\QMT_STRATEGIES\agent_hub\2026-06-14_backtest_v03\00_cc_v03_plan.md
```

必须先确认：

1. P0-A 是否足够。
2. 是否需要 P0-B。
3. universe 生成方案。
4. P2 验证矩阵。

### Phase 1：P0 数据状态 / 补齐路线

输出：

```text
01_data_sync_status.md
```

### Phase 2：P1 universe 扩展

输出：

```text
02_universe_expansion_report.md
```

### Phase 3：P2 真实业绩验证

输出：

```text
03_real_performance_summary.md
```

### Phase 4：P3 OQ 决策建议

输出：

```text
04_v03_open_questions_recommendation.md
```

---

## 9. Acceptance Criteria

v0.3 启动阶段通过标准：

1. 数据状态清楚：当前 max_date、coverage、是否需补数。
2. universe 分层清楚：10/100/500/all_available。
3. 至少 core_100 可以跑通。
4. 至少一个 12 个月级别验证可跑，若数据不足则明确阻塞。
5. 所有结果只落 F 盘。
6. 无交易接口调用。
7. 输出可供 Hermes 判断是否进入策略调参。

---

## 10. 给 CC 的执行说明

本 SPEC 只启动 v0.3，不要求立即实现完整 xtquant 补数链路。

优先顺序：

```text
先看金策智算 DuckDB 能否补齐
再决定是否做 xtquant → 项目自有 DuckDB
先扩 universe
再跑真实业绩验证
最后处理 OQ
```

CC 执行时必须遵循 ODM：

```text
review → plan → build → test → report
```

先产出 writing plan，等诚哥/Hermes 确认后再做高风险数据同步实现。
