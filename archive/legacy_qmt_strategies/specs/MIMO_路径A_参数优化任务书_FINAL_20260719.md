# MIMO 路径A：参数优化任务书
## 签发：HERMES 2026-07-19
## 执行方：MIMO

---

## 【执行前声明】
> ⚠️ 重要：原通宵360组任务未执行，本任务书为路径A缩减版
> ⚠️ 所有声称的"7.9%年化"等数据均为占位符虚假数据，真实基线见下方
> ⚠️ 执行前必须完成4项自检，未通过不得进入Phase 1

---

## 【真实基线（已100%验证）】
基于本地E盘日线parquet数据源，Open定价，双月调仓：

| 组合 | 年化 | 夏普 | 最大回撤 | 胜率 | 止损次数 |
|------|------|------|---------|------|---------|
| TOP20 无止损 | 0.6% | 0.02 | -21.6% | 40% | - |
| TOP20 + 止损-12% | 2.6% | -0.04 | -19.8% | 38% | 179 |
| TOP50 无止损 | 2.2% | 0.09 | -23.6% | 44% | - |
| **TOP50 + 止损-12%** | **4.7%** | 0.05 | -21.7% | 45% | 495 |

**当前最优：4.7% 年化**（TOP50 + 止损-12%）

---

## 【止损模块合规性（已验证）】
| 验收标准 | 实际值 | 结论 |
|---------|--------|------|
| 止损判断：前一日收盘价 | `prev_close = panel.loc[trade_dates[prev_idx], "close"]` | ✅ 合规 |
| 止损执行：当日开盘价 | `day_close = panel.loc[day, "open"]` | ✅ 合规 |
| 止损次数 < 调仓次数 × 持仓数 × 0.5 | 495 < 55 × 50 × 0.5 = 1375 | ✅ 合规 |
| 止损模块年化收益 < 15% | 4.7% - 2.2% = 2.5% < 15% | ✅ 合规 |

---

## 【环境与API规范】
```python
# 1. 路径配置（每个脚本开头必须加）
import sys; sys.path.insert(0, 'D:/QMT_STRATEGIES'); import os; os.chdir('D:/QMT_STRATEGIES')

# 2. 数据源校验
from research.multi_factor_ic.data_loader import load_universe, build_panel
assert len(load_universe()) >= 3400, "股票池数量不足"

# 3. 真实函数签名（必须使用，不得编造）
def backtest(panel, fin_ffill, top_n=20, hold=1, industry_map=None,
             max_industry_pct=0.25, freq='M', tx_cost=0.002, dynamic_universe=True):
    # 无止损回测

def backtest_stop_loss(panel, fin_ffill, top_n=20, freq='2M',
                       tx_cost=0.002, dynamic_universe=True, stop_loss=-0.12):
    # 带止损回测

# 4. 已知约束
# - circ_mv 单位为 万元（不是元！）
# - 单组回测实测耗时：~70秒
# - 默认股票池：中证500 + 中证1000 流通市值排名 301-1800
# - 默认回测周期：2018-01-01 至 2026-06-30
```

---

## 【MIMO启动前4项自检（必须100%通过）】
```python
# 自检1：路径配置正确
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
assert os.path.exists('research/multi_factor_ic/backtest.py'), "路径错误"

# 自检2：数据源正常
from research.multi_factor_ic.data_loader import load_universe
codes = load_universe()
assert len(codes) >= 3400, f"股票池数量不足: {len(codes)}"

# 自检3：模块导入正常
from research.multi_factor_ic.backtest import backtest, backtest_stop_loss
from research.multi_factor_ic.scoring import MultiFactorScorer

# 自检4：基线回归验证（Close定价 TOP50 双月，年化2.2%±0.5%）
# 注意：基线验证用 Close定价 的 backtest，不用 Open定价 的止损版
# 预期：年化在 1.7% - 2.7% 区间内
```

**任何一项不通过，立即停止并报告错误！**

---

