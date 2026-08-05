# 离线回测工厂初研报告

日期：2026-06-13
作者：Hermes
用途：给 MimoCode / CC / RS 共同讨论，决定是否建设离线回测 MVP

---

## 一、背景

当前策略优化和回测流程存在明显效率问题：

- MimoCode 做一次策略优化/回测讨论，耗时约 30 分钟甚至更久。
- 主要耗时不一定来自回测计算本身，而是来自 Agent 每次重复做以下事情：
  - 重新理解项目结构
  - 重新寻找回测入口
  - 临时确认数据位置
  - 临时组织参数
  - 临时读日志/总结结果
  - 出错后再修正执行路径
- 多 Agent 体系已经逐步成型：
  - Hermes：策略讨论、SPEC、调度、验收
  - CC：主力工程执行
  - MimoCode：主动优化建议、策略启发、任务复盘
  - RS：低成本、长上下文、批处理、结果汇总
  - Codex GUI：第二审查员/交互式代码理解

因此，需要把“每次由 Agent 临时跑回测”升级为“固定命令 + 标准数据 + 标准报告”的本地离线回测工厂。

核心目标：

> 让单次日线策略回测从 30 分钟级别，压缩到 3-5 分钟内完成计算、报告和初步解读。

---

## 二、问题诊断

### 1. 当前慢在哪里

| 慢点 | 说明 |
|---|---|
| Agent 重复理解项目 | 每次都要重新读代码、找入口、确认约束 |
| 回测入口不固定 | 没有一条稳定命令可以直接执行 |
| 数据层不标准 | 行情、股票池、参数可能散落在多个位置 |
| 结果格式不统一 | Agent 需要读日志猜结论 |
| 指标计算不标准 | 不同任务输出口径不一致 |
| 缺少批量实验层 | 多参数对比需要重复人工/Agent 调度 |

### 2. 关键判断

当前 30 分钟耗时中，很多是“认路成本”和“解释成本”，不是纯计算成本。

要提速，重点不是换一个更快的 Agent，而是把流程固化：

```text
固定数据入口
+ 固定策略适配层
+ 固定回测命令
+ 固定参数配置
+ 固定结果目录
+ 固定 summary.json/report.md
```

让 Agent 只做：

```text
改参数 → 跑命令 → 读 summary → 给建议
```

---

## 三、建设目标

建设一个本地离线回测 MVP，暂定名称：

```text
Offline Backtest Factory MVP
```

目标目录建议：

```text
D:\QMT_STRATEGIES\backtest\
```

第一阶段目标：

1. 支持日线级回测。
2. 支持固定股票池。
3. 支持统一 yaml/json 参数配置。
4. 支持一条命令运行 base 回测。
5. 支持批量运行多个实验配置。
6. 输出标准化结果目录。
7. 结果可被 RS 快速读取、排序、汇总。
8. 不直接接入 QMT 客户端。
9. 不触碰 release/v1.0 运营版。
10. 不涉及真实下单/模拟端交易。

---

## 四、系统分层设计

### 第 1 层：行情数据层

目标：本地缓存，避免每次调用 QMT/xtquant 拉取行情。

建议目录：

```text
D:\QMT_STRATEGIES\data\
  daily\
    000001.SZ.parquet
    600519.SH.parquet
  index\
    000300.SH.parquet
  universe\
    all_a_2024.csv
    strategy_pool_base.csv
```

第一版只做日线数据。

标准字段建议：

| 字段 | 说明 |
|---|---|
| date | 交易日 |
| open | 开盘价 |
| high | 最高价 |
| low | 最低价 |
| close | 收盘价 |
| volume | 成交量 |
| amount | 成交额 |
| adj_factor | 复权因子，可选 |
| is_st | ST 标记，可选 |
| suspended | 停牌标记，可选 |
| limit_up | 涨停价，可选 |
| limit_down | 跌停价，可选 |

MVP 可以先只要求：date/open/high/low/close/volume/amount。

---

### 第 2 层：策略适配层

