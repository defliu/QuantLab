# 多因子IC策略 — 回测运行说明

> 供其他模型独立复现回测使用。版本: v2（含 Hermes 评审全部修正）

---

## 一、数据依赖

| 文件 | 路径 | 说明 |
|------|------|------|
| 日线行情 | `E:/astock/daily/stock_daily.parquet` | 全A股日线，含 close/pe_ttm/pb/circ_mv/is_st/suspend_type 等 |
| 财务指标 | `E:/astock/finance/fina_indicator.parquet` | 季度频率，含 roe/grossprofit_margin 等 |
| 基础信息 | `E:/astock/basic/stock_basic.parquet` | 行业分类等 |

**注意**: parquet 中 `circ_mv` 单位为**万元**，不是元。回测引擎已做相应处理。

---

## 二、项目结构

```
research/multi_factor_ic/
├── config.py          # 路径/参数配置
├── data_loader.py     # 数据加载 + 动态universe
├── factors.py         # 因子计算(11个因子)
├── ic_test.py         # IC测试框架
├── scoring.py         # 多因子评分器
├── backtest.py        # 回测引擎 + 止损替换
├── run.py             # 全流程入口
├── KNOWLEDGE.md       # 最新结论
└── reports/           # 输出目录
```

---

## 三、核心改动说明（和原始版本比）

### P0修正 4项

| 修正 | 文件位置 | 修改内容 |
|------|---------|---------|
| 动态universe | `data_loader.py:35-49` | `get_universe_at_date()` 按每期市值排名动态选股 |
| 因子前视 | `factors.py:64-98` | 动量/波动率等窗口因子排除当日数据，使用 `date-1` |
| 交易成本 | `backtest.py` | `tx_cost=0.002` 单边佣金+印花税+滑点 |
| circ_mv单位 | `scoring.py` | 条件为 `circ_mv > 5e4`（5亿元），因数据单位是万元 |

### Hermes v2 补充修正

| 修正 | 文件位置 | 修改内容 |
|------|---------|---------|
| 财报滞后45天 | `factors.py:52` , `scoring.py` | 财务查询 `date - 45天` 而非 `date` |
| 次日收盘价成交 | `backtest.py` 入场/出场 | 评分日`rebal_date`→隔日 `trade_dates[idx+1]` 成交 |
| 止损重写 | `backtest.py:backtest_stop_loss()` | 前日D-1收盘判断→D日执行; 现金结算; 排除自替换 |

---

## 四、运行步骤

### 4.1 全流程跑一遍

```python
from research.multi_factor_ic.run import main
main()
```

依次执行：加载数据 → IC测试 → 评分器IC验证 → 组合回测(TOP10/20/30)。

### 4.2 单独跑基线回测

```python
from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest

universe = load_universe()
panel, fin_ffill = build_panel(universe)

for top_n in [10, 20, 30]:
    eq, td, met = backtest(panel, fin_ffill, top_n=top_n, freq="2M",
                           tx_cost=0.002, dynamic_universe=True)
    print(met)
```

### 4.3 跑频率对比

```python
from research.multi_factor_ic.backtest import compare_frequencies
compare_frequencies(panel, fin_ffill, top_n=20, tx_cost=0.002, dynamic_universe=True)
```

### 4.4 跑止损对比

```python
from research.multi_factor_ic.backtest import compare_stop_loss
compare_stop_loss(panel, fin_ffill, top_n=20, freq="2M", tx_cost=0.002, dynamic_universe=True)
```

### 4.5 IC 测试

```python
from research.multi_factor_ic.ic_test import run_ic_test, generate_report
basic_df = pd.read_parquet("E:/astock/basic/stock_basic.parquet")
ic_df = run_ic_test(panel, fin_ffill, basic_df)
stats_df = generate_report(ic_df)
```

### 4.6 评分器 IC 验证

```python
from research.multi_factor_ic.scoring import verify_scorer_ic
verify_scorer_ic(panel, fin_ffill)
```

---

## 五、函数签名说明

### `backtest(panel, fin_ffill, top_n=20, hold=1, industry_map=None, max_industry_pct=0.25, freq="2M", tx_cost=0.002, dynamic_universe=True)`

