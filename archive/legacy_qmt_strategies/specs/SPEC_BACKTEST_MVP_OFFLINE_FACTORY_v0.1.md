# SPEC_BACKTEST_MVP_OFFLINE_FACTORY_v0.1

日期：2026-06-13
作者：Hermes
执行方：CC
评审参考：MimoCode / CC / Hermes 多方评审
状态：v0.1，供 CC 最终确认后执行

---

## 1. Objective

### 1.1 背景

当前策略优化与回测流程存在效率问题：一次策略优化/回测讨论常耗时约 30 分钟。主要瓶颈不是纯计算，而是 Agent 每次重复完成：理解项目、寻找入口、确认数据、临时组织参数、读取日志、总结结果、出错重试。

本任务目标是建设一个 **离线回测工厂 MVP**，将当前全天版 6+2 策略的日线级回测标准化为：

```text
固定数据入口
+ 固定 strategy_core 接口
+ 固定回测命令
+ 固定参数配置
+ 固定结果目录
+ 固定 summary/report/trades/equity 输出
```

让后续 RS/Mimo/CC 只需要：

```text
改参数 → 跑命令 → 读 summary.json → 给建议
```

### 1.2 核心目标

1. 建设日线离线回测 MVP。
2. 第一版只服务当前全天版 6+2 策略，不做通用策略框架。
3. 复用现有纯逻辑资产：`core/scoring/dimension6plus2.py`、`core/utils.py`。
4. 不模拟完整 QMT ContextInfo。
5. 不调用 QMT 实盘/模拟交易接口。
6. 回测引擎不依赖 xtquant。
7. 数据下载脚本可依赖 xtquant，但只负责落盘历史日线数据。
8. 支持 `next_open` 主基准成交模型。
9. 支持 `close` 敏感性对照成交模型。
10. 输出标准化结果，供 RS 批量汇总。

### 1.3 成功标准

MVP 完成后应满足：

1. 一条命令可运行 `base.yaml`。
2. 可运行至少 3 个 experiment 配置。
3. 每个 run 输出标准结果目录。
4. 每个 run 输出：
   - `summary.json`
   - `report.md`
   - `trades.csv`
   - `equity_curve.csv`
   - `positions.csv`
   - `logs.txt`
5. `summary.json` 包含完整复现指纹：`config_hash`、`data_hash`、`universe_hash`、`strategy_core_version`。
6. 同一配置重复运行结果一致。
7. `strategy_core` 不依赖 QMT/xtquant。
8. `backtest_engine` 不依赖 xtquant。
9. 不修改 `release/v1.0`。
10. 不修改生产策略入口文件。
11. 不写入 `D:/QMT_POOL` 生产状态文件。

---

## 2. Commands

> 说明：以下命令为目标命令形态。CC 实现时可微调脚本名，但必须保证“一条命令可复现”，并在交付说明中写明最终命令。

### 2.1 数据下载 / 同步

```bash
python backtest/data_tools/download_daily.py --pool backtest/data/universe/strategy_pool_base.csv --start 2024-01-01 --end 2024-12-31
```

要求：

- 该脚本是唯一允许依赖 xtquant 的组件。
- 如果当前环境无法实际调用 xtquant，CC 应先实现脚本框架与 sample 数据读取流程，并在交付中说明阻塞点。
- data_downloader 失败不得阻塞 engine MVP，可用 sample parquet/pkl/csv 推进后续阶段。

### 2.2 数据校验

```bash
python backtest/data_tools/validate_data.py --data-dir backtest/data/daily --pool backtest/data/universe/strategy_pool_base.csv
```

校验内容：

- 文件是否存在。
- 字段是否完整。
- 日期范围是否覆盖配置区间。
- 是否有重复日期。
- open/high/low/close 是否存在 0 或空值。
- 失败股票写入 `failed_downloads.txt` 或等效日志。

### 2.3 单次回测

```bash
python backtest/scripts/run_backtest.py backtest/configs/base.yaml
```

要求：

- 自动生成唯一 run_id。
- 自动创建结果目录。
- 输出标准 6 个文件。

### 2.4 批量回测

```bash
python backtest/scripts/run_batch.py backtest/configs/experiments/
```

要求：

- 逐个运行目录下所有 `.yaml`。
- 单个实验失败不中断批量。
- 失败项写入 `failed_experiments.txt`。
- 生成 `batch_summary.json`。
- `batch_summary.json` 至少按 `total_return` 降序排列，同时保留 `max_drawdown`、`sharpe`、`calmar_ratio`、`execution_price`。

