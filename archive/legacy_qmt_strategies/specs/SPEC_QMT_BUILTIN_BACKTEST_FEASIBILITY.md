# SPEC: 验证 QMT 内置回测系统接入可行性

## Objective

### 目标

验证能否通过 `xtquant.qmttools.stgentry.run_file()` 在外部 Python 脚本中调用 QMT 内置回测引擎，并与自建回测工厂的结果做交叉对比。

### 背景

诚哥的量化团队在 `D:\QMT_STRATEGIES` 下开发 QMT 策略，自建了回测工厂（`scripts/run_backtest.py`）。两路员工（研究分析 + 工程架构）研究了 QMT 内置回测系统，发现：

- `xtquant.qmttools` 包内含回测入口 `stgentry.run_file()`，可在外部 Python 中触发回测
- 但前提是 MiniQMT 进程必须在后台运行（回测引擎通过 RPC 连接 QMT 进程）
- 现有 `strategy_main.py` 的 `init(C) → handlebar(C)` 结构与 QMT 回测生命周期匹配
- 全天版时间守卫（09:24/10:00/13:30/14:30）在日线回测中可能失效

### 需要验证的核心问题

1. **环境可行性**：`stgentry.run_file()` 在现有环境中能否成功调用？MiniQMT 是否必须启动？
2. **策略兼容性**：现有 `strategy_main.py` 能否直接跑 QMT 内置回测？是否需要改造？
3. **结果导出**：能否通过 `deal_callback` 等回调将回测结果（逐笔交易、净值曲线）写入文件？
4. **数据一致性**：QMT 回测结果与自建回测结果对比，偏差有多大？偏差来源是什么？
5. **多标的支持**：能否循环调用 `run_file()` 跑多只股票？会话状态是否会残留？
6. **参数注入**：能否通过 `param` 字典传入初始资金、滑点、手续费、时间区间等参数？

### 验收标准

1. ✅ 成功调用 `stgentry.run_file()` 完成一次回测，拿到结果
2. ✅ 输出 JSON 文件，包含：逐笔交易明细、每日净值序列、汇总绩效指标
3. ✅ 与自建回测（`scripts/run_backtest.py`）跑相同参数，输出对比报告
4. ✅ 明确标注：已验证事实 / 合理假设 / 待进一步验证

---

## Commands

### 工作目录

```bash
D:/QMT_STRATEGIES
```

### 依赖检查

```bash
python -c "from xtquant.qmttools import stgentry; print('stgentry OK')"
python -c "from xtquant import xtdata; print('xtdata OK')"
```

### 建议脚本结构

```bash
# 1. 验证 stgentry.run_file() 基本调用
python research/qmt_builtin_backtest/01_verify_run_file.py

# 2. 单标的回测结果导出（含 deal_callback 输出）
python research/qmt_builtin_backtest/02_single_stock_backtest.py

# 3. 多标的循环回测
python research/qmt_builtin_backtest/03_multi_stock_backtest.py

# 4. 与自建回测对比
python research/qmt_builtin_backtest/04_compare_with_self_built.py

# 5. 生成报告
python research/qmt_builtin_backtest/05_generate_report.py
```

### 输出目录

```text
D:\QMT_STRATEGIES\agent_hub\qmt_builtin_backtest_feasibility\
  data\
    qmt_backtest_trades.json           # QMT 回测逐笔交易
    qmt_backtest_nav.csv               # QMT 回测每日净值
    self_built_trades.json             # 自建回测逐笔交易
    self_built_nav.csv                 # 自建回测每日净值
  reports\
    01_environment_verification.md     # 环境可行性验证
    02_single_stock_backtest.md        # 单标的结果
    03_multi_stock_backtest.md         # 多标的循环结果
    04_comparison_report.md            # 与自建回测对比
    05_conclusion.md                   # 最终结论与建议
```

---

## Structure

### 模块1：环境可行性验证（01_verify_run_file.py）

验证 `stgentry.run_file()` 能否在现有环境中成功调用。

```python
from xtquant.qmttools import stgentry

param = {
    'stock_code': '000001.SZ',
    'period': '1d',
    'start_time': '20240101',
    'end_time': '20240331',
    'trade_mode': 'backtest',
    'asset': 1000000,
    'dividend_type': 'front',
}

result = stgentry.run_file('D:/QMT_STRATEGIES/strategy_main.py', param)
```

**需要回答的问题：**
- MiniQMT 是否必须启动？不启动会报什么错？
- `run_file()` 返回什么？是回测结果对象还是 None？
- 调用后 QMT 进程状态是否有变化？
- 是否需要修改 `strategy_main.py` 才能跑回测？

