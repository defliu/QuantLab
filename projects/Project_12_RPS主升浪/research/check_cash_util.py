# coding: utf-8
"""分析 10万版 资金利用率（现金占比）——核心：小资金买不起高价股会闲置。"""
import pandas as pd

for name, path in [
    ("10万/8只", "D:/QuantLab/reports/20260814_162537_cadab0_atr_lowvol_10w_8"),
    ("100万/100只", "D:/QuantLab/reports/20260814_163946_9c2b82_atr_lowvol_100w_100"),
]:
    eq = pd.read_csv(path + "/equity_curve.csv")
    eq["util"] = 1 - eq["cash"] / eq["total_asset"]
    # 只看持仓期（market_value > 0）
    hold = eq[eq["market_value"] > 100]
    if len(hold) == 0:
        print("%s: 无持仓期" % name)
        continue
    print("%s: 持仓期平均仓位 %.1f%%, 期末现金 %.0f / 资产 %.0f" % (
        name, hold["util"].mean() * 100,
        eq["cash"].iloc[-1], eq["total_asset"].iloc[-1]))
