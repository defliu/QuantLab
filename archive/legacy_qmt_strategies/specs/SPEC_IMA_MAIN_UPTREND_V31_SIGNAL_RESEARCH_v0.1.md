# SPEC_IMA_MAIN_UPTREND_V31_SIGNAL_RESEARCH_v0.1

日期：2026-06-13
作者：Hermes
策略来源：`C:\Users\Administrator\Desktop\IMA知识库_主升浪量化体系\`
协作模式：MimoCode 策略方案主导，CC 后续按 SPEC 工程实现，Hermes 验收
状态：v0.1 研究型 SPEC，待 Mimo/CC 最终确认后进入实施

---

## 1. Objective

### 1.1 背景

诚哥希望基于 IMA 知识库中的“主升浪量化体系”搭建新策略。知识库中核心版本为 **主升浪 V3.1**，其本质是用通达信公式识别：

```text
筹码集中 → 均线粘合 → 波动收敛 → 温和放量突破 → 趋势加速
```

V3.1 的信号结构是：

```text
5 个硬关卡 HARD
+ 13 个 0/1 评分因子 SC
最终信号 = HARD AND SC >= 阈值
```

当前项目已有离线回测工厂建设路线：DuckDB 只读数据源、F 盘 workspace、标准化输出、短样本警告等。IMA V3.1 第一版应复用这些基础设施，但**不应立即做完整交易回测，也不应接 QMT 实盘**。

### 1.2 本 SPEC 的性质

本 SPEC 是 **信号研究型 MVP**，不是完整交易回测 SPEC，也不是实盘交易 SPEC。

第一版目标不是证明策略长期盈利，而是回答：

```text
IMA V3.1 主升浪信号，在当前 DuckDB 日线数据上，是否具有可重复的短周期正收益倾向？
```

### 1.3 已确认决策

诚哥已确认以下 4 点：

1. 第一版先做 **信号研究型 MVP**，不是完整交易回测。
2. H1 筹码集中度默认 `disabled`，不让 COST 替代方案拖慢第一版。
3. IMA V3.1 独立 `strategy_core`，不与当前 6+2 策略融合。
4. 第一版由 MimoCode 做策略方案主导；后续 CC 只按 SPEC 做工程实现。

### 1.4 MVP 要回答的问题

第一版必须输出可判断的证据：

1. V3.1 默认参数下，每日信号数量是否合理？
2. 信号出现后 T+1 开盘买入，1/3/5/10 日收益分布如何？
3. `SC >= 6/7/8/9` 各阈值下，信号数量与收益倾向如何变化？
4. H1 disabled 后，其余 H2-H5 + S1-S13 是否仍有有效性？
5. 信号是否高度集中在少数行业/少数股票？
6. 假突破风险有多大？最大单笔亏损和亏损分布如何？
7. 是否值得进入下一阶段完整交易回测？

### 1.5 明确不做

MVP 不做：

1. QMT 实盘/模拟端接入。
2. 自动下单。
3. 与 6+2 策略融合。
4. 股东户数数据接入。
5. 完整 COST/chip_estimate 筹码估算。
6. 完整组合交易回测。
7. 复杂卖出条件，如 RSI>85、放量滞涨、跌破 MA20。
8. 修改现有 6+2 strategy_core。
9. 修改 QMT 生产策略文件。
10. 修改 release/v1.0。

---

## 2. Commands

> 本节为目标命令形态。CC 实现时可微调脚本参数，但必须保持“一条命令可复现”，并在交付报告中写清最终命令。

### 2.1 单次信号研究

```bash
py -3.10 -S backtest/scripts/run_signal_research_ima.py backtest/configs/base_ima.yaml
```

若本机 Python 3.10 直接启动受 `.pth` 编码问题影响，可沿用当前 backtest 工厂验收方式，在执行说明中写明 Python 启动方式。

目标：

1. 加载 DuckDB 日线数据。
2. 按 `base_ima.yaml` 生成每日 IMA V3.1 信号。
3. 计算信号后 1/3/5/10 日收益。
4. 输出标准结果目录。

### 2.2 SC 阈值批量实验

```bash
py -3.10 -S backtest/scripts/run_signal_research_ima.py backtest/configs/ima_experiments/sc_thresholds.yaml
```

或：

```bash
py -3.10 -S backtest/scripts/run_signal_research_ima_batch.py backtest/configs/ima_experiments/
```

至少覆盖：

```text
SC >= 6
SC >= 7
SC >= 8
SC >= 9
```

### 2.3 H1 模式对照

```bash
py -3.10 -S backtest/scripts/run_signal_research_ima_batch.py backtest/configs/ima_experiments/h1_modes/
```

MVP 必须支持：

```text
H1_mode = disabled
```

可选支持：

```text
H1_mode = turnover_proxy
```

若缺少流通股本字段，`turnover_proxy` 可在 v0.1 中标为未实现，不阻塞 MVP。

### 2.4 测试

```bash
py -3.10 -S - <<'PY'
import sys
sys.path.append(r'C:\Users\Administrator\AppData\Local\Programs\Python\Python310\Lib\site-packages')
import pytest
raise SystemExit(pytest.main(['backtest/tests','-v']))
PY
```

若 CC 已修复 Python 3.10 `.pth` 问题，也可使用：

```bash
py -3.10 -m pytest backtest/tests -v
```

### 2.5 输出位置

所有大产物必须落 F 盘：

```text
F:\backtest_workspace\results\{run_id}_ima_uptrend_v31\
```

禁止在 C 盘写 backtest 产物。禁止在 D 盘写 results/cache/sample_db/logs 等大产物。

---

## 3. Structure

### 3.1 新增文件建议

```text
D:\QMT_STRATEGIES\backtest\
  strategy_core\
    ima_uptrend_v31.py

  configs\
    base_ima.yaml
    ima_experiments\
      sc_threshold_6.yaml
      sc_threshold_7.yaml
      sc_threshold_8.yaml
      sc_threshold_9.yaml
      h1_disabled.yaml
      h1_turnover_proxy.yaml      # 可选，若流通股本不可用则暂不实现

  scripts\
    run_signal_research_ima.py
    run_signal_research_ima_batch.py   # 可选；如单脚本已支持批量，可不单独建

  tests\
    test_ima_uptrend_v31.py
    test_ima_signal_returns.py
    test_ima_no_lookahead.py
