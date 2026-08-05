# SPEC：MCRPS 策略实现（回测工厂接入）

> **角色**：CC（设计）→ Mimo（实施）
> **日期**：2026-07-01
> **状态**：实现 SPEC，参数已由诚哥拍板，待派 Mimo 工单实施
> **输入**：
> - `agent_hub/2026-07-01_mcrps_parameter_research/90_hermes_summary.md`（Hermes 汇总，参数拍板来源）
> - `agent_hub/2026-07-01_mcrps_parameter_research/02_mimo_impl_review.md`（Mimo 实现可行性）
> - `agent_hub/2026-07-01_mcrps_parameter_research/03_cc_data_verification.md`（CC 数据字段核对）
> - `agent_hub/2026-07-01_mcrps_parameter_research/30_minimax_architecture.md`（MiniMax 架构分析）
> - `knowledge_base/30_策略卡片/MCRPS_多维度复合RPS.md`（策略卡）
> **关联**：`specs/SPEC_20260701_MCRPS_PARAMETER_RESEARCH_DISCUSSION.md`（讨论 SPEC）

---

## §0 参数拍板快照（Ground Truth — 诚哥已拍板）

以下为诚哥拍板值，实施时不得自行更改；如需调整须回到诚哥拍板。

| 模块 | 参数 | 拍板值 |
|---|---|---|
| 全局权重 | α / β / γ | **0.4 / 0.35 / 0.25**（静态，α+β+γ=1） |
| CRPS 四周期权重 | 20/60/120/250 日 | **指数衰减 λ=0.015**（约 38%/30%/20%/12%） |
| RPS 加速度 | 定义 | **ΔRPS20** = CRPS_raw(today) − CRPS_raw(today−20 交易日) |
| RPS 加速度 | 加分曲线 | **线性 ±0.5 分/分位，封顶 ±10** |
| SRPS 波动率 | 指标 | **下行波动率 σ_down**（semideviation） |
| SRPS 波动率 | 惩罚函数 | **二次凸惩罚** penalty = min((σ_down / (2·σ_down_median))², 1) |
| SRPS R² | 回归对象 | **log(价格) vs 时间序号，60 日 OLS** |
| SRPS R² | 方向约束 | **β>0 才给分**（β≤0 即下跌趋势，R² 再高也置 0） |
| SRPS 内部权重 | 波动率 / R² | **0.5 / 0.5**（等权，SPEC 推荐，待敏感性分析） |
| VRPS 量比 | 打分 | **阶梯 6 档**（见 §5.3） |
| VRPS 量比 | 方向确认 | **放量但当日收跌 → 得分 ×0.5** |
| 组合 | 调仓频率 | **周** |
| 组合 | top-N | **20**（回测同时跑 10/20/30 对比） |
| 铁律 1 | 入场过滤 | MCRPS < 80 不买 |
| 铁律 2 | 处置 | **观察告警，不强制卖** |
| 风控 | 止损 | **个股 10% 硬止损** |
| 数据 | Universe | **剔除 ST + 停牌 + 次新 + 涨跌停日**，PIT 口径 |
| 数据 | 回测区间 | **2009-2025 全覆盖** |
| 数据 | 复权 | RPS 计算用前复权（reader 层 `adjustment="qfq"`） |
| 数据 | Benchmark | **沪深 300 + 中证 500 + 全 A 等权**（三维对照） |
| 策略关系 | MCRPS vs 6+2 | **并存**（MCRPS 前置筛 + 6+2 精选，若 6+2 修复后） |

---

## §1 Objective

实现 MCRPS（多维度复合 RPS）策略，作为回测工厂的一个 research 策略接入，在**全 A 截面**上跑通"多周期涨幅排名 + 稳定性修正 + 成交量确认"打分选股。

- **目标产物**：`backtest/strategies/research/mcrps/` 4 文件策略 + 配置 yaml + 测试
- **成功标准**：1 年快速回测跑通（P0）→ 全量 2009-2025 回测 + 4 场景验证 + 参数敏感性（P1）
- **硬约束**：**不改回测工厂任何代码**（引擎/撮合/reader/L1 接口），所有逻辑在 `evaluate_day` 内部实现
- **定位**：研究/验证用，**不接实盘**；回测数字仅作研究，不作策略业绩结论（v0.2 红线）