目标：让策略核心逻辑能在非 QMT 环境下运行。

当前 QMT 策略通常依赖：

```text
init(C)
handlebar(C)
C.get_full_tick()
passorder()
get_trade_detail_data()
```

离线回测不应该直接依赖这些 QMT 对象。

存在两条路线：

#### 路线 A：抽取纯策略核心逻辑

把策略核心规则抽成纯函数/纯模块：

```text
输入：行情、持仓、配置
输出：买入候选、卖出信号、目标仓位
```

优点：

- 快
- 清晰
- 易测试
- 易被回测和实盘共同调用

缺点：

- 与当前 QMT 代码需要适配
- 初期可能存在实盘/回测逻辑偏差

#### 路线 B：模拟 QMT Context

实现 OfflineContext，模拟 QMT 的 C 对象和 passorder 行为。

优点：

- 更贴近现有 QMT 入口

缺点：

- 容易把 QMT 环境复杂性带入离线框架
- 更难调试
- MVP 成本高

### Hermes 初步建议

MVP 采用路线 A。

第一版可以先复制/抽取核心逻辑到 backtest 内部跑通，不急着重构 production。
等离线回测稳定后，再考虑让 QMT 实盘和离线回测共享 strategy_core。

---

### 第 3 层：回测引擎层

核心循环：

```text
加载配置
加载股票池
加载行情
初始化账户
for 每个交易日:
    更新当前行情
    执行卖出逻辑
    执行买入/换仓逻辑
    模拟成交
    更新持仓市值
    记录净值、持仓、交易、日志
输出结果
```

MVP 能力边界：

| 能力 | 第一版要求 |
|---|---|
| 频率 | 日线 |
| 撮合 | 当日收盘价或次日开盘价，需配置化 |
| 手续费 | 固定费率 |
| 滑点 | 固定比例 |
| T+1 | 必须支持 |
| 仓位 | 等权 N 只 |
| 股票池 | 固定 CSV |
| 调仓 | 支持固定调仓周期/操作点近似 |
| 风控 | 支持止损、淘汰、评分差换仓等核心规则 |
| 涨跌停/停牌 | 第一版可简化，第二版补强 |

明确不做：

| 不做 | 原因 |
|---|---|
| 分钟级真实撮合 | MVP 过重 |
| tick 回放 | 过重 |
| QMT 客户端内置回测 | 环境风险高 |
| 实盘/模拟下单 | 禁止 |
| 完全复刻 ContextInfo | MVP 不需要 |
| 修改 release/v1.0 | 禁止 |

---

### 第 4 层：参数实验层

目标：让 Mimo/RS 可以快速批量实验。

建议目录：

```text
D:\QMT_STRATEGIES\backtest\configs\
  base.yaml
  experiments\
    exp_001.yaml
    exp_002.yaml
    exp_003.yaml
```

配置项建议：

| 参数 | 示例 |
|---|---|
| start_date | 2024-01-01 |
| end_date | 2025-12-31 |
| initial_cash | 1000000 |
| universe_file | data/universe/strategy_pool_base.csv |
| max_positions | 5 |
| rebalance_frequency | daily/weekly/custom |
| rebalance_times | 0924,1000,1330,1430 |
| score_gap_threshold | 15 |
| min_5d_return | 0.03 |
| stop_loss | -0.08 |
| fee_rate | 0.0003 |
| slippage | 0.001 |
| execution_price | close/next_open |
| benchmark | 000300.SH |

目标命令形态：

```text
run_backtest base.yaml
run_batch configs/experiments/*.yaml
```

实际命令由 CC 在实现时确定，但必须做到“一条命令可复现”。

---

### 第 5 层：标准报告层

每次回测输出固定目录：

```text
D:\QMT_STRATEGIES\backtest\results\
  2026-06-13_153000_base\
    summary.json
    report.md
    equity_curve.csv
    trades.csv
    positions.csv
    daily_metrics.csv
    logs.txt
```

关键文件：

