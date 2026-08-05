# coding: utf-8
"""QuantLab 通用回测引擎包。

引擎契约（最小化、与策略解耦）：
  - 策略侧只输出 target_weights {code: weight}（通用即插即跑模式），
    或传统的 buy_candidates / sell_decisions（向后兼容）。
  - 引擎统一负责：订单簿记、真实交易约束（涨跌停/停牌/滑点/整数手/ST±5%）、
    配置驱动组合层（equal/vol_parity、行业cap、vol_target、两融杠杆+按日计息）。

数据接口（与 data/ 下的 reader duck-typed 对齐）：
  reader.load_window(codes, start, end) -> {code: DataFrame(date, open, high, low, close, vol, amount, ...)}
  reader.trading_calendar(start, end) -> [date_str]
  reader.coverage(...) / reader.close()
"""
