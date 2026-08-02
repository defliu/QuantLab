# QuantLab Agent 开发指南

本文件适用于 `E:\QuantLab` 下的全部文件。参与本项目开发时，必须优先遵守以下规则。

## 项目定位

- 本项目是 A 股量化研究与实盘框架，覆盖因子研究、回测验证、风控、miniQMT 实盘交易的完整链路。
- `data/` 是数据读取层，采用鸭子类型接口设计（astock parquet / DuckDB 双引擎），财务数据必须 PIT 安全。
- `factors/` 是因子库，因子基类提供 winsorize/zscore/rank_normalize 预处理，引擎负责注册、计算与 IC 评估。
- `strategy/` 是策略库（当前策略逻辑在各项目目录下），未来统一收归此处。
- `broker/` 是券商接口层，含 QMT 单文件策略生成器（`qmt_builder.py`），桥接模块化研究代码与 QMT 单文件需求。
- `projects/` 是策略项目隔离目录，每个策略独立回测、独立配置、独立结果。
- `specs/` 是需求与设计文档目录，可作为需求、设计和验收依据。
- 兄弟项目 `D:/QMT_STRATEGIES` 是实盘策略工程（评分买入 + 分层卖出风控 + QMT 单文件构建）；QuantLab 的 `Project_01\research\multi_factor_ic\` 是其 vendored 副本（自包含），`D:/QMT_STRATEGIES/strategy_mfic.py` 仍是 QMT 生产部署位。

## 核心模块认知

- 数据层统一接口：`data/feed.py` 中的 `DataFeed`，分发到 `astock_reader.py` 或 `duckdb_reader.py`。
- 财务数据 PIT 安全：`data/astock_finance_reader.py` 使用 `ann_date`/`f_ann_date` 防止前视偏差。
- 因子基类：`factors/base.py` 中的 `FactorBase`（ABC），因子引擎：`factors/engine.py` 中的 `FactorEngine`。
- 已实现因子：`factors/vwap_volume_corr.py`（GTJA191 因子：-1 * rank(corr(rank(VWAP), rank(volume), 5))）。
- 主力策略评分：`projects/Project_01_多因子IC小盘Alpha/strategy/scoring.py` 中的 `score()`，五因子加权（BP:27%、反转:22.5%、低波:22.5%、ROE:18%、VWAP:10%）。
- QMT 策略生成器：`broker/qmt_builder.py` 中的 `build_qmt_strategy()` 和 `save_strategy()`，输出 GBK 编码单文件。
- 本地验证适配器：`broker/local_context.py` 中的 `LocalContext`，把 C.* 调用映射到本地 xtdata，各项目的 `local_validate.py` import 使用。
- 实盘 Broker（文档级）：`MiniQMTBrokerPro`、`PaperTradingBroker`，含回调、重连、委托监控、状态持久化。
- 验证框架：`projects/verification/` 下 `engine_tests.py`（B-1~B-8）和 `robustness_tests.py`（D-1~D-7）。

## 策略项目状态

| 编号 | 名称 | 年化 | 夏普 | 回撤 | 状态 |
|---|---|---|---|---|---|
| 01 | 多因子IC小盘Alpha | 10.1%（全池真实口径） | 0.41 | -24.4% | **已审计（2026-08-01）：幸存者偏差修复后超额仅BP因子+6.1%/年，2024+转负，不建议实盘** |
| 10 | 价值小盘V2（微调版） | 全期超额16.1%/年（状态机口径17.9%，z=0.8/hp=0.2） | — | — | **已立项（2026-08-02）：行业中性BP+历史分位+质量排雷，审计基线+241.6%/17.9%；含风控回测15.1%/+174.2%；QMT单文件已构建；待预生成CSV管道+模拟盘验证** |
| 02 | 双均线趋势 | — | — | — | 回测中 |
| 03 | PEAD盈余漂移 | -7.8% | — | — | 不推荐 |
| 04 | ML多因子 | -21.4% | — | — | 已淘汰 |
| 05 | 红利低波 | 3.7% | — | -11.3% | 推荐（防御） |
| 06 | 质量小市值 | 4.1% | — | -20.1% | 可选 |
| 07 | 低换手反转 | -6.9% | — | — | 不推荐 |
| 08 | 指数增强 | 0.4% | — | -8.2% | 防御 |
| 09 | 组合策略 | — | — | — | 组合（70%红利+30%小盘） |

## 构建与产物

- QMT 策略构建入口：`broker/qmt_builder.py`。
- 产物为 GBK 编码单文件策略，含 `init(C)` → `handlebar(C)` → `exit(C)` 生命周期。
- 研究回测入口：`main.py`，默认运行 Project_01。
- 修改源模块后，应通过构建脚本重新生成产物，不要手工长期维护构建产物中的重复逻辑。

## QMT 红线

- 所有 QMT 运行产物必须是 GBK 编码，并且首行必须是 `# coding=gbk`。
- QMT 运行环境按 Python 3.6.8 兼容处理。
- 禁止在 QMT 运行产物或会被合并进产物的代码中使用 Python 3.6 不支持的语法，包括但不限于：
  - `dict[str, ...]`
  - `list[str]`
  - `str | None`
  - `:=`
  - `match/case`
  - f-string