| 文件 | 用途 |
|---|---|
| summary.json | 给 RS/Mimo/Hermes 机器读取 |
| report.md | 给人看 |
| equity_curve.csv | 净值曲线 |
| trades.csv | 交易明细 |
| positions.csv | 每日持仓 |
| daily_metrics.csv | 每日指标 |
| logs.txt | 异常、警告、策略日志 |

summary.json 必备指标：

| 指标 | 说明 |
|---|---|
| total_return | 总收益 |
| annual_return | 年化收益 |
| max_drawdown | 最大回撤 |
| sharpe | 夏普 |
| win_rate | 胜率 |
| trade_count | 交易次数 |
| avg_holding_days | 平均持仓天数 |
| turnover | 换手率 |
| benchmark_return | 基准收益 |
| excess_return | 超额收益 |
| best_trade | 最好交易 |
| worst_trade | 最差交易 |
| abnormal_count | 异常数量 |
| runtime_seconds | 运行耗时 |
| config_hash | 配置指纹 |

---

## 五、多 Agent 分工建议

### 1. Hermes

职责：

- 和诚哥讨论回测目标
- 组织 Mimo 的建议
- 产出正式 SPEC
- 设定验收标准
- 最终判断结果是否可信

不做：

- 不直接写代码
- 不直接改 QMT 文件

### 2. MimoCode

职责：

- 提出回测框架优化建议
- 指出策略回测容易失真的地方
- 提出参数实验设计
- 对回测结果给出下一轮优化建议

适合 Prompt：

```text
你不仅要评价这个离线回测方案，还要输出：
1. 哪些设计会导致回测失真
2. 哪些指标必须补充
3. 哪些参数最值得第一轮实验
4. 如何把单次回测压到 5 分钟以内
5. 你认为 MVP 哪些功能可以砍掉
```

### 3. CC

职责：

- 按 SPEC 实现离线回测 MVP
- 建目录结构
- 写回测引擎
- 写配置 runner
- 写报告输出
- 写 sample 数据/测试
- 保证不碰 release/v1.0 和 QMT 生产文件

适合任务：

```text
严格按 SPEC 实现，不扩展需求，不改无关文件，完成后跑验证。
```

### 4. RS

职责：

- 批量运行实验配置
- 汇总多个 summary.json
- 排名收益/回撤/夏普/综合分
- 找异常结果
- 输出下一轮参数建议

不建议：

- 不负责造回测框架
- 不直接接触 QMT 客户端内置回测

### 5. Codex GUI

职责：

- 第二审查员
- 只读 review 回测框架设计和关键实现
- 检查是否存在明显逻辑漏洞

---

## 六、MVP 验收标准

第一版成功标准：

1. 一条命令能跑通 base.yaml。
2. 结果目录自动生成。
3. summary.json 指标完整。
4. report.md 可读。
5. trades.csv 有交易明细。
6. equity_curve.csv 有净值曲线。
7. runtime_seconds 被记录。
8. 用同一配置重复运行，结果一致。
9. 支持至少 3 个 experiment 配置批量运行。
10. RS 能读取多个 summary.json 并输出排名。
11. 不修改 release/v1.0。
12. 不接真实 QMT 交易接口。
13. 不使用 context_mock.py 污染生产构建。
14. 不破坏 GBK/QMT 生产文件。

性能目标：

| 场景 | 目标耗时 |
|---|---|
| 单次日线 base 回测 | 10 秒 - 2 分钟 |
| 5 组参数批量 | 1 - 8 分钟 |
| RS 汇总结果 | 1 - 3 分钟 |
| 人可读报告生成 | 几秒 |

---

## 七、主要风险

| 风险 | 说明 | 应对 |
|---|---|---|
| 回测与实盘逻辑偏差 | 离线逻辑不等于 QMT 实盘 | MVP 标注假设，后续逐步共用 strategy_core |
| 过度追求真实撮合 | 会拖慢项目 | 第一版只做日线近似 |
| 数据质量不足 | 缺停牌/涨跌停/ST 信息 | 第一版记录限制，第二版补字段 |
| Agent 自由发挥过多 | 容易做大做散 | 必须 SPEC 驱动 |
| 结果不可复现 | 参数/数据没记录 | summary 写 config_hash 和数据范围 |
| 误碰生产代码 | QMT 风险高 | 明确禁止修改 release/v1.0 和生产入口 |
| 性能不达标 | 数据读取慢 | 本地缓存、按需加载、parquet 优先 |

