# Hermes 评审：多因子选股策略

> 提交人: CC | 日期: 2026-07-17 | 状态: ⏳ 待评审

## 一、概述

基于 A 股中证 500+1000 的**多因子选股策略**，从零构建：

- **因子**: BP(30%) + 反转-1月(25%) + 低波60d(25%) + ROE(20%)
- **选股范围**: 中证 500 + 中证 1000（流通市值排名 301~1800）
- **调仓**: 月频月末，等权持仓 TOP 20
- **预过滤**: PE>0, PB>0, ROE>-20

## 二、评审文件清单

### 核心代码

| 文件 | 说明 |
|------|------|
| `research/multi_factor_ic/data_loader.py` | 面板数据加载（Parquet → MultiIndex Panel） |
| `research/multi_factor_ic/factors.py` | 11 个因子计算函数 + winsorize/standardize |
| `research/multi_factor_ic/scoring.py` | 多因子评分器（`MultiFactorScorer`） |
| `research/multi_factor_ic/ic_test.py` | Rank IC / ICIR / 分组收益框架 |
| `research/multi_factor_ic/backtest.py` | 月度调仓回测 + 行业中性化 + 频率对比 |
| `research/multi_factor_ic/timing.py` | 大盘择时增强（含 MA 均线系统） |

### 报告产出

| 文件 | 说明 |
|------|------|
| `research/multi_factor_ic/reports/ic_report.html` | 因子 IC 测试报告 |
| `research/multi_factor_ic/reports/ic_statistics.csv` | IC 统计汇总 |
| `research/multi_factor_ic/reports/ic_series.csv` | 逐月 IC 序列 |
| `research/multi_factor_ic/reports/group_returns.csv` | 分组收益 |
| `research/multi_factor_ic/reports/backtest_summary.csv` | TOP10/20/30 回测对比 |
| `research/multi_factor_ic/reports/scorer_ic_series.csv` | 综合评分器 IC 序列 |
| `research/multi_factor_ic/reports/industry_neutralize_compare.csv` | 行业中性化对比 |
| `research/multi_factor_ic/reports/freq_comparison.csv` | 调仓频率对比 |

### 汇总文档

| 文件 | 说明 |
|------|------|
| `research/multi_factor_ic/KNOWLEDGE.md` | 研究知识库（全面结论） |
| `research/multi_factor_ic/config.py` | 全部可配置参数 |

## 三、关键数字速查

| 指标 | 值 |
|------|-----|
| 单因子最强 | BP: IC=0.064, ICIR=0.46 |
| 综合评分 IC | **0.104** |
| 综合评分 ICIR | **0.65** |
| IC>0 占比 | **75%** |
| 月频 TOP20 年化 | **8.4%**（含基本面过滤） |
| 最大回撤 | -25.2% |
| 夏普比率 | 0.35 |
| 月频调仓次数 | 95（2018-2026） |
| 数据源 | `E:\astock\` Parquet（买断离线数据） |

## 四、评审要点

### 需要验证的事项

1. **未来函数风险** — 因子计算是否存在 look-ahead：
   - 动量/波动率/换手率：使用过去数据，无未来函数 ✅
   - 财务数据 ROE：使用 `ffill` 前向填充，不会用到未来财报 ✅
   - 日线数据：使用 `close` 为当日收盘价（调仓日假设收盘买入）⚠️ 需要确认是否该用次日开盘

2. **回测口径** — 按收盘价计算，未考虑：
   - 交易佣金/印花税
   - 冲击成本
   - 涨停无法买入 / 跌停无法卖出
   - 停牌恢复后的跳空

3. **幸存者偏差** — 使用的是当前成分股数据还是历史成分股？
   - 当前实现使用所有历史数据的 Parquet，成分股未做历史回溯 ❗可能需要处理

4. **季度回测 27.9% 异常** — 仅 32 次调仓，统计意义不足，建议忽略

### 待决策问题

- [ ] 是否启动 Phase 0：QMT 集成（模拟盘验证）
- [ ] 优先级 vs 现有策略（6+2 评分器、黄氏主升浪）
- [ ] 是否与现有 `core/scoring/` 合并，还是独立策略运行
- [ ] 实盘是否需要加入止损/风控模块

## 五、运行入口

```bash
# 完整流水线（IC测试 + 评分验证 + 回测）
cd D:\QMT_STRATEGIES
python research/multi_factor_ic/run.py

# 行业中性化对比
python research/multi_factor_ic/run_indneu.py

# 调仓频率对比
python research/multi_factor_ic/run_final.py

# 单期选股
python -c "
from research.multi_factor_ic.scoring import top_picks
from research.multi_factor_ic.data_loader import load_universe, build_panel
u = load_universe()
p, f = build_panel(u)
top_picks(p, f, '2026-06-18', n=20)
"
```
