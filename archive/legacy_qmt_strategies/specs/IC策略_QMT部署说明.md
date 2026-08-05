# 多因子IC策略 — QMT 部署说明

> 生成时间: 2026-07-20
> 状态: ✅ 已部署 (commit b735109, push origin/main)

## 1. 文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| **生产版(部署用)** | `D:\QMT_STRATEGIES\strategy_mfic.py` | GBK编码, QMT直接加载, 13.7KB |
| 已部署位置 | `E:\国金QMT交易端模拟\python\strategy_mfic.py` | 复制过去的生产版 |
| 开发版(源码) | `research\multi_factor_ic\mfic_strategy\strategy_mfic_dev.py` | UTF-8, 含debug打印 |
| 构建脚本 | `research\multi_factor_ic\mfic_strategy\build_prod.py` | dev(UTF-8)→GBK转换 |
| 测试脚本 | `scripts\qmt_test_mfic.py` | Layer1(数据通路)+Layer2(策略核心) 分层测试 |
| 模拟演示 | `scripts\qmt_simulate_mfic.py` | 模拟运行日志输出 |

## 2. 部署流程

1. 修改 `mfic_strategy/strategy_mfic_dev.py` (UTF-8 源码)
2. 运行 `build_prod.py` 生成 `strategy_mfic.py` (GBK)
3. 运行 `scripts/validate_qmt_file.py strategy_mfic.py` 验证 (6项ALL PASS)
4. 复制 `strategy_mfic.py` 到 `E:\国金QMT交易端模拟\python\`
5. miniQMT终端 → 策略交易 → 加载 `strategy_mfic.py` → 设置账号 → 启动

## 3. QMT 环境验证

**测试环境**: `E:\国金QMT交易端模拟\bin.x64\python.exe` (Python 3.6.8, xtquant可用)

| 检查项 | 结果 |
|--------|------|
| xtdata 导入 | ✅ PASS |
| get_stock_list_in_sector("沪深A股") | ✅ PASS (5201只) |
| get_market_data_ex(基础OHLCV) | ✅ PASS (close/open/amount) |
| get_financial_data(ROE) | ✅ PASS |
| get_full_tick | ✅ PASS |
| pe_ttm/pb/circ_mv | ⏭️ 需QMT ContextInfo环境 |

> ⚠️ standalone xtdata 仅支持基础OHLCV字段。pe_ttm/pb/circ_mv 需在QMT `handlebar` 内通过 `C.get_market_data_ex()` 获取。这是QMT API正常行为，非策略缺陷。

## 4. 实盘运行参数 (10万本金)

| 参数 | 取值 |
|------|------|
| 本金 | 100,000元 |
| 市值区间 | 0-30亿 |
| 调仓频率 | 双月(偶数月最后一个交易日) |
| 持仓数 | TOP80 |
| 止损线 | -12% (每日尾盘检查) |
| 成交额阈值 | >2000万 |
| 单票上限 | 2% (~2000元/只) |
| 预留现金 | 2% |
| 买入隔离 | 持仓文件 `D:/QMT_POOL/mfic_positions.json` + `passorder(strRemark="mfic")` |

## 5. 运行日志特征

- 日志前缀: `[MF]`
- 非调仓日: 仅止损检查, 无操作
- 调仓日(偶数月最后一交易日 14:30-14:55): 全市场扫描 → 过滤(0-30亿+成交额>2000万) → 4因子评分 → TOP80 → 卖出不在池的 → 买入新选中的
- 止损触发日: 跌幅>-12%自动卖出 → 用最高评分替补票替换
- 持仓持久化: `D:/QMT_POOL/mfic_positions.json`

## 6. 回测依据

10万本金参数回测结论 (全周期 2018.07-2026.06):
- 年化收益 17.1% / 夏普 0.55 / 最大回撤 -19.8%
- 10万终值 25.8万

详见: `specs/IC策略_10万本金实盘参数回测.md`