### 2.5 测试

```bash
python -m pytest backtest/tests -q
```

至少覆盖：

- 撮合逻辑。
- T+1。
- 手续费/滑点/印花税方向。
- `market_window` 禁止未来数据。
- 同一配置重复运行一致性。
- 输出完整性。

---

## 3. Structure

### 3.1 目标目录结构

```text
D:\QMT_STRATEGIES\backtest\
  data_tools\
    download_daily.py
    validate_data.py

  data\
    daily\
      {code}.parquet 或 {code}.pkl/csv
    universe\
      strategy_pool_base.csv
    sample\
      sample_daily_data...

  strategy_core\
    __init__.py
    scoring_adapter.py
    decision.py
    evaluate.py
    risk_adapter.py

  engine\
    daily_engine.py
    execution.py
    portfolio.py
    metrics.py
    report.py

  configs\
    base.yaml
    experiments\
      exp_001.yaml
      exp_002.yaml
      exp_003.yaml

  results\
    {run_id}_{config_name}\
      summary.json
      report.md
      trades.csv
      equity_curve.csv
      positions.csv
      logs.txt

  scripts\
    run_backtest.py
    run_batch.py

  tests\
    test_execution.py
    test_tplus1.py
    test_metrics.py
    test_no_future_data.py
    test_reproducibility.py
    test_output_schema.py
```

CC 可根据现有项目结构微调，但必须保持：

1. `strategy_core` 与 `engine` 分离。
2. `data_tools` 与 `engine` 分离。
3. `results` 独立，不混入现有临时 `data/` 产物。
4. 不导入现有 `scripts/backtest_6plus2_full.py`，仅可参考其逻辑。
5. 不导入现有 `scripts/run_backtest.py`，避免继承技术债。

### 3.2 现有资产复用规则

#### 可直接复用

| 文件 | 复用方式 |
|---|---|
| `core/scoring/dimension6plus2.py` | 直接 import `ScoreCalculator6Plus2` |
| `core/utils.py` | 直接 import |
| `tests/conftest.py` | 可复用 mock fixtures |
| `scripts/backtest_params.py` | 可参考结构；backtest_engine 可用现代 Python |

#### 可参考但不直接 import

| 文件 | 原因 |
|---|---|
| `scripts/backtest_6plus2_full.py` | 硬编码参数、无 YAML、输出不标准 |
| `scripts/run_backtest.py` | 另一套入口，可能有技术债 |

#### 需谨慎处理

| 文件 | 处理 |
|---|---|
| `core/risk_manager.py` | 第一版不改本体；通过独立临时 state_dir 隔离 run 状态 |

### 3.3 strategy_core 接口契约

正式实现前必须先完成 **Phase 2.0：接口冻结评审**。

核心入口建议：

```text
def evaluate_day(
    current_date,        # str: "YYYY-MM-DD"
    market_window,       # dict: code -> 日线窗口；只含 current_date 及以前数据
    positions,           # list[dict]
    cash,                # float
    universe,            # list[str]
    account_state,       # dict
    strategy_config,     # dict
    aux_data             # dict
) -> dict
```

#### positions 结构

```text
positions = [
    {
        "code": "000001.SZ",
        "volume": 1000,
        "available_volume": 1000,
        "cost_price": 12.50,
        "entry_date": "2024-03-15",
        "holding_days": 5,
        "last_price": 13.20,
        "unrealized_pnl": 700.0
    }
]
```

#### StrategyDecision 结构

```text
{
    "sell_decisions": [
        {
            "code": "000001.SZ",
            "action": "sell",             # sell / reduce
            "target_volume": 500,         # 0 表示全部
            "reason": "early_stop",
            "layer": "bottom_line",
            "priority": 1
        }
    ],
    "buy_candidates": [
        {
            "code": "600519.SH",
            "score_total": 72.5,
            "score_core": 38.0,
            "bias5": 6.5,
            "daily_pct": 3.2,
            "rank": 1,
            "target_weight": 0.25,
            "target_cash": 25000.0,
            "reason": "top_candidate"
        }
    ],
    "target_positions": [],
    "blocked_candidates": [
        {
            "code": "000002.SZ",
            "blocked_by": "min_score",
            "raw_score": 55.0,
            "reason": "总分 55.0 < 最低分 60"
        }
    ],
    "diagnostics": {
        "scores": {},
        "filter_counts": {},
        "warnings": []
    },
    "logs": []
}
```