---

## §2 Commands

> 环境：回测工厂用 **Python310**（`/c/.../Python310/python`，含 duckdb）；系统 python / git-bash python 无 duckdb（见 [[backtest-factory-python-env]]）。以下命令在 `D:\QMT_STRATEGIES` 根目录执行。

```bash
# 单策略回测（P0 快速验证：1 年）
py -3.10 -m backtest.scripts.run_backtest --config config/mcrps_v1_quick.yaml

# 全量回测（P1：2009-2025）
py -3.10 -m backtest.scripts.run_backtest --config config/mcrps_v1_full.yaml

# 批量对比（6 层基线 + top-N 10/20/30 网格）
py -3.10 -m backtest.scripts.run_batch --config config/mcrps_batch_compare.yaml

# 单元测试
py -3.10 -m pytest backtest/strategies/research/mcrps/tests/ -v

# 性能基准（1 年实测耗时，校准 MiniMax 预估）
py -3.10 -m backtest.scripts.run_backtest --config config/mcrps_v1_perf.yaml
```

> ⚠️ `run_backtest.py` / `run_batch.py` 的确切参数以源码 `--help` 和现有策略 config（如 `ima_uptrend_v31`）为模板核对，**Mimo 不得臆造参数**。若命令行接口与上述不符，以实际接口为准并在工单回执里说明。

---

## §3 Structure

### 3.1 文件结构

```
backtest/strategies/research/mcrps/
├── __init__.py              # 空
├── strategy.py              # evaluate_day 入口 + register_strategy + ALLOWED_TRADING_MODELS
├── factors.py               # 三因子计算 + cross_sectional_rank
├── prefilter.py             # 预筛（ST/停牌/次新/涨跌停）
├── decision.py              # 组装 StrategyDecision（buy/sell/blocked/diagnostics）
└── tests/
    ├── test_cross_sectional_rank.py
    ├── test_factors.py
    └── test_decision_schema.py

config/
├── mcrps_v1_quick.yaml      # P0：1 年快速验证
├── mcrps_v1_full.yaml       # P1：2009-2025 全量
├── mcrps_v1_perf.yaml       # 性能基准
└── mcrps_batch_compare.yaml # 6 层基线 + top-N 网格
```

### 3.2 各文件职责（参考 `ima_uptrend_v31` 的 score_universe + make_decision 分离模式）

| 文件 | 职责 | 输入 → 输出 |
|---|---|---|
| `strategy.py` | 薄壳入口，编排调用顺序 | evaluate_day 8 参 → dict（参考 `ima_uptrend_v31/strategy.py` ~50 行薄壳） |
| `factors.py` | 纯计算，无副作用 | market_window + date → `{code: raw_score}` × 3 因子 |
| `prefilter.py` | 纯过滤 | market_window + universe + date → `valid_codes` + `blocked_list` |
| `decision.py` | 纯组装 | scores + blocked + positions + config → 引擎决策 dict |

### 3.3 配置 yaml 结构（推荐，以现有策略 config 为模板核对）

```yaml
strategy: mcrps_v1            # 或 strategy_params（以当前工厂 schema 为准，见 V1.0 迁移状态）
data:
  source: astock
  adjustment: qfq              # 前复权，reader 层处理
  universe: full_a_pit         # 全 A，PIT 口径
backtest:
  start_date: 2009-01-01
  end_date: 2025-12-31
  rebalance: weekly            # 周调仓
  max_positions: 20            # top-N
  initial_capital: 1000000
mcrps_params:
  alpha: 0.4
  beta: 0.35
  gamma: 0.25
  periods: [20, 60, 120, 250]
  decay_lambda: 0.015          # 四周期指数衰减权重
  accel_window: 20             # ΔRPS20
  accel_scale: 0.5             # ±0.5 分/分位
  accel_cap: 10.0              # 封顶 ±10
  srps_window: 60              # R² / 波动率窗口
  srps_w_vol: 0.5
  srps_w_r2: 0.5
  vrps_vol_short: 5
  vrps_vol_long: 60
  entry_threshold: 80.0        # 铁律 1
  stop_loss: 0.10              # 个股 10% 硬止损
prefilter:
  min_history: 250            # 次新过滤（上市 <250 交易日）
  limit_up_threshold: 0.0995
  limit_down_threshold: -0.0995
  exclude_st: true
  exclude_suspended: true
benchmark:
  - sh000300                   # 沪深 300
  - sh000905                   # 中证 500
  - full_a_equal_weight        # 全 A 等权
```