- `passorder()` 是全局函数，不是 `C.passorder()`。
- 账号 ID 必须硬编码：`67014907`。
- `C.get_market_data_ex` 缺少财务字段，PE/PB/circ_mv 必须来自预生成 CSV。
- QMT Python 3.6.8 无 pyarrow，不能读 parquet，数据源用 CSV。
- `circ_mv` 单位是万元，30 亿 = 300000（万元单位）。
- 生产版不得混入 mock 或测试代码。
- 涉及真实下单、卖出重试、跌停暂缓队列的改动必须格外保守。

### 实盘执行红线（自 QMT_STRATEGIES 实盘教训迁移）

- `passorder()` 是异步接口，**正常返回 0/None，不是订单号**；拿真实订单号必须反查 `get_trade_detail_data(acct, 'STOCK', 'order')`。
- 委托后即时反查会撞 QMT 约 100ms 的 order_id 分配延迟 → 必须短轮询（`*_LOOKUP_RETRIES`/`INTERVAL`）等待，反查失败会静默断链：不登记 pending → 成交回写/撤单/市价重试全不触发。
- 订单反查过滤：remark 只能作候选优先级信号，**不能硬过滤**；硬条件只留 code/volume/status/direction/time 五条 AND，唯一候选即使 remark 空也返回。
- 判订单方向用 `m_strOptName`（含中文"买入"/"卖出"），不要用未实测的 `m_nDirection` 等字段。
- 卖出风控评估前必须同步账户全量持仓纳管（孤儿持仓 = 账户有票但持仓文件没记录 → 止损/止盈永不触发）。
- 策略时间必须用 QMT 行情时间（`C.get_current_time()`），**不能读 `datetime.now()`**（设备 CMOS 时钟可能错乱）；相对计时用 `time.time()` 不受影响。
- 收盘（15:00）后 handlebar 不再触发，>15:00 的收盘任务永不执行；"启动时执行一次"的任务放 init 末尾，两者用标志联动防重复。
- QMT 模拟端 `get_trade_detail_data` 只保留当日 deal/order，隔日查不到 → 每日导出 CSV 到 `D:/QMT_POOL/` 是刚需。
- QMT 内置 Python 不保证有第三方包（如 pyyaml），import 必须 try/except fallback；重装/换设备后必看 `XtClient_FormulaOutput_*.log` 确认初始化完成。
- 策略必须自包含：config 读取不得依赖 `__file__`，读不到要有完整 `_DEFAULT_CONFIG` fallback。
- 详细清单见 `全局复利与踩坑日志.md` 的 QMT 相关章节。

## 风控优先级

- 卖出风控优先级高于买入优化。
- 不得为了提高买入频率、评分命中率或回测收益，绕过风控底线。
- 个股止损 8%、组合最大回撤 15%、最长持有 60 天、单日最大换手 30%。
- 涉及清仓、减仓、禁入期、状态持久化、跌停暂缓的逻辑改动，需要补充或更新测试。

## 配置系统（三级级联）

1. **全局配置** `config/settings.yaml`：项目信息、数据源、回测参数、因子预处理、策略默认股票池、日志。
2. **实盘配置** `config/trading_config.yaml`：账号（67014907）、交易参数、风控阈值、委托管理、调度计划、通知。
3. **项目级配置** `projects/Project_XX_*/config/strategy.yaml`：项目独立参数。

## 文件通信