```

### 3.2 复用现有基础设施

必须复用当前回测工厂已有模块：

| 模块 | 用途 |
|---|---|
| `backtest/data_tools/duckdb_reader.py` | 只读 DuckDB 日线数据 |
| `backtest/paths.py` | D/F/C 盘路径边界 |
| `backtest/scripts/init_workspace.py` | F 盘 workspace 初始化 |
| `backtest/engine/hashing.py` | config/data/universe 指纹 |
| `F:\backtest_workspace\results\` | 输出目录 |

不得复制一套新的数据读取器；不得绕过 `paths.py` 自己硬编码大产物路径。

### 3.3 strategy_core 独立性

IMA V3.1 必须作为独立模块存在：

```text
backtest/strategy_core/ima_uptrend_v31.py
```

不得修改当前 6+2 strategy_core，不得把 IMA 因子混入 6+2 scorer。

后续如要融合，另起 SPEC。

### 3.4 IMA V3.1 输入输出接口建议

第一版可以采用轻量接口：

```python
def evaluate_ima_day(current_date, market_window, universe, config, aux_data):
    """
    输入：
      current_date: str, YYYY-MM-DD
      market_window: dict, code -> DataFrame[date, open, high, low, close, vol, amount]
      universe: list[str]
      config: dict
      aux_data: dict

    输出：
      dict，包含 signals / blocked / diagnostics / logs
    """