### 3.4 score_universe 包装层

必须新增 `score_universe(...) -> scores table` 包装层。

职责：

1. 复用 `ScoreCalculator6Plus2`。
2. 不修改 `ScoreCalculator6Plus2` 本体。
3. 构造 scorer 输入。
4. 处理 PE、sector_heat、benchmark/pool returns 等辅助数据缺失。
5. 输出统一 score record。
6. 记录缺失辅助数据 warning。
7. diagnostics 至少记录：

```text
score_total
score_breakout
score_trend
score_consolidation
score_volume
score_macd
score_valuation
score_market
score_sector
bias5
signal
```

### 3.5 sector_heat 处理

MVP 默认：

```text
sector_heat_mode: zero
```

规则：

1. 不使用 `D:/QMT_POOL/sector_heat.json` 做历史回测。
2. `zero` 模式下所有股票板块热度分统一为 0。
3. 这是一种显式近似，可能改变真实排序。
4. 结果不得宣称等价于完整 QMT wrapper。

`summary.json` 必须包含：

```json
{
  "sector_heat_available": false,
  "sector_heat_mode": "zero",
  "sector_heat_warning": "historical sector heat unavailable; sector score set to 0"
}
```

可选支持：

```text
sector_heat_mode: static
```

`historical` 模式留到第二阶段。

### 3.6 成交模型

必须支持：

```text
execution.price = next_open
execution.price = close
```

主基准：`next_open`。

敏感性对照：`close`。

禁止第一版做 OHLC 线性插值。

#### next_open

```text
T 日产生信号
T+1 日以 open 价成交
T+1 无 open 数据 → 跳过，记录 missing_data_count / unfilled_order_count
回测区间最后一日产生信号 → 丢弃，记录 unfilled_order_count
```

#### close

```text
T 日产生信号
T 日以 close 价成交
必须确保信号计算未使用未来数据
```

### 3.7 T+1 和资金规则

1. 当天买入的股票当天不可卖出。
2. 卖出股票的资金当天可用于买入。
3. 买入数量向下取整到 100 股。
4. 资金不足时跳过或按可买数量成交，并记录日志。
5. summary 中记录资金可用假设。

### 3.8 手续费、滑点、印花税

示例规则：

```text
买入价 = execution_price * (1 + slippage)
卖出价 = execution_price * (1 - slippage)
买入费用 = commission_rate * 成交金额
卖出费用 = (commission_rate + tax_rate) * 成交金额
```

手续费、滑点、印花税必须配置化。

### 3.9 base.yaml 模板

必须提供完整可跑配置，不是空骨架：

```yaml
backtest:
  name: "baseline"
  start_date: "2024-01-01"
  end_date: "2024-12-31"
  initial_cash: 1000000
  benchmark: "000300.SH"

execution:
  price: "next_open"
  slippage: 0.001
  commission_rate: 0.00025
  tax_rate: 0.0001

strategy:
  max_positions: 5
  rebalance_policy: "daily"

scoring:
  min_score: 60
  min_core: 32
  max_bias5: 10
  max_daily_pct: 9
  sector_heat_mode: "zero"

risk:
  early_stop_days: 3
  early_stop_loss: -0.05
  stop_loss: -0.08
  warning_score_threshold: 50
  score_gap_threshold: 15

universe:
  file: "backtest/data/universe/strategy_pool_base.csv"

output:
  dir: "backtest/results"
```

---

## 4. Code Style

### 4.1 总体原则

1. 第一版以可读、可测、可复现为优先，不追求通用框架。
2. 不做大而全抽象。
3. 只实现当前全天版 6+2 策略所需能力。
4. `DailyBacktestEngine` 一个主类即可，不要设计复杂继承体系。
5. `strategy_core` 保持轻薄，不负责数据获取、成交、文件写入。
6. `engine` 负责回测循环、持仓、资金、撮合、指标、输出。
7. `data_tools` 是唯一允许依赖 xtquant 的区域。

### 4.2 Python 版本边界

| 模块 | 版本要求 |
|---|---|
| `strategy_core` | 尽量 Python 3.6-safe |
| `qmt_adapter` | 必须 Python 3.6.8 兼容，虽然本 MVP 不实现它 |
| `backtest_engine` | 可用本机现代 Python |
| `data_tools` | Windows + xtquant 环境 |