> ⚠️ yaml 顶层用 `strategy:` 还是 `strategy_params:` 取决于 V1.0 P3 迁移状态（见 [[backtest-v1-refactor-spec-status]]）。**Mimo 开工前先看一个现有 research 策略的 config（如 `example_ma_cross`），照抄其 schema**，不要臆造。

---

## §4 Code Style

### 4.1 编码与语法

| 项 | 要求 | 说明 |
|---|---|---|
| 编码 | **UTF-8** | backtest/ 目录是现代 Python 库风格，**不是 QMT 实盘的 GBK**。不加 `# coding=gbk` 头 |
| Python | 3.10+ | 工厂用 Python310；可用 type hints（`dict[str, ...]` 等），与 QMT 3.6.8 兼容约束**无关** |
| 依赖 | **只用 pandas / numpy** | 禁用 scipy（见 §4.3）、statsmodels、numba |

### 4.2 向量化优先（强制）

- **禁止逐 code 纯 Python loop 做截面计算**。MiniMax 预估纯 loop 2-7 小时，半向量化 30-60 分钟。
- 推荐**半向量化**：先 loop 收集 `{code: scalar}` 到 `pd.Series`，再用 `Series.rank(pct=True)` 批量截面排名（pandas C 实现，毫秒级）。
- 全向量化（panel/multi-index）作 P1 优化兜底，非 P0 必需。

### 4.3 numpy 替代 scipy（强制）

R² 计算用 numpy，**禁用 `scipy.stats.linregress` / statsmodels.OLS**：

```python
# R² via numpy（x=时间序号, y=log_close）
def _r_squared(log_close: np.ndarray) -> tuple[float, float]:
    """返回 (R², slope_sign)。slope_sign>0 即 β>0（上涨趋势）。"""
    n = len(log_close)
    t = np.arange(n, dtype=np.float64)
    r = np.corrcoef(t, log_close)[0, 1]   # pearson r
    r2 = r * r
    return r2, r   # r 的符号即 β 的符号（std 都正）
```

> β>0 等价于 r>0（因 std(t)>0 且 std(log_close)>0）。故方向约束简化为 `r > 0`。

### 4.4 cross_sectional_rank 辅助函数（核心，factors.py 内）

```python
def cross_sectional_rank(series: pd.Series) -> pd.Series:
    """截面百分位打分：排名百分比 × 100，返回 0-100。
    NaN → 0（数据不足的股票不得分，保持 index 对齐）。
    纯函数，不修改输入，不依赖外部状态，PIT 安全。"""
    valid = series.dropna()
    if len(valid) == 0:
        return pd.Series(0.0, index=series.index)
    ranked = valid.rank(pct=True) * 100.0
    result = pd.Series(0.0, index=series.index)
    result.loc[ranked.index] = ranked.values
    return result
```

---

## §5 三因子实现规格（公式级）

### 5.1 CRPS — 多周期复合 + 加速度

**Step 1 — 四周期收益率**：对每只 code，从 `market_window[code]["close"]`（已前复权）取：
- `ret_N = close[-1] / close[-N-1] - 1`，N ∈ {20, 60, 120, 250}
- 收集为 4 个 `pd.Series(index=code)`

**Step 2 — 四周期截面 RPS**：对 4 个 Series 各调 `cross_sectional_rank` → `rps20 / rps60 / rps120 / rps250`（各 0-100）

**Step 3 — 指数衰减权重**：
```python
periods = [20, 60, 120, 250]
lam = 0.015
raw_w = [np.exp(-lam * p) for p in periods]   # [e^-0.3, e^-0.9, e^-1.8, e^-3.75]
weights = raw_w / sum(raw_w)                   # 归一化 → 约 0.38/0.30/0.20/0.12
CRPS_base = w20*rps20 + w60*rps60 + w120*rps120 + w250*rps250
```