- 策略运行时主要通过 `D:/QMT_POOL/` 交换文件（与 QMT_STRATEGIES 共用）。
- 常见文件包括：
  - `selected.txt` / `QMTselected.txt`：外部股票池
  - `*_holdings*.txt`：持仓跟踪
  - `*_nav*.txt`：累计盈亏净值
  - `sector_heat.json`：板块热度预计算数据
  - `成交记录_*.txt`：交易记录
  - `*_sell_state_*.json`：卖出状态持久化
  - `strategy_log_*.txt`：每日策略执行日志
- 数据源路径：`E:/astock/`（daily/finance/basic parquet 文件）。
- QMT 路径：`E:\国金QMT交易端模拟`。
- 实盘状态持久化：`data/broker_state.json`。

## 数据层约定

- 所有数据读取器遵循鸭子类型 4 方法接口：`load_window()`、`trading_calendar()`、`coverage()`、`close()`。
- 财务数据必须 PIT 安全：使用 `ann_date`/`f_ann_date` 过滤，禁止使用未来财务数据。
- `filter_func` 模式：市值等过滤条件以 callable 传入评分函数，在函数内部应用（预过滤会降低收益）。
- 数据缓存：`data/cache/` 目录，过期时间 1 天。
- 主数据源 `E:/astock/` 是买断离线资产（2009 起、财务全量、PIT 字段齐）；`adj_factor` 是后复权因子，前复权价 = 原始价 × (adj_factor / 最新 adj_factor)。
- look-ahead 是回测第一杀手：复权 look-ahead、宇宙选择 look-ahead（静态池套全期）、撮合 look-ahead（盘中取当日 close）都击中过；"盘中 vs 尾盘对照差异 >30pp 且方向反转"是验证 look-ahead 的干净方法。

## 因子层约定

- 新因子必须继承 `FactorBase`，实现 `compute()` 方法。
- 因子预处理顺序：winsorize(1%,99%) → z-score → 方向控制。
- 因子注册到 `FactorEngine` 后可批量计算和 IC 评估。
- 因子权重修改前，需确认 SPEC、研究文档或用户明确要求。

## 策略项目约定

- 每个策略项目独立目录：`projects/Project_XX_名称/`。
- 标准结构：`strategy/`（模块化代码）、`config/`（参数）、`results/`（回测结果）、`specs/`（需求文档）。
- **QMT 构建物自包含**：每个项目的 `build/` 子目录存放 QMT 部署文件（GBK 单文件），不在项目间共享或拷贝到公共目录。开发流程：编辑源码 → `python build.py`（语法检查+GBK转码） → `build/` 下生成部署文件。
- 新策略项目必须经过回测验证（B 模块）和鲁棒性测试（D 模块）才能进入实盘候选。
- 不推荐的策略（负年化）保留记录但不删除，作为对照参考。

## 验证框架

- 引擎验证（`verification/engine_tests.py`，B-1~B-8）：已知结果校验、成本计算、涨跌停/停牌处理、资金约束、一致性、先卖后买、止损。
- 鲁棒性验证（`verification/robustness_tests.py`，D-1~D-7）：子样本、牛熊分割、压力测试、成本压力、容量、未来函数、幸存者偏差。
- 回测结果必须关注：年化收益、夏普比率、最大回撤、换手率、IC/ICIR。
- 新策略上线前必须通过 B 模块全部测试 + D 模块至少 D-1/D-2/D-6。

## 测试与验证

- 研究环境使用 Python 3.11，QMT 生产必须保持 Python 3.6.8 兼容。
- 优先运行与改动相关的 pytest 测试，再视情况运行更大范围测试。
- **本地 miniQMT 验证（改策略后必跑）**：改完策略逻辑后，用 `broker/local_context.py`（全局共用适配器）连本地 miniQMT 实时数据快速验证，10 秒出结果。流程：
  1. 各项目写 `local_validate.py`，import `broker.local_context` 的 `LocalContext`
  2. 用 miniqmt venv 执行：`C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe local_validate.py`
  3. 验证内容：选股管线通不通、候选数量、排序、fail-open 是否生效
  4. 不能本地验证的（真实下单/涨跌停/持仓管理）再上远程
