# SPEC: 算力产业池主升浪 P0 工程化验证与每日观察版

## ⚙️ 开发引擎激活指令

在开始执行以下任务前，你必须：

1. 已加载项目级与全局 CLAUDE.md 中的 ODM 工作流与 QMT 红线；
2. 自动判断任务复杂度，本任务属于“大任务/研究工程化任务”；
3. 按 ODM 流程执行：review → build → test → validate → report；
4. 每个阶段输出 checkpoint；
5. 不接交易、不下单、不修改现有实盘/模拟盘策略源码；
6. 所有统计结论必须区分：已验证事实 / 合理假设 / 待回测验证 / 数据不足。

---

## Objective

### 目标

将 P0 证据专项中已经通过红队二审的《算力产业池主升浪成功/失败样本定义协议 V1》工程化为一个可重复运行的研究验证与每日观察系统。

核心产物不是交易策略，而是：

1. 可复现 RS 的 32 只算力代表池内部统计；
2. 可扩展到全市场对照组的 P0 统计验证；
3. 可每日输出算力池主升浪候选排名观察榜；
4. 为后续 Hermes / DeepSeek Pro / Doubao CIO 判断是否进入策略开发提供证据。

### 背景

已完成的关键文件：

|文件|作用|
|---|---|
|`D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\09_market_review_2024_to_now_data_verified.md`|32 只算力代表股 2024-2026 数据核验|
|`D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\12_main_uptrend_sample_definition_protocol_v1.md`|主升浪成功/失败样本定义协议 V1|
|`D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\13_protocol_v1_redteam_review.md`|红队二审，CONDITIONAL_APPROVE|
|`D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\14_rs_statistical_validation_report.md`|RS 算力代表池内部统计|
|`D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\15_hermes_hypothesis_decision.md`|Hermes 汇总决策|

### 当前证据

RS 内部验证结果：

|指标|结果|
|---|---:|
|总触发事件|829 次|
|有效触发事件|827 次|
|P(成功 \| 算力池+突破)|32.2%|
|光模块/CPO成功率|44.2%|
|PCB/铜连接成功率|35.3%|
|AI服务器/整机成功率|33.7%|
|液冷/温控成功率|32.3%|
|IDC/算力租赁成功率|17.0%|

但注意：这只是 32 只代表股内部估计，缺全市场对照组，不能证明“算力池过滤有效”。

### 验收目标

本任务完成后，应能回答：

```text
P(主升浪成功 | 算力池 + 突破)
是否显著高于
P(主升浪成功 | 全市场 + 突破)
```

并且输出：

1. 算力代表池复现报告；
2. 全市场对照验证报告；
3. 子方向分层统计；
4. 每日观察版候选榜单；
5. 数据不足/偏差风险说明。

---

## Commands

### 推荐运行环境

在 Windows Hermes / 项目工作区下执行，不进入 QMT 客户端，不加载交易策略。

建议工作目录：

```bash
D:/QMT_STRATEGIES
```

### 依赖检查

```bash
python -c "import akshare, pandas, requests, mootdx, stockstats; print('deps ok')"
```

已知当前环境：

|依赖|状态|
|---|---|---|
|akshare|已安装，1.18.64|
|mootdx|已安装，0.11.7|
|pandas|已安装，3.0.3|
|requests|已安装，2.33.0|
|stockstats|已安装|

### 建议命令形态

以下命令为目标形态，具体文件名可由你根据项目结构调整，但必须在交付报告中列出实际执行命令。

```bash
python research/compute_power_main_uptrend/validate_32_pool.py
python research/compute_power_main_uptrend/build_full_market_control.py
python research/compute_power_main_uptrend/run_p0_statistical_test.py
python research/compute_power_main_uptrend/generate_daily_observation.py
python research/compute_power_main_uptrend/run_all.py
```

### 测试命令

```bash
python -m pytest tests/research/compute_power_main_uptrend -v
```

如果项目当前没有对应测试目录，请创建最小测试集，覆盖：

