# MIMO 通宵作战任务书：v2.1 → v3.0 收益增强冲刺
> **签发时间**：2026-07-18 23:55
> **截止时间**：2026-07-19 08:00（共8小时，明早8点前必须全部交付）
> **签发人**：HERMES
> **执行人**：MIMO
> **核心授权**：通宵期间MIMO拥有完全决策权，遇到问题优先推进不需要等回复，卡壳超过30分钟直接跳过做下一个
> **数据源优势**：`E:\\astock\\daily\\stock_daily.parquet` 本地存储，单组回测实测仅需 **1.15分钟（69秒）**，支持大量并行跑数
> **总目标**：基线打牢 + 第一层12~18% + 第二层30~40% + 第三层50%框架就绪

---

## 🚨 启动清单（必须在 00:10 前完成，按顺序执行）
### 第0项：先修Python路径（否则第一步就卡死！）
在执行**任何代码前**，第一行必须是：
```python
import sys
import os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
```
如果还报错 `ModuleNotFoundError: No module named 'research'`，检查：
- `research/__init__.py` 是否存在
- `research/multi_factor_ic/__init__.py` 是否存在

### 第1-6项：按顺序执行
1. **通读全文档**（包括所有预检清单和API规范）
2. **运行预检清单所有验证项**（circ_mv单位、行业数据、字段名）
3. **构建一次基准Panel**：`codes = load_universe(); panel, fin_ffill = build_panel(codes)`，记录构建耗时
4. **单组耗时测试**：跑一次TOP20双月无止损回测，记录真实耗时，根据耗时调整并行数（**实测69秒/组，推荐8并行**）：
   - < 1.5分钟：开8并行，执行360组完整任务矩阵
   - 1.5~3分钟：开4并行，执行180组核心任务矩阵
   - > 3分钟：开2并行，执行54组简化矩阵
   - > 8分钟：串行，只跑最关键的前10组
5. **基线验证通过**：年化在 0.5%~3% 区间，输出结果到日志
6. **任何一步失败立即停止**，记录错误信息到 `error_log.csv`，30分钟无法修复则降级到只完成Phase 1

---

## ⚠️ 执行前必须完成的预检清单（10分钟，必须最先跑）
> **⚠️ 生死提示：跳过这一步，所有结果全错！**

1. **✅ circ_mv 单位校验**：circ_mv 单位是**万元**，不是元！
   - 20亿 = circ_mv > 2e5
   - 50亿 = circ_mv > 5e5
   - 80亿 = circ_mv > 8e5
   - 运行校验脚本：`df = pd.read_parquet('E:/astock/daily/stock_daily.parquet'); print(df.xs('2024-12-31', level='trade_date').loc['600519.SH', 'circ_mv'])` → 应该等于 ~1.91e+08（= 1.91万亿）

2. **✅ 行业数据加载**：
   - 路径：`E:/astock/basic/stock_basic.parquet`
   - 列名：`industry`（申万一级行业分类）
   - 构建映射：`industry_map = dict(zip(basic['ts_code'], basic['industry']))`

3. **✅ 中证1000指数计算**：
   - 无需外部数据，直接从日线数据计算
   - 取每日 circ_mv 前1000只股票，按市值加权计算指数收益率
   - 计算20日均线用于动态仓位择时

4. **✅ 单组基线验证**：
   - 运行一次 TOP20 双月无止损回测
   - 年化必须落在 **0.5%~3%** 区间
   - 确认 API 调用正确无误

---

## ⚠️ 环境与API规范（所有回测必须遵守）
1. **工作目录**：必须在 `D:/QMT_STRATEGIES/` 下运行，不能在 `multi_factor_ic/` 子目录
2. **Python路径配置**：运行前执行 `import sys; sys.path.insert(0, '.')` 确保模块导入正常
3. **统一导入方式**：`from research.multi_factor_ic.data_loader import build_panel, load_universe, get_rebalance_dates`
4. **Panel 只构建一次**：`codes = load_universe(); panel, fin_ffill = build_panel(codes)` → 耗时约21秒，所有回测复用这两个对象
   - ⚠️ 注：Phase 2不同市值区间测试需要分别重建Panel（每次~21秒），不是全程只构建一次
5. **无止损回测 API**：`backtest(panel, fin_ffill, top_n=20, freq="2M", tx_cost=0.002, dynamic_universe=True)`
   - 返回值：`(equity_df, trades_df, metrics_dict)` 3元组
6. **有止损回测 API**：`backtest_stop_loss(panel, fin_ffill, top_n=20, freq="2M", tx_cost=0.002, stop_loss=-0.12)`
   - 返回值：`(equity_df, trades_df, sl_events_df, metrics_dict)` 4元组