**Step 4 — RPS 加速度（无状态方案）**：
- 在 `evaluate_day` 内对 **today−20 交易日** 的窗口重算 Step 1-3，得 `CRPS_base_20d_ago`
  - 重算需要 4 周期截面 rank（today 已算，20d_ago 再算 4 次 = 共 8 次 rank，毫秒级）
- `delta = CRPS_base_today - CRPS_base_20d_ago`（范围约 ±100）
- `accel_bonus = np.clip(delta * 0.5, -10.0, 10.0)`（线性 ±0.5 分/分位，封顶 ±10）
- 若某 code 20 天前不在 universe（新上市），`accel_bonus = 0`

**Step 5 — CRPS 合成**：
```python
CRPS_raw = CRPS_base + accel_bonus
CRPS = cross_sectional_rank(CRPS_raw)   # 截面归一化到 0-100
```

### 5.2 SRPS — 稳定性修正（波动率 + R²）

**波动率分（下行波动率 + 二次凸惩罚）**：
```python
# 逐 code：
daily_ret = close.pct_change()
neg_ret = daily_ret[daily_ret < 0]
sigma_down = neg_ret.std(ddof=1)           # 下行波动率

# 截面操作：收集所有 code 的 sigma_down
sigma_down_median = np.median(all_sigma_down)   # 截面 median，每日重算
penalty = min((sigma_down / (2.0 * sigma_down_median))**2, 1.0)
SRPS_vol = (1.0 - penalty) * 100.0
```
- 正常股 σ_down≈median → penalty≈0.25 → SRPS_vol≈75
- 暴跌股 σ_down=3×median → penalty=1 → SRPS_vol=0

**R² 分（log 价格 vs 时间，60 日 OLS，β>0 约束）**：
```python
log_close = np.log(close[-60:])
r2, r = _r_squared(log_close)              # numpy 实现，见 §4.3
SRPS_r2 = (r2 * 100.0) if r > 0 else 0.0   # β>0 才给分
```

**SRPS 合成**：
```python
SRPS_raw = 0.5 * SRPS_vol + 0.5 * SRPS_r2   # 等权（SPEC 推荐，待敏感性）
SRPS = cross_sectional_rank(SRPS_raw)
```

### 5.3 VRPS — 量比阶梯 + 量价方向确认

**量比**：`vr = vol.rolling(5).mean() / vol.rolling(60).mean()`，取最新值。

**阶梯 6 档打分**：

| 量比区间 | 得分 |
|---|---|
| `< 0.5` | 0 |
| `[0.5, 0.8)` | 30 |
| `[0.8, 1.2)` | 60 |
| `[1.2, 2.0)` | 80 |
| `[2.0, 4.0)` | 100 |
| `≥ 4.0` | 85（异常巨量，可能出货） |

**量价方向确认**：若量比 ≥ 1.2（放量档）且当日收跌（`pct_chg < 0`），得分 `× 0.5`。

```python
VRPS_raw = tier_score(vr) * (0.5 if (vr >= 1.2 and daily_ret < 0) else 1.0)
VRPS = cross_sectional_rank(VRPS_raw)
```

### 5.4 MCRPS 合成与最终输出

```python
MCRPS_raw = alpha * CRPS + beta * SRPS + gamma * VRPS   # 0.4/0.35/0.25
MCRPS = cross_sectional_rank(MCRPS_raw)                  # 二次归一化，铁律阈值语义=全市场前20%
# 铁律 1 过滤
candidates = codes[MCRPS >= 80.0]
# 按 MCRPS 降序取 top-N
buy_candidates = candidates.sort_desc()[:max_positions]
```

> **二次归一化理由**（MiniMax §2.4）：三因子各自 0-100 截面百分位，加权后可能在区间外；二次 rank 使铁律"≥80"始终语义为"全市场前 20%"，不受权重扰动影响。

---

## §6 预筛实现规格（prefilter.py）