1. 前复权 qfq 数据读取；
2. T+1 开盘价作为收益起点；
3. T+1 一字涨停标记 unexecutable；
4. 标签优先级互斥；
5. 主板/创业板/科创板涨跌停限制识别；
6. 冷却期过滤；
7. 全市场对照组和实验组使用相同过滤规则。

### 输出目录

所有新产物放在：

```text
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\cc_p0_validation\
```

建议输出：

```text
cc_p0_validation/
  data/
    compute_pool_32_daily_qfq.parquet or csv
    full_market_daily_qfq.parquet or csv
    trigger_events_compute_pool.csv
    trigger_events_full_market.csv
  reports/
    01_reproduce_rs_32_pool.md
    02_full_market_control_validation.md
    03_subsector_breakdown.md
    04_daily_observation_latest.md
    05_bias_and_data_quality_report.md
  outputs/
    daily_observation_rank.csv
    p0_statistical_summary.json
```

---

## Structure

### 输入资料

必须读取并遵守：

```text
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\12_main_uptrend_sample_definition_protocol_v1.md
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\13_protocol_v1_redteam_review.md
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\14_rs_statistical_validation_report.md
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\15_hermes_hypothesis_decision.md
```

可复用数据：

```text
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\tmp_data\*.csv
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\tmp_data\merged_report.json
D:\QMT_STRATEGIES\agent_hub\compute_power_main_uptrend_pool\tmp_data\rs_validation_result.json
```

### 建议模块

```text
research/compute_power_main_uptrend/
  __init__.py
  config.py                       # 参数配置，不硬编码阈值
  data_loader.py                  # akshare/mootdx 数据读取与缓存
  universe.py                     # 算力池、全市场对照组、过滤条件
  breakout_detector.py            # detect_breakout()
  sample_classifier.py            # classify_sample()
  validation_runner.py            # 32池复现 + 全市场对照
  statistical_tests.py            # chi-square/Fisher/effect size/CI
  daily_observation.py            # 每日观察榜
  report_writer.py                # markdown/csv/json 输出
```

### 核心函数要求

#### detect_breakout

必须基于 `12_main_uptrend_sample_definition_protocol_v1.md` 第 2 节实现。

硬要求：

- 使用前复权 qfq；
- 触发条件只用 T 日及之前数据；
- 类型 A 新高突破；
- 类型 B 放量平台突破；
- 类型 C 默认关闭；
- 不可成交/一字板/停牌/ST/新股过滤；
- 主板 10%、创业板/科创板 20%、北交所 30% 涨跌停限制区分；
- 所有阈值从配置读取，不硬编码。

#### classify_sample

必须基于 `12_main_uptrend_sample_definition_protocol_v1.md` 第 3 节实现。

硬要求：

- 收益、回撤、成功判定从 T+1 开盘价开始；
- T+1 一字涨停不可买入 → `unexecutable`；
- 标签互斥；
- 优先级按 V1 协议：

```text
unexecutable
→ success_accelerating_main_uptrend
→ success_trend_main_uptrend
→ pulse_failed
→ false_breakout
→ high_volatility_uncertain
→ non_signal
```

#### statistical_tests

至少实现：

- 算力池成功率；
- 全市场对照成功率；
- 效应量 = 二者差值；
- 95% 置信区间；
- 卡方或 Fisher；
- 子方向分层；
- 年度分层；
- 触发类型分层；
- 多重检验校正；
- 滑点 0.1% / 0.3% / 0.5% / 1.0% 敏感性。

---

## Code Style

### 总原则

- 本任务是研究工程，不是 QMT 交易策略；优先 UTF-8 文件；不要生成 QMT 运行文件。
- 不修改现有 `strategy_main.py`、`strategy_allday.py`、`release/`、QMT 生产/模拟策略文件。
- 不接 `passorder`，不调用交易接口。
- 不复制其他版本策略代码。
- 数据与报告可存 D 盘，不放 C 盘。

### 参数管理

所有 V1 协议阈值必须集中配置，例如：

```text
breakout_window_short = 20
breakout_window_long = 60
cooldown_days = 20
event_cluster_gap = 40
success_window = 40
acceleration_window = 60
min_effect_size_pp = 10
```

不要把阈值散落在函数内部。