7. **字段名注意**：成交量字段是 `vol`，不是 `volume`

---

## ⚠️ 通宵铁律（违反任何一条直接判定不合格）
1. **绝对不许动v2基线**：所有新代码新结果全部放在 `research/multi_factor_ic/reports/v3_optimize/` 目录，`reports/*.csv` 原基线文件一个都不许碰
2. **年化>15%必须双重验证**：前两层优化任何组合年化超过15%，99%概率是前视Bug，必须：①换股票池重跑 ②换起止时间重跑 ③代码审计
3. **严格按优先级执行**：P0不做完不许碰P1，P1不做完不许碰P2，时间不够时优先保前面的
4. **每2小时存一次进度快照**：`progress_0200.md` / `progress_0400.md` / `progress_0600.md`，防止白干一整晚
5. **卡壳30分钟直接跳**：不要死磕一个问题一整晚，标注原因做下一个，早上统一解决

---

## 📋 任务优先级总览（按顺序执行）
| 阶段 | 内容 | 预计耗时 | 目标年化 | 优先级 | 必须完成 |
|------|------|---------|---------|--------|---------|
| **Phase 0** | 运行时自校准（测耗时 + 自动选并行数） | 3分钟 | - | 🔴 阻断 | ✅ 必须100%完成 |
| **Phase 1** | v2.1 P0验收小修（地基） | 1小时 | 0.5%~3% | 🔴 阻断 | ✅ 必须100%完成 |
| **Phase 2** | 第一层参数调优（360组全量正交测试） | 2.5小时 | 12%~18% | 🔴 核心 | ✅ 必须100%完成 |
| **Phase 2.5** | 鲁棒性验证（分行情区间 + 蒙特卡洛） | 1小时 | - | 🟡 核心 | ✅ 必须100%完成 |
| **Phase 3** | 第二层因子优化（换因子+中性化+过滤） | 1小时 | 30%~40% | 🟡 核心 | ✅ 必须100%完成 |
| **Phase 3.5** | 因子权重网格搜索（20+组合） | 0.5小时 | 提升2~3% | 🟢 高优 | ✅ 必须完成 |
| **Phase 4** | 第三层增强（择时+过滤+事件因子） | 1小时 | 35%~45% | 🟢 高优 | ✅ 必须完成 |
| **Phase 5** | 战报生成+代码整理 | 1小时 | - | 🟢 收尾 | ✅ 必须完成 |

**总耗时：8小时，刚好到明早8点**

---

## 🔴 Phase 1：v2.1 P0验收小修（1小时，必须00:55前完成）
### 任务1.1：执行价全部改为次日开盘价
**修改文件**：`backtest.py`
**共5处精准修改**（只改执行价，判断触发用的前一日`close`保留不动）：
| 行号（约） | 函数 | 当前代码 | 修改为 |
|-----------|------|---------|--------|
| ~123 | `backtest()` 普通回测入场 | `entry_close = panel.loc[entry_date, "close"]` | `panel.loc[entry_date, "open"]` |
| ~124 | `backtest()` 普通回测出场 | `exit_close = panel.loc[exit_date, "close"]` | `panel.loc[exit_date, "open"]` |
| ~107 | `backtest_stop_loss()` 止损卖出价 | `sp = day_close.get(code)` | 改为从 `panel.loc[trade_dates[trade_dates.index(day) + 1], "open"]` 取值 |
| ~131 | `backtest_stop_loss()` 替代股入场价 | `bp = day_close.get(cand)` | 改为从 `panel.loc[trade_dates[trade_dates.index(day) + 1], "open"]` 取值 |
| ~144 | `backtest_stop_loss()` 周期末出场价 | `exit_close = panel.loc[exit_date, "close"]` | `panel.loc[exit_date, "open"]` |

**验证脚本**（修改完必须跑）：
```python
import re
with open('research/multi_factor_ic/backtest.py', 'r', encoding='utf-8') as f:
    code = f.read()
# 确保成交价用的是open不是close
entry_close = [l for l in code.split('\n') if 'entry' in l.lower() and 'close' in l.lower() and '#' not in l]
exit_close = [l for l in code.split('\n') if 'exit' in l.lower() and 'close' in l.lower() and '#' not in l]
if entry_close or exit_close:
    print('❌ 仍有入场/出场使用close，检查：', entry_close + exit_close)
else:
    print('✅ 执行价已全部改为open')

# 验证基线回测
import time
from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest

codes = load_universe()
panel, fin_ffill = build_panel(codes)
print('开始基线回测...')
t0 = time.time()
eq, trades, metrics = backtest(panel, fin_ffill, top_n=20, freq="2M", tx_cost=0.002)
print(f'单组回测耗时: {time.time()-t0:.1f}秒')
print(f'基线年化: {metrics["年化收益"]}, 夏普: {metrics["夏普比率"]}, 最大回撤: {metrics["最大回撤"]}')
# 验证: 年化必须在 0.5%~3% 之间
```

