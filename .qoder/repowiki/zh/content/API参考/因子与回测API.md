# API 参考 · 因子与回测

> 速查向。因子框架原理见 `项目概述/系统架构设计/因子框架设计/`，回测原理见 `回测引擎/引擎核心架构.md`。

---

## 一、FactorBase —— 因子基类（`factors/base.py`）

```python
from factors.base import FactorBase

class MyFactor(FactorBase):
    def __init__(self):
        super().__init__(name="my_factor", category="价量", description="...")

    def compute(self, panel, fin_ffill, **kwargs):
        # 返回 Series（index 与 panel 对齐）
        ...
```

| 成员 | 说明 |
| --- | --- |
| `__init__(name, category, description="")` | 三个元数据字段，注册与报告都依赖它们 |
| `compute(panel, fin_ffill, **kwargs)` | **抽象方法**，必须实现；`fin_ffill` 为已前填充的财务数据（PIT 安全） |
| `winsorize(series, lower=0.01, upper=0.99)` | 静态方法，去极值（默认 1%/99%） |
| `zscore(series)` | 静态方法，标准化 |
| `rank_normalize(series)` | 静态方法，秩归一化 |

预处理顺序惯例：**先 winsorize 去极值，再 zscore 或 rank_normalize**，横截面上做。

内置因子见 `factors/`：`atr.py`、`fina.py`、`roe.py`、`rps_acceleration.py`、`volatility.py`、`vwap_volume_corr.py`（GTJA191：`-1 * rank(corr(rank(VWAP), rank(volume), 5))`）。

---

## 二、FactorEngine —— 注册/计算/IC（`factors/engine.py`）

```python
engine = FactorEngine()
engine.register(MyFactor())
panel   = engine.compute_all(df, fin_ffill)          # 批量计算全部已注册因子
ic      = engine.compute_ic(panel, price_data, forward_days=20)
names   = engine.list_factors()
```

| 方法 | 签名 | 说明 |
| --- | --- | --- |
| `register` | `(factor)` | 注册一个 FactorBase 实例 |
| `compute_all` | `(panel, fin_ffill, **kwargs)` | 遍历已注册因子逐个 compute 并拼成面板 |
| `compute_ic` | `(factor_panel, price_data, forward_days=20)` | 计算 IC（前瞻 `forward_days` 日收益） |
| `list_factors` | `()` | 列出已注册因子 |

### 上线判定阈值（审计框架硬指标）

| 指标 | 门槛 |
| --- | --- |
| IC | ≥ 0.03 |
| ICIR | ≥ 0.3 |
| 五分位 | 单调 |
| Calmar | ≥ 0.6 |

四项不达标即不予上线。历史教训：**大市值域多次失败**（Project_12 -60.8%、Project_18、Project_01、2026-08-18 T-20260817-004 均 FAIL），低波 alpha 主要存在于小微盘域。

---

## 三、run_backtest —— 回测主入口（`backtest/engine.py`）

```python
from backtest.engine import run_backtest

result = run_backtest(
    reader,                  # reader 实例（鸭子类型：load_window/trading_calendar/coverage/close）
    universe,                # 股票代码 list
    start_date,              # "YYYY-MM-DD"
    end_date,                # "YYYY-MM-DD"
    strategy_config,         # dict：策略参数
    execution_cfg,           # dict：price / slippage / commission_rate / tax_rate
    initial_cash,            # float
    aux_data=None,
    benchmark_code=None,
    benchmark_db_path=DEFAULT_BENCHMARK_DB,
    config_name="baseline",
    config_hash="",
    universe_hash="",
    run_id=None,
    now=None,
    universe_by_date=None,   # PIT 模式：{as_of_date: [codes]}
    strategy_name=None,      # 扁平注册名，默认 'atr_lowvol'
    trading_model=None,      # 默认 'next_open'
    fundamentals_reader=None,
    industry_map=None,       # {code: industry}，用于行业 cap
)
```

要点：