```

建议输出结构：

```python
{
    "signals": [
        {
            "date": "2026-02-27",
            "code": "000001.SZ",
            "score": 8,
            "hard_pass": True,
            "h": {"H1": None, "H2": True, "H3": True, "H4": True, "H5": True},
            "s": {"S1": 1, "S2": 1, "S3": 0, "S4": 1, "S5": 1, "S6": 1, "S7": 1, "S8": 1, "S9": 0, "S10": 1, "S11": 0, "S12": 1, "S13": 1},
            "h1_mode": "disabled",
            "reason": "H2-H5 pass; SC=8>=7"
        }
    ],
    "blocked": [
        {
            "date": "2026-02-27",
            "code": "000002.SZ",
            "blocked_by": "H3",
            "score": 9,
            "reason": "3日涨幅不在区间"
        }
    ],
    "diagnostics": {
        "signal_count": 0,
        "blocked_counts": {},
        "factor_pass_counts": {},
        "warnings": []
    },
    "logs": []
}
```

### 3.5 输出文件

每次 run 输出目录必须至少包含：

```text
summary.json
signal_report.md
signals.csv
signal_returns.csv
factor_diagnostics.csv
logs.txt
```

#### `signals.csv`

建议字段：

```text
date, code, score, H1, H2, H3, H4, H5, S1, S2, ..., S13, h1_mode, reason
```

#### `signal_returns.csv`

建议字段：

```text
signal_date, code, entry_date, entry_open, ret_1d, ret_3d, ret_5d, ret_10d, max_loss_10d, max_gain_10d, score, h1_mode
```

#### `summary.json`

必须包含：

```json
{
  "strategy_name": "ima_uptrend_v31",
  "research_type": "signal_research",
  "is_trade_backtest": false,
  "sample_period_warning": {...},
  "h1_mode": "disabled",
  "sc_threshold": 7,
  "signal_count_total": 0,
  "avg_signals_per_day": 0.0,
  "win_rate_1d": null,
  "win_rate_3d": null,
  "win_rate_5d": null,
  "win_rate_10d": null,
  "avg_return_1d": null,
  "avg_return_3d": null,
  "avg_return_5d": null,
  "avg_return_10d": null,
  "max_single_loss_10d": null,
  "profit_loss_ratio_5d": null,
  "factor_pass_counts": {},
  "blocked_counts": {},
  "data_backend": "duckdb",
  "data_source": "jince_zhisuan"
}
```

---

## 4. Code Style

### 4.1 原则

1. 第一版以信号研究为核心，不做交易组合引擎。
2. IMA 逻辑保持独立，不和 6+2 混写。
3. 阈值全部配置化，不硬编码在函数深处。
4. 计算逻辑必须可解释，每个信号保留 H/S 明细。
5. 不做复杂抽象，不做通用策略框架。
6. 不接 QMT/xtquant。
7. 不写生产文件。

### 4.2 Python 兼容

`ima_uptrend_v31.py` 应尽量 Python 3.6-safe，为未来 QMT adapter 留路。

避免：

```text
dataclass
dict[str, ...]
str | None
match/case
walrus :=
复杂 f-string
```

可以使用普通 dict/list/tuple 和普通函数。

### 4.3 因子命名

硬关：

```text
H1_CHIP
H2_MA_BULL
H3_3D_GAIN
H4_NOT_DEEP_PIT_RUSH
H5_VOLUME_CEILING
```

评分：

```text
S1_MA_BULL
S2_MA_RISING
S3_PRICE_STRONG
S4_VOLUME_EXPAND
S5_BREAKOUT_20H
S6_MACD_STRONG
S7_RSI_HEALTHY
S8_MA_CONVERGENCE
S9_VOLATILITY_CONTRACTION
S10_LONG_TREND
S11_DIVERGENCE_ACCEL
S12_CONTINUOUS_STRENGTH
S13_VOLUME_TREND
```

输出 CSV 可使用短名 `H1`、`S1` 等，但代码内部建议保留可读常量。

### 4.4 前视偏差规则

V3.1 是日线收盘信号。信号研究中必须明确：

```text
T 日信号只能用于 T+1 open 后的收益观察。
```

禁止：

```text
T 日信号用 T 日 close 买入并计算当日收益。
```

`signal_returns.csv` 的 entry_date 必须是下一交易日。

### 4.5 H1 筹码模式

配置：

```yaml
h1:
  mode: disabled