**执行顺序不可颠倒**（排名必须在预筛后，否则垃圾股拉低健康股 RPS）：

| Step | 剔除项 | 条件 | blocked_by 标签 |
|---|---|---|---|
| 0 | 数据不足 | `len(market_window[code]) < 250` | `insufficient_history` |
| 1 | 当日停牌 | 最后一行 `date != today`（或 `vol == 0`） | `suspended` |
| 2 | 涨跌停日 | `pct_chg >= 9.95%` 或 `<= -9.95%` | `limit_up` / `limit_down` |
| 3 | ST | `is_st == 1`（**字段存在性见 §10 矛盾**） | `st_stock` |
| 4 | 次新 | `listed_days < 250` | `new_listing` |

所有被剔除的 code 写入 `blocked_candidates`（含 `code / blocked_by / reason`），不参与截面排名。

> 涨跌停阈值简化：统一 ±9.95%（全 A 95%+ 是 10% 板）。科创/创业 ±20% 误判概率可接受（非 MCRPS 主要候选池）。

---

## §7 决策组装规格（decision.py）

```python
StrategyDecision = {
    "sell_decisions": [...],        # 个股 10% 硬止损触发的卖出
    "buy_candidates": [{             # top-N，按 MCRPS 降序
        "code": ..., "score_total": MCRPS, "rank": i,
        "target_weight": 1.0/max_positions,  # 等权
        ...
    }],
    "target_positions": {...},
    "blocked_candidates": [{         # 预筛剔除
        "code": ..., "blocked_by": ..., "reason": ...
    }],
    "diagnostics": {
        "warnings": [...],
        "candidate_total": N,
        "candidate_passed": M,
        "strategy_specific": {      # v0.4 namespace 机制
            "mcrps": {
                "alpha": 0.4, "beta": 0.35, "gamma": 0.25,
                "crps_median": ..., "srps_median": ..., "vrps_median": ...,
                "sigma_down_median": ...,
                "top20_codes": [...],
                "rule2_alerts": [...]   # 铁律 2：连续 3 天下降的持仓（告警，不强制卖）
            }
        }
    },
    "logs": [...]
}
```

**周调仓实现**：`evaluate_day` 每日被调用，但只在调仓日（如每周第一个交易日）产出有效 buy/sell；非调仓日返回空 decision（仅止损检测仍每日执行）。

**铁律 2（观察告警）**：持仓中 MCRPS 连续 3 天下降 → 写入 `diagnostics.strategy_specific.mcrps.rule2_alerts`，**不强制卖出**。

**个股 10% 硬止损**：持仓个股成本价下跌 10% → `sell_decisions` 触发卖出（每日检测）。

---

## §8 Testing

### 8.1 单元测试（P0）

| 测试 | 覆盖点 |
|---|---|
| `test_cross_sectional_rank` | NaN→0、全 0 输入、单值、正常排名、index 对齐 |
| `test_factors_crps` | 四周期权重正确（约 38/30/20/12）、加速度封顶 ±10 |
| `test_factors_srps` | β>0 约束、下行波动率惩罚曲线、R²×100 映射 |
| `test_factors_vrps` | 6 档阶梯边界、量价方向 ×0.5 |
| `test_decision_schema` | 决策 dict 符合引擎 L1 schema（6 顶层键） |
| `test_prefilter` | 顺序正确、blocked 标签正确 |

### 8.2 6 层对比基线（P1，来自 Hermes §4.1）

| 对比组 | 信号 | 说明 |
|---|---|---|
| 基线 1 | 传统 RPS(250) top-20 | 最简基线 |
| 基线 2 | 等权 4 周期 RPS（无 SRPS/VRPS） | 剥离多周期贡献 |
| 基线 3 | 沪深 300 指数 | 市场 beta |
| MCRPS-v1 | α=0.4/0.35/0.25，指数衰减 λ=0.015 | 推荐静态基线 |
| MCRPS-v2 | v1 + RPS 加速度 | 加速度增量贡献 |
| MCRPS-v3 | v2 + 铁律 1（MCRPS<80 过滤） | 铁律有效性 |

### 8.3 4 场景验证（P1，来自 Hermes §4.2）