- 返回**内存中的结果结构体**，落盘由 `report.py` 负责；
- `run_id` 由 `_make_run_id(started_at)` 生成，配合 `universe_hash` / `data_hash` / `config_hash` 完成 run 溯源（`backtest/hashing.py`），未传时自动创建；
- `universe_by_date` 是**PIT 股票池**入口，做幸存者偏差修复时必须用它，而不是固定 `universe`；
- 默认基准 DB：`F:/backtest_workspace/data/duckdb/benchmark_index.duckdb`，需存在且包含所选 `benchmark_code`；
- 报告目录默认 `D:/QuantLab/reports`，可用 `set_results_dir` 覆盖。

### 策略解析

`resolve_strategy(strategy_name, trading_model)` 返回 `(_evaluate_day, _trading_model)`，是「扁平注册名 → 每日评估函数」的解析点。新增策略在此注册，不在主循环里写分支。

---

## 四、回测分层（谁负责什么）

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 编排 | `engine.py` | T→T+1 open 撮合主循环、benchmark 加载、run_id、window 切片、警告去重 |
| 撮合 | `execution.py` | 纯函数：涨跌停判定（双创/北交/主板/ST）、lot 取整、滑点/手续费/印花税、next_open 拒绝分支 |
| 记账 | `portfolio.py` | `Portfolio` 维护 cash / positions / available_volume（T+1 冻结），mark_to_market、advance_holding_days、apply_trade |
| 组合层风控 | `rebalance.py` | target_weights → sell_decisions + buy_candidates；equal / vol_parity / custom / absolute sizing、行业 cap、max_positions、min_position_value、vol_target + leverage |
| 每日风控 | `risk/` | **每个交易日**评估的守卫（CrashGuard / RegimeGate），可跨调仓期强制转现 |
| 绩效 | `analyzer.py` | CAGR / max_drawdown / sharpe / calmar / win_rate / avg_holding_days / excess_return / tracking_error |
| 落盘 | `report.py` | frozen CSV 列序（trades / equity_curve / positions）、logs.txt WARN block、report.md |

**组合层风控（调仓日才跑）与每日风控（每天跑）是互补关系，不是替代关系。**

---

## 五、风控叠加层 API（`risk/`）

```python
from risk.daily_overlay import build_daily_risk_overlay
overlay = build_daily_risk_overlay(strategy_config)
decision = overlay.apply(decision, today, pf, window, strategy_config, industry_map)
```

- 接入点：engine 主循环中「strategy decision 生成后、成为 pending 前」；
- 要求空仓时，用框架既有 `target_weights_to_decision({})` 产出 full-exit decision，**覆盖策略原意**；
- 开关默认关闭（`crash_guard=0` / `regime_gate=0`），未启用时零影响、逐笔一致。

Config 键：

```
crash_guard=1  crash_lookback=5  crash_threshold=-0.07  crash_cooldown=5
regime_gate=1  regime_lookback=60  regime_vol_percentile_threshold=0.50
```

单测：`python risk/test_overlay.py`（纯逻辑，不依赖大数据）。

---

## 六、产物契约

`report.py` 写出的 CSV 列序是**冻结的**，改动需同步所有下游分析脚本：

- `trades.csv`（13 列，含拒绝原因：suspended / limit_up / limit_down / no_target_cash / capacity_exceeded）
- `equity_curve.csv`
- `positions.csv`

外加 `logs.txt`（WARN block）与 `report.md`（汇总报表）。

---

## 七、验证门槛（上线前必过）

```bash
cd projects/verification
python engine_tests.py       # B-1~B-8：已知结果/成本/涨跌停/资金/一致性/先卖后买/止损
python robustness_tests.py   # D-1~D-7：子样本/牛熊/压力/成本压力/容量/未来函数/幸存者偏差
```

**硬规则：新策略上线前必须通过 B 模块全部用例，以及 D 模块至少 D-1 / D-2 / D-6。** 报告落 `results/engine_test_report.md` 与 `results/robustness_report.md`。