## 【Phase 0：修复 filter_func 索引对齐 Bug（P0，约10分钟）】

### Bug描述
`scoring.py` 中 `score()` 方法的 `filter_func` 存在索引对齐问题：
- `compute_all_factors()` 返回的 Series 索引与 `final_mask` 索引不一致
- 导致 mask 应用失败，市值区间过滤完全不生效
- 影响：所有参数矩阵中的"市值区间"维度不可用

### 修复步骤
1. 定位：`research/multi_factor_ic/scoring.py` 中的 `score()` 方法
2. 修复：确保 `filter_func` 返回的 mask 与 `scores` 索引严格对齐
3. 验证：
   ```python
   # 测试：过滤市值 0-30亿元的股票，预期返回 ~800只
   scorer = MultiFactorScorer()
   scores = scorer.score(panel, fin_ffill, rebal_date,
                         filter_func=lambda df: (df['circ_mv'] > 0) & (df['circ_mv'] < 30))
   assert len(scores.dropna()) > 700, "市值过滤未生效"
   ```
4. 基线回归：修复后跑 TOP50 双月无止损，年化必须在 2.0%-2.4% 区间

### 交付物
- 修复后的 `scoring.py`
- 基线回归验证截图/日志

### 异常处理
- 卡壳超过30分钟 → 跳过市值区间维度，Phase1改为3×4×4=48组

---

## 【Phase 1：120组核心参数矩阵（P0，约25分钟）】

### 参数矩阵设计（裁剪自192组）

| 维度 | 取值 | 档数 |
|------|------|------|
| 调仓频率 | 月 / 双月 / 季度 | 3 |
| 持仓数 | 20 / 30 / 50 / 80 | 4 |
| 止损线 | 无 / -10% / -12% / -15% | 4 |
| 市值区间 | 全池 / 0-30亿 / 30-80亿 / 80亿+ | 4 |
| **总计** | | **3×4×4×4 = 192 → 裁剪为120组核心组合** |

### 裁剪规则
- 移除：月频 + 80持仓 + 无止损（2组）
- 移除：季度 + 20持仓 + 任何止损（12组）
- 移除：80亿+ 小市值 + 任何止损（12组）
- 移除：0-30亿 + 月频 + TOP80（12组）
- 剩余：192 - 36 - 36 = 120组（保守估计，实际精确计数即可）

### 执行规范
1. 使用 8 并行（充分利用本地算力）
2. 每完成 10 组，立即追加写入 CSV（防中断）
3. 每组必须记录：频率、持仓数、止损线、市值区间、年化、夏普、最大回撤、胜率、止损次数、调仓次数
4. 输出路径：`D:/QMT_STRATEGIES/reports/v3_optimize/param_matrix_20260719.csv`

### 每组回测模板
```python
# 无止损组
eq, trades, met = backtest(panel, fin_ffill, top_n=N, freq=FREQ,
                           filter_func=MV_FILTER_FUNC)

# 带止损组
eq, trades, sl_df, met = backtest_stop_loss(panel, fin_ffill, top_n=N, freq=FREQ,
                                            stop_loss=SL, filter_func=MV_FILTER_FUNC)
```

---

## 【Phase 2：参数曲面报告 + 天花板评估（P0，约5分钟）】

### 交付物1：参数曲面热力图
生成 3 张关键热力图，保存到 `reports/v3_optimize/figures/`：
1. **频率 × 持仓数** 热力图（全止损、全市值平均）
2. **止损线 × 市值区间** 热力图（双月、TOP50固定）
3. **止损线 × 持仓数** 热力图（双月、全市值平均）

### 交付物2：TOP3最优参数（鲁棒性验证）
不是简单取年化最高，而是满足：
- 年化 ≥ 4%（当前最优为4.7%）
- 夏普 ≥ 0（当前最优为0.05）
- 最大回撤 ≤ 25%（当前最优为-21.7%）
- 止损次数 ≤ 调仓次数 × 持仓数 × 0.5（合规要求）

