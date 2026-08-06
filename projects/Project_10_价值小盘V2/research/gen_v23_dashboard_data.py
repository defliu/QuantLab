# coding=utf-8
"""v2.3 看板数据生成 (阶段1): 单进程跑各变体, 导出完整时序 + 汇总到 JSON。
复用 runner.py 的 fin_snapshot / 候选缓存, 首个变体慢、后续快。
用法: python research/gen_v23_dashboard_data.py
产物: results/v23_dashboard_data.json"""
import sys, os, time, json

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES_DIR = os.path.join(PROJ_DIR, "results")
os.makedirs(RES_DIR, exist_ok=True)

sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, PROJ_DIR)

import numpy as np
import pandas as pd
import runner as R
from strategy.risk import RiskController


def fresh_risk(tag):
    path = os.path.join(RES_DIR, "_dash_state_%s.json" % tag)
    if os.path.exists(path):
        os.remove(path)
    rcfg = R.CFG["risk_control"]
    return RiskController(
        stop_loss=rcfg["stop_loss_pct"], max_drawdown=rcfg["max_drawdown_pct"],
        max_holding_days=rcfg["max_holding_days"], max_daily_turnover=rcfg["max_daily_turnover"],
        state_file=path)


def run_variant(buffer_keep, delist_screen, tag):
    R.CFG["rebalance"]["buffer_keep"] = buffer_keep
    R.CFG["universe"]["delist_screen"] = delist_screen
    R._cand_cache.clear()
    R.risk = fresh_risk(tag)
    t1 = time.time()
    res = R.run_backtest()
    res = res.set_index("date") if "date" in res.columns else res
    print("  [%s] buffer=%s delist=%s 期数=%d (%.0fs)" % (tag, buffer_keep, delist_screen, len(res), time.time() - t1))
    return res


def window_ret(s, start, end):
    seg = s[(s.index >= start) & (s.index < end)]
    return float((1 + seg).prod() - 1) if len(seg) else None


def stats_of(res, base):
    cum = (1 + res["period_return"]).cumprod()
    total = float(cum.iloc[-1] - 1.0)
    years = (res.index[-1] - res.index[0]).days / 365.25
    ann = float((1 + total) ** (1 / years) - 1) if years > 0 else 0.0
    peak = cum.cummax()
    dd_series = (cum - peak) / peak
    max_dd = float(dd_series.min())
    rf = 0.025
    pr = res["period_return"]
    sharpe = float((pr.mean() - rf / 6) / (pr.std() + 1e-9) * np.sqrt(6))
    win = float((pr > 0).mean())
    ex = {}
    for lab, s_, e_ in [("full", "2018-01-01", "2027-01-01"),
                        ("p2024", "2024-01-01", "2027-01-01"),
                        ("p2026", "2026-01-01", "2027-01-01")]:
        sr = window_ret(pr, s_, e_)
        br = window_ret(base["ret"], s_, e_)
        ex[lab] = (sr - br) if (sr is not None and br is not None) else None
    return {
        "total": total, "ann": ann, "max_dd": max_dd, "sharpe": sharpe,
        "calmar": (ann / abs(max_dd)) if max_dd != 0 else 0.0,
        "win_rate": win, "turnover": float(res["turnover"].mean()),
        "ex_full": ex["full"], "ex_2024": ex["p2024"], "ex_2026": ex["p2026"],
        "n_periods": int(len(res)),
    }


print("数据加载中...", flush=True)
t0 = time.time()
base = R.run_base()
base_nav = (1 + base["ret"]).cumprod()
print("基准期数=%d (%.0fs)" % (len(base), time.time() - t0))

print("跑变体...", flush=True)
variants = {
    "baseline": run_variant(0, False, "a"),       # V2a 存档口径(全量重建)
    "buffer160": run_variant(160, False, "b"),    # 仅 buffer
    "v23": run_variant(160, True, "c"),           # v2.3 = buffer160 + 退市排雷
}

out = {"meta": {
    "gen_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "range": "%s ~ %s" % (variants["v23"].index[0].date(), variants["v23"].index[-1].date()),
    "n_periods": int(len(variants["v23"])),
}, "benchmark": {
    "dates": [d.strftime("%Y-%m-%d") for d in base.index],
    "nav": [round(float(v), 4) for v in base_nav.values],
}, "variants": {}}

for name, res in variants.items():
    cum = (1 + res["period_return"]).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    out["variants"][name] = {
        "dates": [d.strftime("%Y-%m-%d") for d in res.index],
        "nav": [round(float(v), 4) for v in cum.values],
        "drawdown": [round(float(v) * 100, 2) for v in dd.values],
        "period_return": [round(float(v) * 100, 2) for v in res["period_return"].values],
        "turnover": [round(float(v), 3) for v in res["turnover"].values],
        "n_hold": [int(v) for v in res["n"].values],
        "stats": stats_of(res, base),
    }

# 年度收益 (基准 + v2.3)
def yearly(nav_dates, nav_vals):
    df = pd.DataFrame({"d": pd.to_datetime(nav_dates), "nav": nav_vals})
    df["y"] = df["d"].dt.year
    g = df.groupby("y").agg(s=("nav", "first"), e=("nav", "last"))
    return {int(y): round(float(r.e / r.s - 1) * 100, 2) for y, r in g.iterrows()}

out["yearly"] = {
    "benchmark": yearly(out["benchmark"]["dates"], out["benchmark"]["nav"]),
    "baseline": yearly(out["variants"]["baseline"]["dates"], out["variants"]["baseline"]["nav"]),
    "v23": yearly(out["variants"]["v23"]["dates"], out["variants"]["v23"]["nav"]),
}

path = os.path.join(RES_DIR, "v23_dashboard_data.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("已写出:", path, "(%.0fs)" % (time.time() - t0))

for fn in os.listdir(RES_DIR):
    if fn.startswith("_dash_state_") and fn.endswith(".json"):
        try:
            os.remove(os.path.join(RES_DIR, fn))
        except Exception:
            pass