`strategy_core` 禁止使用：

```text
dataclass
dict[str, ...]
list[str]
str | None
:=
match/case
复杂 f-string
```

建议使用普通 dict/list/tuple 或普通类。

`backtest_engine` 可以使用现代 Python，但不要让现代语法泄漏到未来可能被 QMT 调用的 `strategy_core`。

### 4.3 编码与生产文件

1. 本任务新建文件应使用 UTF-8。
2. 不修改 GBK QMT 生产策略文件。
3. 不修改 `strategy_main.py`。
4. 不修改 `release/v1.0`。
5. 不修改生产配置。
6. 不写 `D:/QMT_POOL`。

### 4.4 Import 边界

禁止：

```text
strategy_core import xtquant
strategy_core import QMT ContextInfo
engine import xtquant
engine import passorder
engine import get_trade_detail_data
```

允许：

```text
strategy_core import core/scoring/dimension6plus2.py
strategy_core import core/utils.py
data_tools import xtquant
```

### 4.5 状态管理

1. 每个 run 必须独立 state。
2. 第一版不修改 `core/risk_manager.py` 本体。
3. 如需使用 `SellStrategyEngine`，由 `DailyBacktestEngine` 为每个 run 创建独立临时 state_dir。
4. 禁止复用生产 state。
5. 禁止写入 `D:/QMT_POOL`。

---

## 5. Testing

### 5.1 测试必须穿插开发

测试不等到 Phase 5 才补。以下测试必须随 Phase 3 同步完成：

1. `next_open` 最后一日信号不可成交。
2. T+1：当日买入不可当日卖出。
3. 手续费、滑点、印花税方向正确。
4. 同一 config 重复运行结果一致。
5. `market_window` 不包含未来数据。

### 5.2 单元测试

至少覆盖：

1. 撮合价格。
2. 买入数量 100 股取整。
3. 资金不足处理。
4. 买入费用扣除。
5. 卖出费用扣除。
6. T+1 可用数量。
7. 最大回撤计算。
8. 收益率计算。
9. summary 字段完整性。
10. `score_universe` 缺失辅助数据 warning。

### 5.3 回归测试

同一 `base.yaml` 连续运行两次：

1. 交易明细一致。
2. 净值曲线一致。
3. summary 核心指标一致。
4. config_hash 一致。
5. data_hash 一致。

### 5.4 冒烟测试

使用 sample 数据：

```text
5-10 只股票
1 年日线数据
base.yaml
```

要求：

1. 不崩溃。
2. 输出完整。
3. 有 `runtime_seconds`。
4. `logs.txt` 无未捕获异常。

### 5.5 输出完整性测试

每个 run 结果目录必须包含：

```text
summary.json
report.md
trades.csv
equity_curve.csv
positions.csv
logs.txt
```

### 5.6 batch 测试

至少 3 个 experiment 配置：

1. 每个实验独立结果目录。
2. 单个失败不影响其他实验。
3. 生成 `batch_summary.json`。
4. `batch_summary.json` 可被 RS 直接读取排序。

### 5.7 人工验收检查

交付时 CC 必须说明：

1. 最终命令。
2. 实际输出目录。
3. 使用的数据范围。
4. 使用的股票池。
5. `next_open` 与 `close` 是否都能跑。
6. 运行耗时。
7. 哪些功能是近似。
8. 哪些功能未实现。

---

## 6. Boundaries

### 6.1 禁止事项

严禁：

1. 修改 `release/v1.0`。
2. 修改生产 `strategy_main.py`。
3. 修改 QMT 生产配置。
4. 调用真实交易接口。
5. 调用模拟交易下单接口。
6. 在回测中调用 `passorder()`。
7. 在回测中调用账户/持仓真实查询接口。
8. 写入 `D:/QMT_POOL` 生产状态文件。
9. 将 `context_mock.py` 加入生产构建。
10. 在 `strategy_core` 或 `engine` 中 import xtquant。
11. 模拟完整 QMT ContextInfo。
12. 第一版做分钟/tick 回放。
13. 第一版做 OHLC 线性插值。
14. 第一版做通用策略框架。

### 6.2 允许事项

允许：