| 场景 | 定义 | 典型区间 |
|---|---|---|
| 牛市 | 沪深 300 60 日收益 > 15% | 2014H2-2015H1, 2019Q1, 2020H2 |
| 熊市 | 沪深 300 60 日收益 < -15% | 2015H2-2016Q1, 2018, 2022 |
| 震荡 | \|60 日收益\| ≤ 15% | 2016Q2-2017, 2023-2024 |
| 板块轮动 | 行业收益截面 std > 80% 分位 | 2021 新能源→2022 煤炭→2023 AI |

> 🔴 **轮动期失效是 RPS 类因子命门**（论文 "When Alpha Breaks"）。若轮动场景 MCRPS 显著恶化，需 v1.1 加板块过滤。

### 8.4 参数敏感性分析（P1）

- ±20% 扰动 α/β/γ、decay_lambda、accel_scale、srps_w_vol/w_r2
- 夏普比波动 < 15% → 参数稳健，可拍板
- 参数-夏普曲线：单峰平滑=合理，多峰锯齿=噪声/过拟合
- Bootstrap 95% CI（10000 次重抽样）
- 时间序列交叉验证（4 折）

### 8.5 性能基准（P0 收尾）

- 1 年（~240 天）实测耗时，校准 MiniMax 预估（半向量化 45-60 分钟/5年，全量 2.5-3.5 小时）
- 若单日 > 3s，P1 必须做 R² 向量化优化（numpy 批量 corrcoef）

---

## §9 Boundaries（禁止事项）

1. **不改回测工厂任何代码** — 引擎（`daily_engine.py`）/撮合（`execution.py`）/reader（`astock_reader.py`）/L1 接口（`evaluate_day` 8 参签名、6 顶层键）一律不动
2. **所有逻辑在 `evaluate_day` 内部实现** — 截面排名、预筛、加速度状态都在策略侧解决
3. **不接实盘/模拟** — 不调 passorder / xttrader / xtbp；回测数字仅研究用（v0.2 红线）
4. **不引入新数据源** — 仅用 astock parquet（`data.source: astock` 显式声明）
5. **不切 reader 默认源** — `AstockParquetReader` 通过配置声明，不 hardcode
6. **不写实现代码**（本 SPEC 是文档，实施走 Mimo 工单，CC 不直接编码 — [[cc-role-no-coding]]）
7. **不把回测数字当策略业绩结论**（v0.2 红线，见 [[backtest-v02-mvp-status]]）

---

## §10 风险与已知矛盾（Mimo 必读）

### 10.1 🔴 关键矛盾：astock parquet 的 ST/字段存在性

| 来源 | 说法 |
|---|---|
| 03 CC 数据核对（声称实测） | **有** `is_st`/`suspend_type`/`listed_days`/`up_limit`/`down_limit`（34 列齐全） |
| 02 Mimo / 30 MiniMax（代码审查+推断） | **不含** `is_st`/`name`，ST 标记是阻塞项 |

**处置**：SPEC 字段映射按 03（乐观）写，但 **TASK-0 开工第一步实测核对**（见 §11）。Mimo 用 pyarrow/duckdb 读 `E:/astock/daily/stock_daily.parquet` 列名，确认 03 发现属实：
- ✅ 若字段齐全 → 直接用 `is_st`/`listed_days`/`suspend_type`/`up_limit`/`down_limit`
- ❌ 若 `is_st` 不存在 → fallback：用 tushare `stock_basic` 拉 code→name 映射，name 模糊匹配 ST；或跳过 ST 剔除（标注已知限制）

### 10.2 🟡 复权：前复权动态基准漂移

`AstockParquetReader(adjustment="qfq")` 的 `latest_adj_factor` 取窗口最新日，回测场景下是已知未来信息。但 SPEC §9 约束"不接实盘"，回测研究不受影响。**遵循工厂现有 qfq 模式，不特殊处理执行价**（与 `ima_uptrend_v31` 一致）。

> 03 提"交易执行用实际价"在工厂架构下做不到（需改 reader），违反边界。故全链路前复权，接受微小偏差。

### 10.3 🟡 adj_factor 累积误差