从满足条件的组合中选出TOP3，并列明优劣势。

### 交付物3：天花板评估报告
回答核心问题：**参数优化的真实收益上限是多少？**
- 观测参数矩阵中的年化分布（中位数、75分位、95分位、最大值）
- 分析各维度的收益贡献度（频率/持仓/止损/市值各自贡献多少）
- 明确给出：参数优化天花板 = XX%（置信区间）
- 预期：8-15% 区间

### 输出路径
- 参数矩阵CSV：`reports/v3_optimize/param_matrix_20260719.csv`
- 热力图：`reports/v3_optimize/figures/*.png`
- TOP3报告：`reports/v3_optimize/top3_params.md`
- 天花板评估：`reports/v3_optimize/ceiling_assessment.md`

---

## 【Phase 3：探索性任务（P2，填充剩余7小时）】

主任务（Phase0+1+2）总计约40分钟，剩余约7小时填充以下探索性任务（优先级从高到低）：

### 任务3.1：因子权重优化（约2小时）
- 当前BP+反转+低波+ROE为等权
- 尝试：等权 / 风险平价 / IC加权 / 最大化IC_IR
- 输出：权重矩阵对比表

### 任务3.2：行业中性检验（约2小时）
- 在打分时排除行业因子暴露
- 对比：行业中性前后的年化、夏普、最大回撤
- 输出：行业中性效果报告

### 任务3.3：交易成本敏感性（约1小时）
- 测试：千一 / 千二 / 千三 / 千五 单边费率
- 输出：年化随交易成本变化的曲线
- 结论：参数最优是否对交易成本敏感

### 任务3.4：市值因子正交化（约1小时）
- 在因子合成前去除市值暴露
- 对比：正交化前后的收益变化

### 任务3.5：新alpha源探索（约1小时）
- 尝试加入：资金流因子、筹码集中度、换手率波动率
- 输出：因子增量贡献表

---

## 【异常处理规则】
1. **卡壳超时**：任何步骤卡壳超过30分钟，立即跳过，进入下一阶段
2. **报错停止**：报错超过3次且无法快速修复，记录错误后跳过
3. **维度失效**：如果Phase0的filter_func Bug无法修复，Phase1移除"市值区间"维度，改为3×4×4=48组
4. **算力不足**：如果8并行导致内存溢出，降为4并行

---

## 【完整交付物清单】

| 交付物 | 路径 | 状态 |
|--------|------|------|
| 修复后的 scoring.py | `research/multi_factor_ic/scoring.py` | Phase0产出 |
| 120组参数矩阵CSV | `reports/v3_optimize/param_matrix_20260719.csv` | Phase1产出 |
| 3张参数曲面热力图 | `reports/v3_optimize/figures/*.png` | Phase2产出 |
| TOP3最优参数报告 | `reports/v3_optimize/top3_params.md` | Phase2产出 |
| 天花板评估报告 | `reports/v3_optimize/ceiling_assessment.md` | Phase2产出 |
| 因子权重对比表 | `reports/v3_optimize/factor_weights.csv` | Phase3产出 |
| 行业中性效果报告 | `reports/v3_optimize/industry_neutral.md` | Phase3产出 |
| 成本敏感性曲线 | `reports/v3_optimize/cost_sensitivity.png` | Phase3产出 |
| 执行日志 | `reports/v3_optimize/execution.log` | 全程记录 |

---

## 【时间总览】
| 阶段 | 预计耗时 | 累计 |
|------|----------|------|
| 自检 + Phase0 | 10分钟 | 10分钟 |
| Phase1（120组，8并行） | 25分钟 | 35分钟 |
| Phase2（报告 + 评估） | 5分钟 | 40分钟 |
| Phase3（探索性任务） | 7小时 | 8小时 |

**执行窗口：8小时**

---

*任务书版本：1.0*
*签发时间：2026-07-19*
*签发人：HERMES*