- QMT 产物构建后，应验证：文件存在、GBK 编码、`# coding=gbk` 文件头、Python 3.6 语法、无 MOCK 残留、无长小数输出。
- MOCK 只测信号逻辑，**测不到 QMT 集成层 BUG**（时序/异步/字段/文件/生命周期，约 8 成 bug 在此）；改代码后除 MOCK+单测外，必须过模拟端验证（部署前跑 1 交易日核对关键日志行）。
- 大量/跨文件修复后必须跑测试并如实汇报失败；测试报告记录 warning 类型与来源分类。
- 任务完成必须明确汇报：完成状态、commit、验证结果、遗留尾巴，区分"本任务相关"与"无关历史遗留"。
- 连接测试：`test_connection.py`（外部 Python）、`test_connection_qmt.py`（QMT 内 Python）。

## 实盘调度

```
09:15  盘前准备：检查连接、下载数据、查询账户、撤销遗留委托
09:35  策略执行：计算因子 → 目标组合 → 先卖后买执行
10:05+ 风控巡检：每 30 分钟 — 止损(-8%)、回撤检查(-15%)
13:05  午后检查：验证持仓
14:50  日终处理：对账、保存状态、发送日报
```

## 与 QMT_STRATEGIES 的关系

- `D:/QMT_STRATEGIES` 是兄弟项目，侧重实盘策略构建与分层卖出风控。
- **QMT 部署文件不再拷贝到公共目录**：每个项目的 `build/` 子目录自包含 QMT 部署文件（GBK 单文件），QMT 直接从项目目录加载。
- 两个项目共享 `D:/QMT_POOL/` 文件交换目录（预生成 CSV 数据）。
- QMT_STRATEGIES 的 `SellStrategyEngine` 是核心风控模块，如需复用应通过明确接口调用，不得绕过。
- 修改 QMT_STRATEGIES 中的模块前，需遵守其 AGENTS.md 规则。

## 开发原则

- 优先修根因，避免只改构建产物或做表面补丁。
- 保持最小改动，不顺手重构无关模块。
- 修改策略参数、评分权重、风控阈值前，先确认 SPEC、研究文档或用户明确要求。
- 新因子/新策略必须有 IC 分析和回测验证，不能凭直觉上线。
- 数据 PIT 安全不可妥协，任何涉及财务数据的改动必须验证无前视偏差。
- 如需编辑 QMT 生产产物，必须注意编码；默认应改源文件并重新构建。
- 空模块占位（`strategy/`、`backtest/`、`risk/`、`optimization/`、`dashboard/`、`sentiment/`）为规划中功能，填充前先确认架构设计。

## DE（deveco）委派规范

- **DE = DevEco Code CLI**（本机 `D:\Program Files\npm-global\deveco.cmd`），模型 `deveco/glm-5`，华为 oauth 免费额度（历史 12M tokens 费用 $0）。与 opencode 同架构，工具完整（bash/read/write/edit/task/glob）。
- **用途**: 耗 TOKEN 的重活交给 DE（通读大文件做总结、写分析报告、独立跑脚本、批量重写），opencode 只做审核与落盘。
- **调用**: `deveco run "<任务>" --dangerously-skip-permissions --dir E:\QuantLab --format json`；长会话用 `deveco serve` + `--attach`。
- **委派必须写明**: ①允许读取的文件清单 ②输出文件路径 ③禁止修改任何其他文件/禁止 commit/禁止修改类命令。
- **DE 产物必须审核**: DE 结论不保证正确（曾把日期标记 bug 的伪差异归因于持仓延续），opencode 需用原始数据独立验证后再落盘，发现错误要纠正 DE 并重写报告。
- **教训**: DE 报告文本引用旧文档时可能把过时结论当结论输出（audit13 报告直接复述了被证伪的归因），审核时以原始数据为准。
- **【强制】DE 工作必须全程盯梢，不得派完就等**: 这是基本流程。派任务后立即确认进程在跑（`deveco.exe` 存活 + 有无审计子进程），并主动向诚哥报告"DE 在跑/空闲"。长时间任务用轮询盯盘（PowerShell 循环查输出文件落盘 + DE CPU 是否增长）。判定卡死的信号：输出文件长时间不落盘 + CPU 墙钟利用率 <5%（如 11 分钟只涨 ~11s CPU）。一旦 2 次轮询确认卡死 → 立即 KillingDE 进程重派，并改用两步委派（第一步只写脚本贴 diff 审核，通过后第二步只运行），不要等 DE 自己超时。DE 进程 `deveco.exe` 从启动 PID 稳定；若 DE 假跑（时间戳变了内容没变）要识破。