| 参数 | 默认 | 说明 |
|------|------|------|
| panel | — | 日线面板 (MultiIndex: trade_date, ts_code) |
| fin_ffill | — | 财务数据 (index=date, columns=MultiIndex: variable, ts_code) |
| top_n | 20 | 每期持仓数 |
| freq | "2M" | 调仓频率: "W"/"2W"/"M"/"2M"/"Q" |
| tx_cost | 0.002 | 单边交易成本 |
| dynamic_universe | True | 是否启用动态滚动universe |

返回: `(equity_df, trades_df, metrics_dict)`

### `backtest_stop_loss(panel, fin_ffill, top_n=20, freq="2M", tx_cost=0.002, dynamic_universe=True, stop_loss=-0.12, filter_func=None, weights=None, start_date=None, end_date=None, max_weight_per_stock=None)`

额外参数:

| 参数 | 默认 | 说明 |
|------|------|------|
| stop_loss | -0.12 | 止损线，-0.12=-12% |
| filter_func | None | 自定义过滤 `callable(panel, fin_ffill, date) -> mask` |
| weights | None | 自定义因子权重 dict（默认 FACTOR_WEIGHTS） |
| start_date | None | 区间起始（`pd.Timestamp` 或 str），用于分区间验证 |
| end_date | None | 区间结束，禁止未来数据泄漏 |
| max_weight_per_stock | None | 单票仓位上限（如 0.02=2%），实盘分散约束 |

返回: `(equity_df, trades_df, sl_events_df, metrics_dict)`

**注意**：`backtest()` 同样支持 `filter_func`/`weights`/`start_date`/`end_date` 参数。

### 4.7 10万本金实盘参数回测

```python
from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest_stop_loss

universe = load_universe()
panel, fin_ffill = build_panel(universe)

# 成交额阈值过滤（amount单位=千元，2000万=20000千元）
def liq_filter(min_amount_k=20000):
    def _f(p, f, d):
        mv_mask = (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)
        tds = sorted(p.index.get_level_values('trade_date').unique())
        di = tds.index(d)
        if di < 20:
            return mv_mask
        window = p.loc[tds[di-20]:d]
        avg_amt = window.groupby('ts_code')['amount'].mean()
        liq = avg_amt > min_amount_k
        return mv_mask & liq.reindex(p.loc[d].index, fill_value=False)
    return _f

eq, td, sl, met = backtest_stop_loss(
    panel, fin_ffill, top_n=80, freq="2M", tx_cost=0.0015,
    dynamic_universe=True, stop_loss=-0.12,
    filter_func=liq_filter(20000), max_weight_per_stock=0.02)
# 结果：年化17.1%，夏普0.55，回撤-19.8%，10万→25.8万
```

详见 `specs/IC策略_10万本金实盘参数回测.md`。

---

## 六、验收标准

跑完回测后检查：

| 指标 | 期望值 | 说明 |
|------|--------|------|
| 评分器IC均值 | ~0.09 | 因子组合方向预测能力 |
| ICIR | ~0.65 | IC稳定性 |
| IC>0占比 | ~75% | 方向一致性 |
| 双月TOP20年化 | **1.5%~5%** | 合理区间 |
| 止损-12%年化 | < 15%（~3.5%） | 带止损的上限 |
| 止损-8%触发次数 | < 570 (57×20×0.5) | 合理性检查 |

如果年化 > 10%（不含止损），说明某处存在前视偏差未修复。

---

## 七、常见问题

**Q: 回测结果和预期不符？**
A: 检查：① `circ_mv` 单位是否为万元 ② `factors.py` 中动量因子是否用 `prev_close` ③ `scoring.py` 中 `circ_mv > 5e4` ④ `backtest.py` 中入场价是否为次日收盘

**Q: 止损-8%/ -12%/ -16% 结果完全一致？**
A: 说明 `backtest_stop_loss` 中有 Bug：替换票的 `entry_prices` 可能用了卖出价而非替代票买入价，或替换时未排除刚卖出的同票。

**Q: 数据路径不对？**
A: 修改 `config.py` 中的 `DATA_DIR`。

**Q: 其他指标（频率对比等）有前视偏差吗？**
A: 频率对比函数 `compare_frequencies()` 仍使用 v1 引擎（无次日成交+财报滞后），仅做**频率间相对比较**，不承诺绝对值的正确性。需要精确绝对值请用 `backtest()` 单独跑。