```

MVP 默认：

```text
disabled
```

`disabled` 语义：

```text
H1 不参与 HARD 判断。
H1 输出为 null 或 "disabled"。
summary 中必须记录 h1_mode=disabled。
```

可选 `turnover_proxy` 若实现，必须在 report 里标明近似假设。

---

## 5. Testing

### 5.1 单元测试

必须测试：

1. MA5/10/20/60 计算。
2. H2 多头排列。
3. H3 3 日涨幅边界：5%、25%。
4. H4 急拉坑底排除。
5. H5 量能天花板。
6. S1-S13 基础逻辑。
7. SC 阈值：SC=6/7/8/9。
8. H1 disabled 时 HARD 不受 H1 阻断。
9. 缺数据/历史不足时 blocked。

### 5.2 前视偏差测试

必须有测试：

```text
T 日信号 → T+1 open 作为 entry
```

验证：

1. `signal_returns.csv.entry_date` 是信号日后第一个交易日。
2. 收益计算不使用 T 日 close 作为 entry。
3. 最后一个交易日产生的信号无法计算 T+1 entry，应记录为 unavailable/unfilled，而不是静默忽略。

### 5.3 信号收益测试

构造小样本，验证：

```text
ret_1d = exit_close_after_1d / entry_open - 1
ret_3d = exit_close_after_3d / entry_open - 1
ret_5d = exit_close_after_5d / entry_open - 1
ret_10d = exit_close_after_10d / entry_open - 1
```

### 5.4 输出完整性测试

每次 run 必须有：

```text
summary.json
signal_report.md
signals.csv
signal_returns.csv
factor_diagnostics.csv
logs.txt
```

### 5.5 短样本警告

继承回测工厂 v0.2：

1. `signal_report.md` 顶部必须提示当前样本期较短。
2. `summary.json` 必须有 `sample_period_warning`。
3. `logs.txt` 顶部必须有纯文本 warning。

### 5.6 性能要求

第一版只做信号研究，性能目标：

| 场景 | 目标 |
|---|---|
| 200 只股票 × 6 个月 | < 10 秒 |
| 全表 5197 只 × 6 个月 | < 60 秒 |

若超过，交付报告说明瓶颈，不阻塞 MVP。

---

## 6. Boundaries

### 6.1 禁止事项

严禁：

1. 修改 QMT 生产策略文件。
2. 修改 release/v1.0。
3. 调用 xtquant / MiniQMT。
4. 调用 passorder 或任何交易接口。
5. 写入 `F:\金策智算\`。
6. 写 C 盘 backtest 产物。
7. 在 D 盘写 results/cache/sample_db/logs 等大产物。
8. 修改现有 6+2 strategy_core。
9. 把 IMA V3.1 混入 6+2 scorer。
10. 第一版实现股东户数/财务数据接入。
11. 第一版实现完整 COST/chip_estimate。
12. 第一版实现复杂卖出引擎。
13. 第一版接 QMT 实盘。

### 6.2 允许事项

允许：

1. 新增独立 IMA strategy_core 文件。
2. 新增 IMA 专用 config。
3. 新增 IMA 信号研究脚本。
4. 使用 DuckDBDailyReader 读取现有只读数据。
5. 输出到 `F:\backtest_workspace\results\`。
6. 使用现有 workspace / hashing / paths 能力。
7. 生成 signal research 报告。

### 6.3 Mimo 与 CC 分工

Mimo：

```text
策略方案主导
参数实验建议
信号质量判断
COST/H1 方案评审
```

CC：

```text
等 SPEC 通过后按图实现
不自行扩展为完整交易系统
不接实盘
不融合 6+2
```

Hermes：

```text
调度、SPEC、验收、最终决策
```

### 6.4 交付格式

CC/Mimo 后续交付应包含：

1. 实现了哪些功能。
2. 使用的数据范围。
3. 使用的股票池。
4. 信号数量。
5. 1/3/5/10 日收益统计。
6. SC 阈值分组表现。
7. H1 模式说明。
8. 短样本期风险说明。
9. 是否建议进入完整交易回测。

---

## 7. Phase Plan

### Phase 0：Mimo 策略确认

当前已完成。

输出：

```text
D:\QMT_STRATEGIES\agent_hub\2026-06-13_ima_main_uptrend\02_mimo_review.md
D:\QMT_STRATEGIES\agent_hub\2026-06-13_ima_main_uptrend\90_hermes_summary.md
```

### Phase 1：信号复刻

实现：

```text
backtest/strategy_core/ima_uptrend_v31.py
```

目标：

1. H2-H5。
2. S1-S13。
3. SC 计算。
4. H1 disabled。
5. signals / blocked / diagnostics 输出。

### Phase 2：信号收益研究

实现：

```text
run_signal_research_ima.py
```

目标：

1. T 日信号。
2. T+1 open entry。
3. 1/3/5/10 日收益。
4. signals.csv / signal_returns.csv。

### Phase 3：参数实验

实现：

```text
SC 阈值 6/7/8/9
H1 disabled baseline
```

可选：

```text
H1 turnover_proxy
```

### Phase 4：报告与决策

输出：

```text
signal_report.md
summary.json
```

Hermes/Mimo 判断：

```text
是否进入完整交易回测 SPEC？
是否补 H1？
是否考虑与 6+2 组合？
```

---

## 8. First Experiment Defaults

建议 `base_ima.yaml`：

```yaml
research:
  name: "ima_uptrend_v31_signal_research"
  start_date: "2025-09-01"
  end_date: "2026-02-27"
  horizons: [1, 3, 5, 10]

strategy:
  sc_threshold: 7
  h1_mode: "disabled"

hard_filters:
  gain_3d_min: 5
  gain_3d_max: 25
  deep_pit_amplitude_18d: 16
  rush_3d_ratio: 1.13
  volume_ceiling_multiple: 5

factors:
  volume_expand_multiple: 1.5
  breakout_20h_ratio: 0.998
  rsi_min: 45
  rsi_max: 80
  ma_convergence_max: 5
  volatility_contraction_max: 5

execution_observation:
  entry: "next_open"

output:
  workspace_root: "F:/backtest_workspace"
```

---

## 9. Acceptance Criteria

MVP 验收通过需满足：

1. 能生成每日 IMA V3.1 信号。
2. 能输出每个信号的 H/S 明细。
3. H1 disabled 语义明确。
4. 能计算 1/3/5/10 日收益。
5. 严格 T+1 open entry，无前视偏差。
6. 输出 6 个标准文件。
7. SC 阈值 6/7/8/9 至少能批量对比。
8. report 明确短样本期风险。
9. 不修改 6+2，不接 QMT，不写生产路径。
10. Hermes/Mimo 能根据报告判断是否进入下一阶段。
