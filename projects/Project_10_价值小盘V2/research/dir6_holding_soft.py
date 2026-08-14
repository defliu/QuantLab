# coding=utf-8
"""方向6 (降级): 持有期软化 —— 60天硬截断 vs 无限持有的成本量化

状态机口径 (buffer160)。通过 risk.update_holdings 的 max_holding_days 参数
控制持有期: 60 (现状) vs 9999 (无限) vs 90/120, 对比年化/超额/换手。
"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import runner as R

RES = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
os.makedirs(RES, exist_ok=True)

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)


CFG = R.CFG

def run_with_max_holding(max_holding_days):
    """用指定 max_holding_days 跑状态机回测 (复用 runner.run_backtest 但覆盖 risk 参数)"""
    # 重建 risk
    state_file = os.path.join(RES, "risk_state_hold%d.json" % max_holding_days)
    if os.path.exists(state_file):
        os.remove(state_file)
    rcfg = CFG["risk_control"]
    risk = R.RiskController(
        stop_loss=rcfg["stop_loss_pct"], max_drawdown=rcfg["max_drawdown_pct"],
        max_holding_days=max_holding_days, max_daily_turnover=rcfg["max_daily_turnover"],
        state_file=state_file,
    )
    # 临时替换 runner.risk 与 risk 相关的模块级引用
    old_risk = R.risk
    R.risk = risk
    try:
        res = R.run_backtest()
    finally:
        R.risk = old_risk
    return res


CFG = R.CFG

if __name__ == "__main__":
    base = R.run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    p("基准: 累计=%6.1f%%" % (b_cum.iloc[-1] * 100))

    p("\n============ 方向6: 持有期软化 ============")
    for mhd in [60, 90, 120, 9999]:
        res = run_with_max_holding(mhd)
        if len(res) == 0:
            p("  mhd=%d: 无结果" % mhd)
            continue
        res = res.set_index("date")
        cum = (1 + res["period_return"]).cumprod() - 1
        years = (res.index[-1] - res.index[0]).days / 365.25
        ann = (1 + cum.iloc[-1]) ** (1 / years) - 1
        excess = []
        for label, s, e in [("全期", "2018-01-01", "2027-01-01"),
                            ("2024+", "2024-01-01", "2027-01-01"),
                            ("2026", "2026-01-01", "2027-01-01")]:
            sr, _ = R.nav_ret(res["period_return"], s, e)
            br, _ = R.nav_ret(base["ret"], s, e)
            excess.append("超额%-6s=%+7.1f%%" % (label, (sr - br) * 100) if sr is not None and br is not None else "")
        p("  mhd=%5d 年化=%+6.1f%% 累计=%+7.1f%% 期数=%d 换手=%.2f  %s" % (
            mhd, ann * 100, cum.iloc[-1] * 100, len(res), res["turnover"].mean(), " ".join(excess)))

    p("\n总用时 %.0fs" % (time.time() - R.t0))
    out = os.path.join(RES, "dir6_holding_soft.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    p("结果已写入:", out)