---

## 八、推荐实施路线

### Phase 0：Mimo 设计评审

目标：让 MimoCode 对本报告提出改进意见。

输入：本初研报告。

输出：

```text
1. 是否认可路线 A：纯策略逻辑抽取优先
2. MVP 哪些功能应该删/加
3. 第一轮最值得测的参数
4. 可能导致回测失真的地方
5. 如何把运行时间压到 5 分钟内
```

### Phase 1：Hermes 生成正式 SPEC

基于 Mimo 的建议，Hermes 生成：

```text
SPEC_BACKTEST_MVP_OFFLINE_FACTORY.md
```

### Phase 2：CC 实现 MVP

CC 按 SPEC 实现：

- backtest 目录
- 数据读取
- 日线引擎
- 配置 runner
- 报告生成
- batch runner
- sample config
- 最小测试

### Phase 3：RS 批量测试

RS 跑：

- base.yaml
- 3-5 组实验配置
- 汇总 summary.json
- 输出排名和异常

### Phase 4：Hermes 验收

验收：

- 是否符合 SPEC
- 是否提速
- 是否可复现
- 是否产生有价值的策略结论
- 是否进入第二阶段开发

---

## 九、给 MimoCode 的讨论 Prompt

建议直接把下面这段发给 MimoCode：

```text
你现在是策略优化参谋和回测框架评审员。

请阅读这份《离线回测工厂初研报告》，不要写代码，不要修改文件，只做设计评审。

请重点回答：

1. 你是否认可“先做日线离线回测 MVP，而不是直接复刻 QMT 回测”的路线？为什么？
2. 报告中建议的五层结构是否合理？哪些层应该合并或拆分？
3. 路线 A（抽取纯策略核心逻辑）和路线 B（模拟 QMT Context），你更推荐哪条？有没有折中方案？
4. 如果目标是把单次策略回测从 30 分钟压到 3-5 分钟，最大瓶颈会在哪里？
5. 第一版 MVP 必须保留哪些功能？哪些功能必须砍掉？
6. 回测最容易失真的地方有哪些？比如撮合价格、涨跌停、停牌、T+1、手续费、滑点、调仓时点等。
7. 针对当前全天版策略，第一轮最值得实验的 5-10 个参数是什么？
8. summary.json 里还应该加入哪些指标，才能帮助我们判断策略是否靠谱？
9. 你认为 CC 实现这个 MVP 时最容易犯哪些工程错误？
10. 请给出你优化后的 MVP 方案，按“必须做 / 可以做 / 不该做”三类输出。

输出格式：
一、总体判断
二、路线选择
三、MVP 功能取舍
四、回测失真风险
五、第一轮参数实验建议
六、给 CC 的实现提醒
七、你的最终建议
```

---

## 十、Hermes 初步结论

CC 能实现离线回测工厂，但不应让 CC 自己决定框架长什么样。

推荐路线：

```text
MimoCode 先做设计评审
→ Hermes 整理正式 SPEC
→ CC 按 SPEC 实现 MVP
→ RS 批量跑实验和汇总
→ Hermes 验收和辅助决策
```

这套流程能充分发挥各 Agent 优势：

| Agent | 作用 |
|---|---|
| MimoCode | 有想法，负责优化建议和风险提醒 |
| CC | 稳定执行，负责工程落地 |
| RS | 低成本批处理，负责跑批量回测和汇总 |
| Hermes | 大管家，负责调度、SPEC、验收 |

最终目标不是“多一个回测脚本”，而是形成一个可复用的本地回测工厂，让后续策略优化从 30 分钟一次，变成几分钟一轮。