250 日前复权价经 250 次浮点累积，可能与官方前复权差 0.1-0.5%。截面排名同向偏移不影响相对排序，但可能影响铁律 ≥80 的边界股判定。**缓解**：若边界股误判严重，P1 改用收益率链式连乘（`daily_ret = close/pre_close × adj_factor_t/adj_factor_{t-1} - 1`）代替绝对前复权价。

### 10.4 🟡 性能：R² 是瓶颈

MiniMax 预估 SRPS R² 占总时间 67%。P0 用 `np.corrcoef`（比 scipy 快 3-5×）；若不够，P1 用 numpy 批量（panel 一次算全市场）。

### 10.5 🔴 策略有效性：轮动期失效

非工程问题，属回测验证阶段评估。4 场景验证（§8.3）回答。若轮动期显著恶化 → v1.1 板块过滤。

### 10.6 🟡 RPS 加速度无状态

`evaluate_day` 是纯函数。方案：内部重算 20d_ago 截面 RPS（+5% 性能，毫秒级）。新上市股加速度置 0。

---

## §11 Mimo 工单 TASK 列表（实施顺序）

> 每完成一个 TASK 写工单回执，遇异常必停（[[mimo-must-stop-on-any-failure]]）；回执必须 staged 进主 commit（[[mimo-receipt-commit-separation]]）。

| TASK | 内容 | 依赖 | 验收 |
|---|---|---|---|
| **TASK-0** | 🔴 **实测核对 astock parquet schema**（解决 §10.1 矛盾）：读列名，确认 is_st/listed_days/suspend_type/up_limit/down_limit 是否存在；写 `04_schema_verification.md` 结论 | 无 | 字段清单落盘，fallback 方案确定 |
| TASK-1 | `factors.py`：`cross_sectional_rank` + 三因子（CRPS/SRPS/VRPS）公式实现 | TASK-0 | 单元测试 PASS |
| TASK-2 | `prefilter.py`：5 步预筛（按 §6 顺序） | TASK-0 | 预筛测试 PASS |
| TASK-3 | `decision.py`：决策组装 + 周调仓 + 止损 + 铁律 2 告警 | TASK-1/2 | schema 校验 PASS |
| TASK-4 | `strategy.py`：薄壳入口 + register_strategy + ALLOWED_TRADING_MODELS=["next_open"] | TASK-1/2/3 | registry 注册成功 |
| TASK-5 | 配置 yaml（quick/full/perf/batch）+ 1 年快速回测跑通 | TASK-4 | 1 年回测产出 summary.json |
| TASK-6 | 性能基准 + 全量 2009-2025 回测 | TASK-5 | 全量跑通，耗时记录 |
| TASK-7 | 6 层基线对比 + 4 场景 + 参数敏感性 | TASK-6 | 对比报告落盘 `knowledge_base/40_回测与实验/` |

**脏文件守卫**（[[cc-ticket-must-check-dirty-target-file]]）：开工前 `git diff` 确认目标文件初始范围，不把诚哥未 commit 的改动带进 commit。

---

## §12 相关文件

| 项 | 路径 |
|---|---|
| 策略卡 | `knowledge_base/30_策略卡片/MCRPS_多维度复合RPS.md` |
| 讨论 SPEC | `specs/SPEC_20260701_MCRPS_PARAMETER_RESEARCH_DISCUSSION.md` |
| Hermes 汇总 | `agent_hub/2026-07-01_mcrps_parameter_research/90_hermes_summary.md` |
| Mimo 可行性 | `agent_hub/2026-07-01_mcrps_parameter_research/02_mimo_impl_review.md` |
| CC 数据核对 | `agent_hub/2026-07-01_mcrps_parameter_research/03_cc_data_verification.md` |
| MiniMax 架构 | `agent_hub/2026-07-01_mcrps_parameter_research/30_minimax_architecture.md` |
| 工厂 SPEC | `specs/SPEC_BACKTEST_FACTORY_V1.0_REFACTOR.md` |
| 参考策略 | `backtest/strategies/production/ima_uptrend_v31/`（score+decision 分离模式） |
| 参考骨架 | `backtest/strategies/research/example_ma_cross/`（最简薄壳） |