1. 新建 `D:\QMT_STRATEGIES\backtest\`。
2. 新建 backtest 下的 data/configs/results/scripts/tests。
3. 读取现有纯逻辑模块。
4. 参考现有 scripts 逻辑，但不直接 import。
5. data_downloader 单独依赖 xtquant。
6. 使用 sample 数据推进 engine MVP。
7. 使用现代 Python 实现 backtest_engine。
8. strategy_core 尽量保持 Python 3.6-safe。

### 6.3 分阶段门禁

#### Gate 1：Phase 2.0 strategy_core 接口冻结

进入 strategy_core 实现前，必须先提交接口草案供 Hermes/Mimo 确认：

1. `evaluate_day()` 签名。
2. positions schema。
3. StrategyDecision schema。
4. diagnostics schema。
5. 卖出理由枚举。
6. blocked reason 枚举。

未确认前不要实现 engine。

#### Gate 2：Phase 3 前输出 schema 确认

进入 DailyBacktestEngine 实现前，必须冻结：

1. `summary.json` schema。
2. `trades.csv` schema。
3. `equity_curve.csv` schema。
4. `positions.csv` schema。
5. `logs/diagnostics` schema。

#### Gate 3：Phase 4 后只读 review

报告输出完成后，建议交给 Codex GUI 或 Mimo 做只读 review：

1. 是否存在未来函数。
2. schema 是否足够 RS 汇总。
3. sector_heat 近似是否显式记录。
4. next_open/close 是否清晰区分。

#### Gate 4：Phase 6 RS 批量实验

MVP 工厂验收后，RS 才开始批量跑参数实验。

RS 不参与主实现，只负责：

1. 运行实验配置。
2. 汇总 `summary.json`。
3. 输出排名。
4. 标记异常。
5. 给下一轮参数建议。

### 6.4 第一轮实验配置建议

正式实现后，第一批实验建议包括：

```text
A. baseline
B. max_bias5=9
C. min_score=72 + max_bias5=9
D. min_core=38 + max_bias5=9
E. max_bias5=9 + early_stop_days=2 + early_stop_loss=-0.045
F. min_score=68 + max_bias5=9
G. min_core=35 + max_bias5=9
```

每组必须跑：

```text
execution.price=next_open
execution.price=close
```

### 6.5 交付格式

CC 交付时必须输出：

```text
1. 实现了哪些 Phase
2. 运行命令
3. 测试命令与结果
4. 结果目录
5. 已知近似假设
6. 未实现项
7. 是否触碰生产文件：必须明确说明未触碰
8. 下一步建议
```

---

## 7. Phase Plan

### Phase 1A：本地缓存格式与读取器

目标：不依赖 xtquant，先定义本地日线数据格式和读取器。

验收：

1. sample 数据可被读取。
2. 字段完整。
3. 可构造 `market_window`。

### Phase 1B：xtquant 数据下载器

目标：实现数据下载脚本框架。

验收：

1. 支持股票池 + 日期范围输入。
2. 输出本地日线文件。
3. 单只失败不中断全部。
4. 如环境阻塞，记录阻塞点并允许 sample 数据继续推进。

### Phase 2.0：接口冻结评审

目标：提交 `evaluate_day`、positions、StrategyDecision、diagnostics、输出 schema 草案。

验收：Hermes/Mimo 确认后进入 Phase 2。

### Phase 2：strategy_core 最小实现

目标：包装 scorer、实现买入过滤/排序/卖出决策/换仓判断。

验收：

1. 不依赖 QMT/xtquant。
2. 可用假数据独立调用。
3. sector_heat zero 模式显式记录。
4. run state 隔离。

### Phase 3：DailyBacktestEngine

目标：日线主循环、撮合、持仓、资金、T+1、净值。

验收：

1. base.yaml 可跑。
2. next_open/close 可选。
3. 关键测试同步完成。
4. 不 import xtquant。

### Phase 4：报告输出

目标：标准 6 文件输出。

验收：

1. summary 字段完整。
2. trades 可追溯。
3. equity_curve 可绘图。
4. 两次运行一致。

### Phase 5：batch runner + 测试

目标：批量配置运行与测试补齐。

验收：

1. 3+ experiments 可跑。
2. batch_summary.json 生成。
3. 测试通过。

### Phase 6：RS 批量实验 + Hermes 验收

目标：RS 跑第一轮参数实验，Hermes 汇总判断。

本 SPEC 不要求 CC 执行 Phase 6，但 CC 需保证输出足够标准，便于 RS 使用。