### 数据口径

- 日线统一 qfq 前复权；
- 记录数据源版本和抓取时间；
- 如 akshare 失败，允许 mootdx 或腾讯/东财兜底，但报告必须注明；
- 全市场对照组和算力实验组必须使用完全相同的数据口径和过滤规则。

### 结论表述

报告中必须区分：

- 已验证事实；
- 合理假设；
- 待回测验证；
- 数据不足。

禁止写“策略已成功”“可实盘”“可下单”等结论。

---

## Testing Strategy

### 必测场景

1. `T+1 开盘价收益起点`
   - 构造一个 T 日突破、T+1 跳空高开的样本；
   - 验证收益起点不是 T 日收盘价。

2. `T+1 一字涨停不可成交`
   - T+1 开盘=收盘=涨停，低换手；
   - 标签应为 `unexecutable` 或不可成交状态，不应计入成功。

3. `标签互斥`
   - 同一触发事件只能有一个最终标签。

4. `uncertain 兜底`
   - success / pulse / false 都不满足时，才进入 uncertain / non_signal。

5. `涨跌停限制识别`
   - 600/000 主板 10%；
   - 300/688 创业板/科创板 20%；
   - 北交所 30%。

6. `冷却期过滤`
   - 同一股票 20 日内多次触发，只保留规则允许的事件。

7. `对照组过滤一致`
   - 算力池与全市场对照组使用同一停牌/ST/流动性/一字板规则。

8. `RS 结果复现`
   - 对 32 只代表池跑出与 RS 报告同方向结果；数值允许因冷却期/实现细节发生差异，但必须解释差异。

### 验收报告必须包含

```text
- 测试命令
- 测试结果
- 32池复现结果与RS结果差异说明
- 全市场对照组样本数
- 算力池成功率
- 全市场成功率
- 效应量
- p值/校正p值
- 子方向分层
- 数据缺口
- 是否建议进入下一阶段
```

---

## Boundaries

### Always

- 使用 `12_main_uptrend_sample_definition_protocol_v1.md` 作为定义源；
- 使用 qfq 前复权作为主口径；
- 使用 T+1 开盘价作为收益/回撤起点；
- 所有阈值配置化；
- 实验组和对照组过滤规则一致；
- 输出报告必须明确局限性；
- 所有产物放到 D 盘项目目录；
- 交付前运行测试；
- 汇报实际执行命令和真实输出。

### Ask First

- 新增大型依赖；
- 需要下载全市场多年全量数据且耗时很长；
- 需要使用付费数据源或登录凭证；
- 需要修改现有 QMT 策略文件；
- 需要把观察版接入定时任务或飞书推送；
- 需要写入 C 盘或系统目录。

### Never

- 不接 QMT 下单；
- 不调用 `passorder`；
- 不修改生产策略；
- 不生成可交易策略文件；
- 不把观察版包装成实盘策略；
- 不用今日算力名单直接回测历史并宣称有效；
- 不伪造全市场对照结论；
- 不跳过涨跌停/T+1/ST/停牌约束；
- 不删除已有研究产物；
- 不提交密钥或凭证。

---

## 交付判定

### APPROVED 条件

满足以下条件才算完成：

1. 32 只代表池统计可复现；
2. 全市场对照组统计完成，或明确说明数据源阻塞并给出替代方案；
3. 生成每日观察版候选榜单；
4. 测试通过；
5. 报告完整标注局限性；
6. 不涉及任何交易执行。

### REJECT 条件

任一触发即退回：

1. 使用 T 日收盘价计算收益；
2. 使用后复权作为主突破检测；
3. 跳过 T+1 / 涨跌停 / ST / 停牌约束；
4. 全市场对照组和算力池过滤规则不同；
5. 把 32 只代表池内部结果当成最终证明；
6. 修改现有 QMT 交易策略或接入下单。

---

## 给 CC 的最后提醒

这是研究验证工程，不是交易系统。

你的任务是把“这套定义是否真的有效”验证清楚，而不是证明它一定有效。

如果全市场对照结果显示算力池过滤没有显著增益，应如实报告失败。

失败也是有效交付。
