# 审计脚本：重算 ATR MAX5 与 Project_13 关键回测的真实 CAGR/分年度收益（只读，不写任何回测产物）
# 背景：2026-08-16 审计发现框架 analyzer.py 的 annual_return 是线性年化（total×252/n），非复利 CAGR
# 运行: python research_audit/audit_max5_p13_20260816.py（QuantLab 根目录下）
# 依赖: pandas/numpy（研究环境 py3.11）
import json
import pandas as pd
import numpy as np

RUNS = {
    "ATR_base": "reports/20260815_110119_ccfb82_atr_10w_price50",
    "ATR_max5": "reports/20260815_174019_e77de8_atr_10w_price50_a_max",
    "P13_n16": "reports/20260816_093145_7071c2_v3_n16_h60_s12",
    "P13_gate": "reports/20260816_094200_6dfa6d_v3_n8_gate_hold",
}


def load_nav(d):
    df = pd.read_csv(d + "/equity_curve.csv")
    cols = {c.lower(): c for c in df.columns}
    date_c = cols.get("date") or cols.get("trade_date")
    nav_c = None
    for cand in ["total_asset", "nav", "equity", "total_value", "asset"]:
        if cand in cols:
            nav_c = cols[cand]
            break
    df = df[[date_c, nav_c]].copy()
    df.columns = ["date", "nav"]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def metrics(df):
    nav = df["nav"].astype(float)
    n = len(df)
    total = nav.iloc[-1] / nav.iloc[0] - 1.0
    yrs = n / 252.0
    cagr = (1.0 + total) ** (1.0 / yrs) - 1.0
    linear = total / yrs
    ret = nav.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0.0
    dd = (nav / nav.cummax() - 1.0).min()
    df2 = df.copy()
    df2["year"] = df2["date"].dt.year
    yearly = df2.groupby("year")["nav"].last()
    prev = yearly.shift(1)
    prev.iloc[0] = nav.iloc[0]
    yearly_ret = (yearly / prev - 1.0) * 100
    return dict(total=total, cagr=cagr, linear=linear, sharpe=sharpe, mdd=dd,
                n_days=n, yearly={int(k): round(v, 1) for k, v in yearly_ret.items()})


def main():
    navs = {}
    for k, d in RUNS.items():
        df = load_nav(d)
        navs[k] = df
        m = metrics(df)
        s = json.load(open(d + "/summary.json", encoding="utf-8"))["performance"]
        print("== %s ==" % k)
        print("  重算: total=%.2f%%  CAGR=%.2f%%  linear=%.2f%%  sharpe=%.3f  mdd=%.2f%%  days=%d"
              % (m["total"] * 100, m["cagr"] * 100, m["linear"] * 100,
                 m["sharpe"], m["mdd"] * 100, m["n_days"]))
        print("  报告: total=%.2f%%  annual=%.2f%%  sharpe=%.3f  mdd=%.2f%%"
              % (s["total_return"] * 100, s["annual_return"] * 100,
                 s["sharpe"], s["max_drawdown"] * 100))
        print("  分年度: %s" % m["yearly"])

    # ATR 基线 vs MAX5 差异交易
    tb = pd.read_csv(RUNS["ATR_base"] + "/trades.csv")
    tm = pd.read_csv(RUNS["ATR_max5"] + "/trades.csv")
    key = [c for c in tb.columns if c.lower() in ("date", "trade_date")]
    code = [c for c in tb.columns if c.lower() in ("code", "ts_code", "stock")]
    if key and code:
        kb = set(zip(tb[key[0]].astype(str), tb[code[0]].astype(str)))
        km = set(zip(tm[key[0]].astype(str), tm[code[0]].astype(str)))
        only_b, only_m = kb - km, km - kb
        print("\n基线独有交易: %d | MAX5独有交易: %d | 共同: %d" % (len(only_b), len(only_m), len(kb & km)))
        for y in sorted({d_[:4] for d_, _ in only_b | only_m}):
            bb = sum(1 for d_, _ in only_b if d_[:4] == y)
            mm = sum(1 for d_, _ in only_m if d_[:4] == y)
            print("  %s: 基线独有 %d 笔, MAX5 独有 %d 笔" % (y, bb, mm))

    # 分段 CAGR（2019-2022 vs 2023-2026）
    print("\n== 分段 CAGR（重算） ==")
    for k, df in navs.items():
        for lab, a, b in [("2019-2022", "2019-01-01", "2022-12-31"),
                          ("2023-2026", "2023-01-01", "2026-06-30")]:
            seg = df[(df["date"] >= a) & (df["date"] <= b)]
            if len(seg) > 30:
                m = metrics(seg)
                print("  %s %s: CAGR=%.2f%%  mdd=%.2f%%" % (k, lab, m["cagr"] * 100, m["mdd"] * 100))


if __name__ == "__main__":
    main()
