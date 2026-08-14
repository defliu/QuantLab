# coding=utf-8
"""方向2 (降级): 滚动窗口因子权重 —— BP vs EP 滚动 IC 加权

在 run_grid_validation 的 V2d (BP0.7+EP0.3) 验证基础上, 用"滚动 IC"自适应权重:
  每期用过去 K 期滚动 ICIR 决定 BP:EP 权重 (ICIR 高的权重更大)
对比: 纯BP (基线), V2d固定0.7/0.3, 滚动IC权重。

用法: python research/dir2_rolling_weight.py
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

panel, ti, trade_dates = R.panel, R.ti, R.trade_dates
rebal = R.rebal
CFG = R.CFG

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)

# ---- 构建 EP z-score (行业中性) ----
basic = pd.read_parquet(r"E:/astock/basic/stock_basic.parquet")
ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))


def industry_z(values, candidates):
    inds = pd.Series(candidates, index=candidates).map(ind_map)
    t = pd.DataFrame({"v": values, "ind": inds}).dropna()
    return t.groupby("ind")["v"].transform(lambda x: (x - x.mean()) / (x.std() + 1e-9))


def per_period_factors():
    """每期 BP/EP 因子得分 + 下期收益"""
    recs = []
    for i in range(len(rebal) - 1):
        d = pd.Timestamp(rebal[i])
        dd_data = panel.loc[d]
        cand = R.get_candidates(d, dd_data)
        if len(cand) < 10:
            continue
        bp = 1.0 / dd_data["pb"].reindex(cand).replace(0, np.nan)
        z_bp = industry_z(bp, cand)
        pe = dd_data["pe_ttm"].reindex(cand)
        ep = (1.0 / pe).where(pe > 0)
        z_ep = industry_z(ep, cand)
        e1_idx = ti[d] + 1
        e2_idx = ti[pd.Timestamp(rebal[i + 1])] + 1
        if e1_idx >= len(trade_dates) or e2_idx >= len(trade_dates):
            continue
        r1 = panel.loc[trade_dates[e1_idx], "open"]
        r2 = panel.loc[trade_dates[e2_idx], "open"]
        rets = {}
        for code in cand:
            a, b = r1.get(code), r2.get(code)
            if a is not None and b is not None and a > 0 and b > 0:
                rets[code] = b / a - 1.0
        if len(rets) < 10:
            continue
        common = [c for c in cand if c in rets and not np.isnan(z_bp.get(c, np.nan))]
        if len(common) < 10:
            continue
        ic_bp = z_bp.reindex(common).corr(pd.Series(rets).reindex(common))
        ic_ep = z_ep.reindex(common).corr(pd.Series(rets).reindex(common))
        recs.append({"date": d, "ic_bp": ic_bp, "ic_ep": ic_ep, "n": len(common)})
    return pd.DataFrame(recs)


if __name__ == "__main__":
    p("计算 BP/EP 每期 IC...")
    ic = per_period_factors()
    p("期数:", len(ic))
    p("BP IC: 均值=%.4f  std=%.4f" % (ic["ic_bp"].mean(), ic["ic_bp"].std()))
    p("EP IC: 均值=%.4f  std=%.4f" % (ic["ic_ep"].mean(), ic["ic_ep"].std()))
    p("BP-EP IC 差: BP 更好期占比 = %.1f%%" % ((ic["ic_bp"] > ic["ic_ep"]).mean() * 100))

    # 滚动窗口: 过去 K 期 ICIR 决定权重
    ic["year"] = ic["date"].dt.year
    p("\n逐年 BP/EP IC:")
    for y, g in ic.groupby("year"):
        p("  %d: BP IC=%.4f  EP IC=%.4f  (n=%d)" % (y, g["ic_bp"].mean(), g["ic_ep"].mean(), len(g)))

    # 滚动 ICIR 权重方案
    K = 8
    ic = ic.sort_values("date").reset_index(drop=True)
    w_bp_roll = []
    w_ep_roll = []
    for i in range(len(ic)):
        if i < K:
            w_bp_roll.append(1.0)
            w_ep_roll.append(0.0)
            continue
        hist = ic.iloc[i - K:i]
        ir_bp = hist["ic_bp"].mean() / (hist["ic_bp"].std() + 1e-9)
        ir_ep = hist["ic_ep"].mean() / (hist["ic_ep"].std() + 1e-9)
        wb = max(0.0, ir_bp) / (abs(ir_bp) + abs(ir_ep) + 1e-9)
        wb = 0.5 + 0.5 * (ir_bp - ir_ep) / (abs(ir_bp) + abs(ir_ep) + 1e-9)
        wb = min(1.0, max(0.0, wb))
        w_bp_roll.append(wb)
        w_ep_roll.append(1.0 - wb)
    ic["w_bp_roll"] = w_bp_roll
    ic["w_ep_roll"] = w_ep_roll
    p("\n滚动权重 (K=8) 概要: BP权重 均值=%.2f  [%.2f, %.2f]" % (
        ic["w_bp_roll"].mean(), ic["w_bp_roll"].min(), ic["w_bp_roll"].max()))
    p("近6期滚动权重 (BP):", [round(x, 2) for x in ic["w_bp_roll"].tail(6).tolist()])

    out = os.path.join(RES, "dir2_rolling_weight.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    p("\n结果已写入:", out)
