# coding=utf-8
"""v2.3 集成校验: runner.py buffer + 退市排雷 并入口径验证
- 变体A buffer_keep=0  & delist_screen=false  -> 应复现 V2a 存档 (年化16.2/超额200.9/换手0.91)
- 变体B buffer_keep=80 & delist_screen=false  -> 同上(keep=n 即全量重建)
- 变体C buffer_keep=160& delist_screen=true   -> v2.3 新基线 (预期年化~18, 换手~0.80)
用法: python research/validate_v23_integration.py"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import runner as R
from strategy.risk import RiskController

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES_DIR = os.path.join(PROJ_DIR, "results")


def summarize(result, base):
    if len(result) == 0:
        return None
    result = result.set_index("date") if "date" in result.columns else result
    cum = (1 + result["period_return"]).cumprod()
    total = cum.iloc[-1] - 1.0
    years = (result.index[-1] - result.index[0]).days / 365.25
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else 0
    peak = cum.cummax()
    max_dd = ((cum - peak) / peak).min()

    def nav_ret(s, start, end):
        seg = s[(s.index >= start) & (s.index < end)]
        return ((1 + seg).prod() - 1) if len(seg) else None

    ex = {}
    for lab, s, e in [("full", "2018-01-01", "2027-01-01"),
                      ("2024+", "2024-01-01", "2027-01-01"),
                      ("2026", "2026-01-01", "2027-01-01")]:
        sr = nav_ret(result["period_return"], s, e)
        br = nav_ret(base["ret"], s, e)
        ex[lab] = (sr - br) if (sr is not None and br is not None) else None
    return {"ann": ann, "max_dd": max_dd, "turn": result["turnover"].mean(),
            "ex_full": ex["full"], "ex_2024": ex["2024+"], "ex_2026": ex["2026"]}


def fresh_risk(tag):
    path = os.path.join(RES_DIR, "val_state_%s.json" % tag)
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
    R._cand_cache.clear()          # 清候选缓存(排雷开关影响候选)
    R.risk = fresh_risk(tag)
    t1 = time.time()
    res = R.run_backtest()
    st = summarize(res, R.run_base())
    st["time"] = time.time() - t1
    return st


print("数据加载完成, 开始 v2.3 集成校验...\n")
base_row = "%-28s 年化    回撤     换手   超额[全期     2024+     2026]"
print(base_row)

rows = []
for tag, bk, ds in [("A buffer=0 无排雷(复现存档)", 0, False),
                    ("B buffer=80 无排雷(=全量重建)", 80, False),
                    ("C v2.3 buffer=160+退市排雷", 160, True)]:
    st = run_variant(bk, ds, "v%d" % len(rows))
    rows.append((tag, st))
    print("%-28s %+6.1f%% %+7.1f%%  %.2f   %+8.1f%% %+7.1f%% %+6.1f%%   (%.0fs)" % (
        tag, st["ann"] * 100, st["max_dd"] * 100, st["turn"],
        (st["ex_full"] or 0) * 100, (st["ex_2024"] or 0) * 100,
        (st["ex_2026"] or 0) * 100, st["time"]))

print("\n自检判据: 变体A/B 应≈ V2a存档 (年化+16.2% 回撤-29.7% 超额全期+200.9% 换手0.91)")
a = rows[0][1]; b = rows[1][1]
ok = abs(a["ann"] - 0.162) < 0.01 and abs((a["ex_full"] or 0) - 2.009) < 0.05
print("变体A 复现存档:", "通过" if ok else "偏差需检查")
ok2 = abs(a["ann"] - b["ann"]) < 0.001 and abs((a["ex_full"] or 0) - (b["ex_full"] or 0)) < 0.005
print("变体A==B (buffer=0 与 buffer=80 等价):", "通过" if ok2 else "不一致")

for fn in os.listdir(RES_DIR):
    if fn.startswith("val_state_") and fn.endswith(".json"):
        try:
            os.remove(os.path.join(RES_DIR, fn))
        except Exception:
            pass
