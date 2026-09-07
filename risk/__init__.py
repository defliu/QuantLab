# coding: utf-8
"""QuantLab 风险叠加层（risk overlay）。

本包放「持仓期内每日、有状态」的风险守卫，与 backtest/rebalance.py 的
「调仓日组合层」互补：
  - rebalance.py  : 调仓日才跑（selection / sizing / industry_cap / vol_target / 杠杆）
  - risk/         : 每个交易日都评估（崩盘空仓 / 低波动 regime 门控），可跨调仓期强制转现

所有守卫默认关闭，由 strategy_config 的 crash_guard / regime_gate 开关启用，
不改动既有回测。
"""
