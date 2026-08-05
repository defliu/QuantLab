# Spec: MACD 指标策略（research/macd_strategy）

## Objective

把用户提供的 MACD 思维导图战法工程化，实现为回测工厂可注册策略，用 `E:\astock` 全 A 日线跑回测，验证纯 MACD 是否具备 alpha。

## Scope

- 新增策略：`backtest/strategies/research/macd_strategy/strategy.py`（含 `__init__.py` 注册）
- 新增配置：`backtest/configs/macd_astock.yaml`（v1）、`macd_astock_v2.yaml`（沪深300 基准）、`macd_astock_v2b.yaml`（中证1000 基准）
- 新增工具：`backtest/scripts/build_macd_universe.py`（从 `stock_basic` 生成 universe）
- 修复数据源：`backtest/data_tools/astock_reader.py`（parquet 非 MultiIndex → 构造 MultiIndex + 只读所需列 + hfq 复权）

## Implementation

- 接口：`evaluate_day(current_date, market_window, positions, cash, universe, account_state, strategy_config, aux_data)`
- MACD：标准 `EMA12`/`EMA26` → `DIF`；`DIF` 的 `EMA9` → `DEA`；`DIF-DEA` → 红绿柱 `HIST`
- 买入信号：
  - 零轴上金叉（`DIF>0` 且金叉 且 `HIST>0`）→ 强买点 优先级 10
  - 零轴下金叉（`allow_below_zero_cross` 控制，需底背离 或 `HIST` 翻红确认）→ 弱买点 优先级 6
  - 纯底背离（无金叉但价格新低、`HIST` 未新低，动能转强）→ 优先级 4
- 卖出信号：死叉（`DIF` 下穿 `DEA`）/ 顶背离（价格新高、`HIST` 未新高）/ 硬止损（`unrealized_pnl/cost_basis <= -8%`）
- 仓位：按 `max_positions`(=8) 均分 `total_asset`，按单只资金 ÷ 近似开盘价预判可买手数，`target_cash` 设为刚好买满整数手（含滑点垫）；买不起 100 股直接 `below_min_lot` 跳过
- 撮合：`next_open`，滑点 0.1%，佣金 0.025%，印花 0.01%
- 无未来函数：引擎 `_slice_window_up_to` 保证 `market_window` 仅含 `date<=today`

## Files

- `D:\QMT_STRATEGIES\backtest\strategies\research\macd_strategy\strategy.py` — 策略主体
- `D:\QMT_STRATEGIES\backtest\configs\macd_astock_v2.yaml` — 沪深300 基准配置（推荐）
- `D:\QMT_STRATEGIES\backtest\scripts\build_macd_universe.py` — universe 生成
- `D:\QMT_STRATEGIES\backtest\data_tools\astock_reader.py` — 数据源修复（MultiIndex + hfq）
- `E:\astock\daily\stock_daily.parquet` — 主数据源
- `F:\backtest_workspace\data\duckdb\benchmark_index.duckdb` — 基准

## Verification

1. 语法检查 `strategy.py` / `build_macd_universe.py` 通过（py_compile）
2. duckdb 基准读取冒烟：`000300.SH` / `000852.SH` 各 808 行、零 NaN
3. 回测跑通：v1/v2/v2b 均无报错，信号正常触发，撮合正常
4. 契约核对：strategy dict 输出 / 引擎签名 / reader 返回 `date+open+close` 且 hfq 复权，逐一确认

## Notes

- C 盘满（21M）：venv 装 `F:\quant_venv`，`TMPDIR` 指向 `F:`，结果在 `F:\backtest_workspace\`
- `run_backtest.py` 第 23 行无条件 `import duckdb`，是硬依赖（已装 1.5.4）
- `stock_basic` 的 `exchange` 实际是 `SSE/SZSE`（非 `SH/SZ`）、`list_date` 是 `YYYYMMDD` 字符串，`build` 脚本已修正
- 回测工厂 v0.2 红线：绝对数字勿外推实盘，相对对比有效
