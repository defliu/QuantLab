# 趋势/均线/量能 研究底座

> 本目录是 `D:\QMT_STRATEGIES` 通宵研究（2026-08-11）在 `D:\QuantLab` 的研究底座镜像。
> 来源脚本与报告仍保留在 `D:\QMT_STRATEGIES\scripts\overnight_research\` 与 `D:\QMT_STRATEGIES\docs\`；此处为可复用的研究资产快照。

## 一、这个底座回答的问题

除因子策略（ATR 低波）外，**趋势策略、均线、量能是否还有研究价值、能否落地？**

## 二、结论速览

1. **趋势/均线/量能当独立选股策略：基本不可能**——动量类 IC 在 1/5/20 日全为负，组合回测全部负超额（与项目海龟/双均线/MACD 实证一致）。
2. **趋势择时门控（000300>MA60）是 A 股里趋势唯一可靠、稳健的用法**——作为选股策略的风控/择时过滤层，跨 5 年/13 年、多池、多参数一致把回撤砍半、IR 大幅改善。
3. **量能/换手过滤是有效正因子**（turnover_1to8/2to10），可作叠加层。
4. **但单靠"低波+门控"不足以稳定盈利**（PIT 全市场 13 年约打平 -0.90%），定位为防御型压舱石；盈利需叠加更强 alpha。

## 三、目录结构

```
趋势均线量能研究底座/
├── README.md          本说明
├── docs/              结论报告（主报告 + o04/o05/o06 三个附录 + 最终总结 SUMMARY）
├── scripts/           o01~o06 研究脚本 + run_overnight.bat
└── results/           o01~o06 的 JSON/CSV 结果 + 运行日志
```

## 四、脚本说明

| 脚本 | 内容 | 用途 |
|---|---|---|
| o01_factor_attribution.py | 30+ 因子前向收益归因 + 牛熊拆分 | 有没有 alpha |
| o02_portfolio_backtest.py | 5 策略统一引擎组合回测 | 策略对比 |
| o03_report.py | 读取 o01/o02 生成 Markdown 报告 | 报告自动化 |
| o04_robustness.py | 门控/股票池/参数 稳健性网格 | 结论是否稳健 |
| o05_10yr.py | 13 年长历史（静态池） | 跨牛熊 |
| o06_pit.py | 13 年 PIT 实时全市场池 | 消除幸存者偏差（真实水平） |
| run_overnight.bat | o01→o02→o03 一键全量 | 复跑 |

## 五、复跑方法

- **运行环境**：`D:\QMT_STRATEGIES\.venv_research\Scripts\python.exe`（Python 3.11 + pandas/numpy/pyarrow/duckdb；由 `D:\Python311\python.exe` 创建）
- **数据**：`E:/astock/daily/stock_daily.parquet`（只读）+ `F:/backtest_workspace/data/duckdb/benchmark_index.duckdb`（000300/000852）
- **结果默认写往**：`F:\backtest_workspace\results\overnight\`
- 全量流水线：`scripts\run_overnight.bat`；单独重跑某一步：`python scripts\o0X.py`（各脚本支持 `--smoke 1` 快速冒烟）

## 六、稳健性口径（重要）

- 剔除单日 |收益| > 45% 的病态数据（A 股单日极限 ≤20%，必为复权/数据错误）。
- 因子判定以**中位数 spread** 为准（对极端离群值稳健）。
- 组合回测：等权 top-N、周频、信号于收盘 d 决策次日开盘成交、含佣金+印花税+滑点。
- **PIT（o06）为真实可交易口径**，优先以此为准；o05 静态池含幸存者偏差，仅供对照。

## 七、建议的下一步

1. 用 `D:\QuantLab\backtest` 统一引擎把"ATR<6% + 换手1-8% + 000300>MA60 门控 + 20只 + 周频"固化为原型做前向验证。
2. 叠加 alpha（质量/基本面/换手增强）用 PIT 池检验能否把 -0.90% 拉正。
3. 与实盘 ATR 低波策略（10 万子账户）对齐资金与风控口径后评估是否实盘。