### 任务1.2：修复HTML看板百分比渲染Bug
**问题**：CSV中 `3.1%` 字符串被二次×100后变成 `310%`
**修复要求**：看板生成代码检测数值是否包含 `%`，已含则直接显示，不再转换
**输出**：`reports/dashboard_v2.1.html`

### 任务1.3：v2.1基线验证
修改完成后，必须跑一次双月TOP20无止损基线回测：
- 年化必须落在 **0.5%~3%** 区间
- 最大回撤 -22%~-26%
- 输出：`reports/v3_optimize/v2_1_baseline.csv`

### 任务1.4：文档同步
更新v2评审文档中所有"次日收盘价"表述为"次日开盘价"

---

## 🔴 Phase 2：第一层参数调优（2.5小时，必须03:25前完成）
**核心思路**：不改因子逻辑，只改参数，全量正交测试54种组合，并行跑数
**测试矩阵（所有组合全覆盖，不许漏）**：

| 参数维度 | 档位数量 | 具体取值 |
|---------|---------|---------|
| 股票池市值区间 | 3档 | ①20~50亿（circ_mv 2e5~5e5） ②50~80亿（5e5~8e5） ③20~80亿（2e5~8e5） |
| 调仓频率 | 3档 | ①周度（5交易日） ②双周（10交易日） ③月度（22交易日） |
| 加权方式 | 2档 | ①等权 ②市值倒数加权（`weight = 1/circ_mv` 归一化） |
| 止损规则 | 3档 | ①无止损 ②-12%固定止损 ③2倍ATR自适应止损 |

**ATR计算方式**：`ATR = (max(high, close_prev) - min(low, close_prev))` 的14日均值

**输出要求**：
1. `param_matrix_all_54groups.csv`：所有组合的年化、夏普、最大回撤、换手率、卡玛比率
2. `param_matrix_top10.csv`：按卡玛比率排序前10的组合明细
3. 最优组合单独的回测明细CSV + HTML报告
4. 所有结果存放在 `reports/v3_optimize/`

**验收标准**：
- 54组回测全部完成无报错
- 所有组合年化 < 15%，无Bug级异常收益
- 前3名组合卡玛比率 > 0.3

---

## 🟡 Phase 3：第二层因子优化（2小时，必须05:25前完成）
### 任务3.1：低质量因子清除 + 高Alpha因子替换
**操作步骤**：
1. **砍掉ROE因子**（IC仅0.02，纯噪音），原20%权重释放出来
2. 在 `factors.py` 中新增3个高IC价量因子，每个权重7%，剩余1%给BP：
   - **量价一致度**：(收盘价-开盘价)与成交量的20日秩相关系数（预期IC≈0.08）
   - **60日真实波动率**：日内涨跌幅标准差的60日均值（预期IC≈0.07，低波因子增强版）
   - **20日资金净流入**：(收盘价-开盘价)×成交量 的20日均值（预期IC≈0.09）
3. 先单独跑3个新因子的IC序列验证，IC < 0.05的因子直接砍掉，权重分配给其他因子
4. 用新因子组合重跑TOP20 + Phase 2最优参数组合

**输出**：
- `new_factors_ic_validation.csv`：3个新因子的IC均值、ICIR、胜率
- `factor_optimized_result.csv`：因子替换后的回测结果

**验收标准**：
- 至少2个新因子IC > 0.05
- 因子替换后年化比v2.1基线高5pct以上

### 任务3.2：行业中性化
**操作步骤**：
1. 对因子得分做中证一级行业中性化处理：每个股票的因子得分减去其所属行业的因子得分均值
2. 中性化后再排序选股
3. 重跑Phase 2最优参数组合

**输出**：
- `industry_neutralize_result.csv`

**验收标准**：
- ICIR提升30%以上
- 年化不低于中性化前

---

## 🟡 Phase 4：第三层增强（1.5小时，必须06:55前完成）
### 任务4.1：动态仓位择时
**操作步骤**：
1. 新增中证1000指数20日均线动态仓位规则：
   - 指数站上20日均线 → 满仓100%
   - 指数跌破20日均线 → 仓位降为50%，剩余仓位按年化2%计息（现金等价）
2. 叠加到Phase 3最优组合上重跑

**输出**：
- `dynamic_position_result.csv`

**验收标准**：
- 最大回撤从24%降到15%以内
- 年化不低于基线

