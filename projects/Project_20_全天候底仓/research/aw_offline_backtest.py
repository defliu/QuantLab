# -*- coding: utf-8 -*-
"""Project_20 全天候 - 真实 ETF 六线回测（离线数据 D:/astock/etf/）。

数据：etf_daily.parquet（nav 净值口径，含分红再投资）
对照：V1 固定比例 / V2 含纳指 / EW 等权 / RP 波动率倒数 / 100%沪深300 / 100%货币
口径：月频末收盘再平衡，成本 Σ|Δw|×0.05%（对齐 DE de_allweather_backtest 口径）
用法：python research/aw_offline_backtest.py
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ETF = os.path.join(os.path.dirname(os.path.dirname(HERE)), "..", "astock", "etf",
                   "etf_daily.parquet")
ETF = r"D:\astock\etf\etf_daily.parquet"
COST = 0.0005  # 单边 0.05%

LEGS = {
    "510300.SH": "沪深300",
    "511010.SH": "十年国债",
    "511880.SH": "货币",
    "518880.SH": "黄金",
    "513100.SH": "纳指QDII",
}

H11025_JSON = r"D:\QuantLab\data\schedules\de_r3_aw_money_h11025.json"

V1 = {"510300.SH": 0.30, "511010.SH": 0.30, "511880.SH": 0.25, "518880.SH": 0.15}
V2 = {"510300.SH": 0.25, "511010.SH": 0.30, "511880.SH": 0.20,
      "518880.SH": 0.15, "513100.SH": 0.10}
EW4 = {c: 0.25 for c in ("510300.SH", "511010.SH", "511880.SH", "518880.SH")}


def load_nav(start="20150101"):
    e = pd.read_parquet(ETF)
    e["trade_date"] = e["trade_date"].astype(str).str.replace("-", "", regex=False)
    e = e[e["trade_date"] >= start]
    # 收益用 close（价格口径）；货币腿(511880)价格恒 ~100（收益走份额，同 511990 坑）
    # -> 货币腿收益改用 H11025 货币基金指数（含收益再投资，DE 已验证 2.44%/年）
    piv = e.pivot_table(index="trade_date", columns="ts_code", values="close")
    piv = piv[sorted(LEGS)]
    piv = piv.ffill().dropna(how="any")
    import json
    with open(H11025_JSON, "r", encoding="utf-8") as f:
        m = pd.DataFrame(json.load(f))
    m["trade_date"] = m["trade_date"].astype(str).str.replace("-", "", regex=False)
    m = m.set_index("trade_date")["close"]
    m = m[m.index.isin(piv.index)]
    piv["_H11025"] = m.reindex(piv.index).ffill()
    return piv


def line_ret(nav):
    """每只腿的日收益：货币腿用 H11025，其余用 close。"""
    ret = nav.pct_change().fillna(0.0)
    ret["511880.SH"] = ret["_H11025"]
    return ret.drop(columns=["_H11025"])


def run_line(nav, weights, freq="month"):
    ret = line_ret(nav)
    dates = list(nav.index)
    # 月末信号日
    months = sorted({d[:6] for d in dates})
    signal = {max(d for d in dates if d.startswith(m)) for m in months}
    w = {c: 0.0 for c in nav.columns}
    nav_val = 1.0
    day_ret = {}
    prev_close = None
    for d in dates:
        if d in signal:
            new_w = weights
            delta = sum(abs(new_w.get(c, 0) - w.get(c, 0)) for c in nav.columns)
            nav_val *= (1 - delta * COST)
            w = dict(new_w)
        r = sum(w.get(c, 0) * ret.loc[d, c] for c in ret.columns)
        nav_val *= (1 + r)
        day_ret[d] = r
    sr = pd.Series(day_ret)
    return nav_val, sr


def metrics(sr, nav_val):
    years = len(sr) / 244.0
    nv = (1 + sr).cumprod()
    cagr = nv.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    dd = (nv / nv.cummax() - 1).min()
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    sharpe = sr.mean() / (sr.std() + 1e-12) * np.sqrt(244) if sr.std() > 0 else 0
    return cagr, dd, calmar, sharpe


def main():
    nav = load_nav()
    print("[load] ETF nav rows=%d dates=%s~%s" % (
        len(nav), nav.index[0], nav.index[-1]))
    lines = {
        "V1固定(30/30/25/15)": V1,
        "V2含纳指(25/30/20/15/10)": V2,
        "EW等权4腿": EW4,
        "100%沪深300": {"510300.SH": 1.0},
        "100%货币": {"511880.SH": 1.0},
        "100%黄金": {"518880.SH": 1.0},
    }
    print("=" * 74)
    print("%-24s %8s %8s %6s %6s" % ("线", "年化", "MaxDD", "卡玛", "夏普"))
    print("-" * 74)
    results = {}
    for name, w in lines.items():
        nv, sr = run_line(nav, w)
        cagr, dd, calmar, sharpe = metrics(sr, nv)
        results[name] = (cagr, dd, calmar, sharpe)
        print("%-24s %7.2f%% %7.1f%% %6.2f %6.2f" % (
            name, cagr * 100, dd * 100, calmar, sharpe))
    # 分年（V1 vs EW）
    print("=" * 74)
    for name in ("V1固定(30/30/25/15)", "EW等权4腿"):
        nv, sr = run_line(nav, lines[name])
        y = (1 + sr).groupby(sr.index.str[:4]).prod() - 1
        print("[%s] 分年: %s" % (name, ", ".join("%s:%.1f%%" % (k, v * 100) for k, v in y.items())))


if __name__ == "__main__":
    main()
