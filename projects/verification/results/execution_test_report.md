# 回测引擎修复专项单测报告

## 结果: 5/5 通过

| 测试项 | 结果 | 说明 |
|---|---|---|
| E-1 容量约束超限拒绝 | PASS | reason=capacity_exceeded |
| E-2 容量约束默认关闭 | PASS | vol=19900 |
| E-3 win_rate 计入未平仓 | PASS | win_rate=1.000 n_open=1 |
| E-4 日期缺失兜底 | PASS | fallback=3 |
| E-5 涨跌停/停牌/整数手回归 | PASS | limit=True suspended=True lot=True pl=True |