### 任务4.2：涨跌停/停牌过滤
**操作步骤**：
1. 入场时过滤：如果股票当日涨停（涨幅≥9.8%）或成交量=0（停牌），不买入
2. 出场时过滤：如果股票当日跌停（跌幅≥9.8%）或停牌，顺延到下一个可交易日出场
3. 叠加到Phase 3最优组合上重跑

**输出**：
- `filter_optimized_result.csv`

### 任务4.3：全增强叠加测试
将Phase 2最优参数 + Phase 3因子优化 + Phase 4择时过滤全部叠加，跑最终组合

**输出**：
- `final_combined_result.csv`
- `final_combined.html` 看板

**目标**：年化 ≥ 35%，最大回撤 ≤ 18%，卡玛比率 ≥ 2.0

---

## 🟢 Phase 5：战报生成+代码整理（1小时，必须07:55前完成）
### 任务5.1：通宵战报
输出 `通宵作战总结_20260719.md`，必须包含：
1. **各阶段完成情况**：哪些完成了，哪些跳过了，原因是什么
2. **核心结果对比表**：
   | 阶段 | 年化 | 夏普 | 最大回撤 | 换手率 | 卡玛比率 |
   |------|------|------|---------|--------|---------|
   | v2基线（无止损TOP20） | 1.8% | 0.07 | -24.1% | 100%/月 | 0.07 |
   | v2.1开盘价修正后 | | | | | |
   | Phase 2参数最优 | | | | | |
   | Phase 3因子最优 | | | | | |
   | Phase 4全增强 | | | | | |
   | 与50%目标的差距 | | | | | |
3. **Top 3组合详细参数**：明确给出能达到最高卡玛的参数组合
4. **存在的问题**：哪些地方有Bug，哪些回测看起来可疑
5. **今日（7.19）后续攻坚建议**：下一步做什么能到50%+

### 任务5.2：代码整理
1. 所有修改过的文件备份到 `code_backup_v3/` 目录
2. 生成 `changes_from_v2_to_v3.diff` 差异文件
3. 清理临时文件，保留最终结果

---

## 📦 明早8点必须交付的完整清单
| 序号 | 交付物 | 路径 | 优先级 |
|------|------|------|--------|
| 1 | v2.1基线回测结果 + 修正后代码 | `reports/v3_optimize/v2_1_baseline.csv` | P0 |
| 2 | 修复Bug后的v2.1看板 | `reports/dashboard_v2.1.html` | P0 |
| 3 | 54组参数矩阵全量结果 + TOP10排序表 | `reports/v3_optimize/param_matrix_*.csv` | P1 |
| 4 | 3个新因子IC验证报告 + 因子优化结果 | `reports/v3_optimize/factor_*.csv` | P2 |
| 5 | 行业中性化 + 动态仓位 + 过滤结果 | `reports/v3_optimize/*_result.csv` | P2 |
| 6 | 全增强最终组合结果 + HTML看板 | `reports/v3_optimize/final_combined.*` | P2 |
| 7 | 通宵作战总结报告 | `specs/通宵作战总结_20260719.md` | 必交 |
| 8 | 代码差异diff + 备份 | `code_backup_v3/` | 必交 |

---

## ⏱️ 通宵时间节点硬约束
| 时间点 | 必须完成的里程碑 |
|--------|----------------|
| **00:55** | Phase 1全部完成，v2.1基线跑通，P0验收通过 |
| **03:25** | Phase 2 54组参数矩阵全部跑完，TOP10排序输出 |
| **05:25** | Phase 3因子优化全部完成，3个新因子验证通过 |
| **06:55** | Phase 4择时过滤全部完成，全增强最终组合输出 |
| **07:55** | 战报写完，代码整理好，全部交付物就绪 |

---

## 🎯 明早诚哥醒来能看到的预期结果
| 阶段 | 预期年化 | 最大回撤 | 状态 |
|------|---------|---------|------|
| v2.1基线（开盘价修正） | 0.5%~3% | -23%~-25% | ✅ 打牢地基，可上实盘模拟 |
| +第一层参数优化 | 12%~18% | -22%~-26% | ✅ 无Alpha纯参数优化，收益翻10倍 |
| +第二层因子优化 | 30%~40% | -18%~-22% | ✅ 真Alpha显现，接近机构级水平 |
| +第三层择时过滤 | 35%~45% | -15%~-18% | ✅ 风控增强，收益再提5~10pct |
| 与50%目标差距 | 还差5~15pct | | ⏳ 留待T0系统解决 |

**诚哥明早8点起床直接看战报，所有结果都准备好了，不需要等任何东西**

🌙 晚安，明早见结果！
