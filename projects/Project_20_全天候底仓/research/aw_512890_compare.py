# -*- coding: utf-8 -*-
"""P20 股腿对照：沪深300(510300) vs 红利低波(512890)，2019 起同窗口月频。

复用 aw_offline_backtest 引擎；DE R4 建议（P05 已推荐红利低波）。
用法：python research/aw_512890_compare.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aw_offline_backtest as aw

STOCK_300 = "510300.SH"
STOCK_512 = "512890.SH"


def build_line(nav, weights, cost=aw.COST):
    ret = aw.line_ret(nav)
    dates = list(nav.index)
    months = sorted({d[:6] for d in dates})
    signal = {max(d for d in dates if d.startswith(m)) for m in months}
    w = {c: 0.0 for c in ret.columns}
    nav_val = 1.0
    day_ret = {}
    for d in dates:
        if d in signal:
            delta = sum(abs(weights.get(c, 0) - w.get(c, 0)) for c in ret.columns)
            nav_val *= (1 - delta * cost)
            w = dict(weights)
        r = sum(w.get(c, 0) * ret.loc[d, c] for c in ret.columns)
        nav_val *= (1 + r)
        day_ret[d] = r
    return pd.Series(day_ret)


def main():
    # 加入 512890 到数据加载（不影响 510300）
    if STOCK_512 not in aw.LEGS:
        aw.LEGS[STOCK_512] = "红利低波"
    nav = aw.load_nav("20190101")
    print("[load] %s~%s rows=%d (2019 起同窗口)" % (nav.index[0], nav.index[-1], len(nav)))

    v1_300 = {k: v for k, v in aw.V1.items()}
    v1_512 = {k: v for k, v in aw.V1.items() if k != STOCK_300}
    v1_512[STOCK_512] = aw.V1[STOCK_300]

    print("=" * 74)
    print("%-22s %8s %8s %6s %6s" % ("股腿", "年化", "MaxDD", "卡玛", "夏普"))
    print("-" * 74)
    for name, w in (("沪深300", v1_300), ("红利低波512890", v1_512)):
        sr = build_line(nav, w)
        nv = (1 + sr).cumprod()
        years = len(sr) / 244.0
        cagr = nv.iloc[-1] ** (1 / years) - 1
        dd = (nv / nv.cummax() - 1).min()
        calmar = cagr / abs(dd) if dd < 0 else np.nan
        sharpe = sr.mean() / (sr.std() + 1e-12) * np.sqrt(244) if sr.std() > 0 else 0
        print("%-22s %7.2f%% %7.1f%% %6.2f %6.2f" % (name, cagr * 100, dd * 100, calmar, sharpe))
    # 分年对照
    print("=" * 74)
    for name, w in (("沪深300", v1_300), ("红利低波512890", v1_512)):
        sr = build_line(nav, w)
        y = (1 + sr).groupby(sr.index.str[:4]).prod() - 1
        print("[%s] %s" % (name, ", ".join("%s:%.1f%%" % (k, v * 100) for k, v in y.items())))


if __name__ == "__main__":
    main()