### 模块2：单标的回测结果导出（02_single_stock_backtest.py）

通过回调函数收集回测结果并写入文件。

**需要实现：**
- `deal_callback(deal_info)` — 每笔成交写入 JSON
- `order_callback(order_info)` — 委托记录
- `account_callback(account_info)` — 账户净值序列
- 在 `handlebar` 末尾通过 `C.paint('nav', total_asset)` 记录每日净值
- 回测结束后将以上数据写入 `data/` 目录

**输出字段要求（逐笔交易）：**

```json
{
  "trades": [
    {
      "no": 1,
      "code": "000001.SZ",
      "entry_date": "20240105",
      "exit_date": "20240110",
      "direction": "buy",
      "volume": 1000,
      "price": 10.5,
      "pnl": 500.0,
      "return_pct": 4.76
    }
  ],
  "nav": [
    {"date": "20240101", "nav": 1000000.0},
    {"date": "20240102", "nav": 1000500.0}
  ],
  "summary": {
    "total_return": 0.0368,
    "annualized_return": 0.1637,
    "max_drawdown": -0.0448,
    "sharpe_ratio": 0.8285,
    "total_trades": 78,
    "win_rate": 0.5385
  }
}
```

### 模块3：多标的循环回测（03_multi_stock_backtest.py）

循环调用 `run_file()` 跑多只股票。

**测试标的：** 算力池 32 只代表股中的前 5 只

**需要验证：**
- 连续调用 `run_file()` 后，QMT 会话状态是否会残留？
- 是否需要每次调用前重置什么？
- 5 只股票串行跑完的总耗时

### 模块4：与自建回测对比（04_compare_with_self_built.py）

用相同参数（相同股票、相同时间区间、相同初始资金）在自建回测中跑一次，对比结果。

**对比维度：**

| 指标 | QMT 回测 | 自建回测 | 偏差 |
|------|:--------:|:--------:|:---:|
| 总收益率 | x% | y% | x-y |
| 最大回撤 | x% | y% | x-y |
| 夏普比率 | x | y | x-y |
| 交易次数 | N | M | N-M |
| 胜率 | x% | y% | x-y |

**偏差分析：**
- 偏差 < 1% → 一致性良好
- 偏差 1-5% → 可接受，需说明来源
- 偏差 > 5% → 需要深入调查根因

### 模块5：生成报告（05_generate_report.py）

汇总以上所有结果，生成 markdown 报告。

---

## Code Style

- 本任务是研究验证，不是 QMT 交易策略
- 优先 UTF-8 编码
- 不修改现有 `strategy_main.py`、`strategy_allday.py`、`release/` 等生产文件
- 不调用 `passorder`（仅在 QMT 回测内部由引擎调用）
- 所有阈值参数化，不硬编码
- 报告中必须区分：已验证事实 / 合理假设 / 待进一步验证

---

## Testing

### 必测场景

1. **环境探活**：不启动 MiniQMT 时调用 `run_file()` 的报错信息
2. **单标的基本回测**：000001.SZ，2024-01-01 至 2024-03-31，日线
3. **回调函数触发**：确认 `deal_callback` 在回测中是否真实被调用
4. **结果文件完整性**：JSON 文件格式正确，字段完整
5. **多标的隔离性**：连续跑 5 只股票，确认每只结果独立
6. **与自建回测对比**：相同参数下跑自建回测，输出对比表

### 验收报告必须包含

```text
- 环境可行性验证结果（MiniQMT 是否必须）
- 单标的结果（trades + nav + summary）
- 多标的循环结果（5只股票，总耗时）
- 与自建回测的对比表
- 6个核心问题的回答（成立/不成立/需更多数据）
- 是否建议进入 Phase 2（补齐自建工厂差距）
```

---

## Boundaries

### Always
- 使用前复权数据（`dividend_type='front'`）
- 所有阈值参数化
- 报告必须明确标注局限性
- 所有产物放 D 盘项目目录
- 不修改现有生产策略文件

### Never
- 不接 QMT 下单（不修改 `passorder` 调用）
- 不修改生产策略源码
- 不把验证结果包装成实盘建议
- 不伪造对比结论
- 不删除已有研究产物

---

## 交付判定

### APPROVED 条件
1. `stgentry.run_file()` 成功调用并返回结果
2. 单标的结果文件完整（trades + nav + summary）
3. 多标的循环验证完成
4. 与自建回测对比报告完成
5. 6个核心问题全部回答
6. 报告完整标注局限性

### REJECT 条件
1. MiniQMT 无法启动导致回测不可用
2. `run_file()` 调用后无法获取逐笔交易数据
3. 对比报告缺失或数据不完整
4. 修改现有 QMT 交易策略或接入下单
