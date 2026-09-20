# coding=utf-8
"""排雷前置诊断：负向因子五分位尾部检查（DE R2 §2.3）

对 R2 批次 S2 通过的负向因子，按因子值分 5 组（Q1=低暴露..Q5=高暴露），
对比各组前向收益（T+1 开 -> 下期 T+1 开）。
若 Q5 显著差而 Q1-Q4 平坦 → 排除高暴露组有增益空间（排雷条件成立前提）。

用法：
  D:\\Python311\\python.exe research_factor_mining/run_exclude_diag.py
"""
import sys, os, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")

from research.multi_factor_ic.config import BASIC_PATH
from research.multi_factor_ic.data_loader import get_rebalance_dates
import run_screen as RS
import factor_defs_r2 as FD

TARGETS = ["HK_RATIO_STABILITY", "LHB_AMOUNT_RATE_20D", "LHB_BUY_RATIO_20D",
           "LHB_TIMES_60D", "RES_INIT_COVER_120D", "PX_MAX20",
           "PX_LIMITUP_CNT20", "PX_AMT_RATIO_20D", "PX_VOL_STD20", "PX_LISTED_NEW"]
FN_MAP = {c: getattr(FD, c.lower()) for c in TARGETS}


def main():
    t0 = time.time()
    panel = RS.load_panel("2018-01-01", "2026-09-11")
    north_ratio, north_vol = RS.load_northbound()
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    rebal = RS.get_rebalance_dates(panel, freq="M")
    codes_all = set(panel.index.get_level_values("ts_code"))
    fin_wide = RS.load_finance_wide(rebal, codes_all)
    lhb_net, lhb_rate, lhb_flag, lhb_amt_rate, lhb_buy_ratio = RS.load_lhb()
    inst_net, inst_flag = RS.load_inst()
    res_cnt, res_rate, res_tp, res_eps = RS.load_res()

    close_by_code, pctchg_by_code, turnover_by_code, limit_flag_by_code = {}, {}, {}, {}
    open_by_code, amount_by_code = {}, {}
    for code, g in panel.groupby(level=1):
        g = g.droplevel(1).sort_index()
        close_by_code[code] = g["close"]
        pctchg_by_code[code] = g["close"].pct_change()
        turnover_by_code[code] = g["turnover_rate"] if "turnover_rate" in g.columns else None
        open_by_code[code] = g["open"]
        amount_by_code[code] = g["amount"]
        if "up_limit" in g.columns:
            ul = g["up_limit"]
            limit_flag_by_code[code] = ((g["close"] >= ul - 0.01) & ul.notna() & (ul > 0)).astype(int)
        else:
            limit_flag_by_code[code] = (pctchg_by_code[code] >= 0.095).astype(int)
    list_date = {}
    try:
        basic = pd.read_parquet(BASIC_PATH)
        ld = pd.to_datetime(basic["list_date"], errors="coerce")
        list_date = {str(c).upper(): d for c, d in zip(basic["ts_code"], ld)
                     if d is not None and pd.notna(d)}
    except Exception:
        pass

    ctx = {"fin_wide": fin_wide, "close_by_code": close_by_code,
           "pctchg_by_code": pctchg_by_code, "turnover_by_code": turnover_by_code,
           "limit_flag_by_code": limit_flag_by_code, "open_by_code": open_by_code,
           "amount_by_code": amount_by_code, "list_date": list_date,
           "north_ratio": north_ratio, "north_vol": north_vol,
           "lhb_net": lhb_net, "lhb_rate": lhb_rate, "lhb_flag": lhb_flag,
           "lhb_amt_rate": lhb_amt_rate, "lhb_buy_ratio": lhb_buy_ratio,
           "inst_net": inst_net, "inst_flag": inst_flag,
           "res_cnt": res_cnt, "res_rate": res_rate, "res_tp": res_tp, "res_eps": res_eps}

    fwd = RS.per_period_forward_returns(panel, trade_dates, rebal)
    _rebal_used = [d for d in rebal if pd.Timestamp(d) in fwd]

    # 逐因子聚合五分位前向收益
    records = {c: [] for c in TARGETS}
    for d in _rebal_used:
        dd = panel.loc[pd.Timestamp(d)]
        cand = RS.get_candidates(dd)
        if len(cand) < 100:
            continue
        fwd_ret = fwd[pd.Timestamp(d)]
        for fname in TARGETS:
            fn = FN_MAP[fname]
            try:
                vals = fn(pd.Timestamp(d), dd, cand, ctx)
            except Exception:
                vals = None
            if vals is None or len(vals) < 100:
                continue
            common = vals.dropna().index.intersection(fwd_ret.dropna().index)
            if len(common) < 100:
                continue
            q = pd.qcut(vals.loc[common].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
            for qi in [1, 2, 3, 4, 5]:
                grp = common[q == qi]
                if len(grp) >= 10:
                    records[fname].append({"date": pd.Timestamp(d), "q": qi,
                                           "ret": fwd_ret.loc[grp].mean()})

    lines = []
    lines.append("# 排雷前置诊断：负向因子五分位尾部检查\n")
    lines.append("- 生成: %s | 口径: 月频，前向=期i T+1开->期i+1 T+1开，universe=小盘域\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("| 因子 | Q1(低暴露) | Q2 | Q3 | Q4 | Q5(高暴露) | Q5-Q1 | 尾部驱动判定 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for fname in TARGETS:
        recs = pd.DataFrame(records[fname])
        if recs.empty:
            lines.append("| %s | — | — | — | — | — | — | 样本不足 |" % fname)
            continue
        pivot = recs.pivot_table(index="date", columns="q", values="ret")
        pivot = pivot[[1, 2, 3, 4, 5]]
        q_means = pivot.mean()
        q1, q5 = q_means[1], q_means[5]
        mid = q_means[[2, 3, 4]].mean()
        # 尾部驱动判定：Q5 显著低于中段，且 Q1-Q4 相对平坦（Q4 不显著低于中段）
        if q5 < mid - 0.005 and q_means[4] >= mid - 0.005:
            verdict = "✅Q5尾部差，Q1-4平坦 → 排除条件有增益空间"
        elif q5 < mid - 0.005:
            verdict = "⚠️Q5差但Q4也弱 → 排除Q5收益有限"
        else:
            verdict = "✗尾部不显著 → 排除增益存疑"
        lines.append("| %s | %.4f%% | %.4f%% | %.4f%% | %.4f%% | %.4f%% | %+.4f%% | %s |"
                     % (fname, q1 * 100, q_means[2] * 100, q_means[3] * 100,
                        q_means[4] * 100, q5 * 100, (q5 - q1) * 100, verdict))
        # 训练段单独看
        tr = pivot[pivot.index <= "2023-12-31"]
        if len(tr) >= 10:
            tq = tr.mean()
            lines.append("  - 训练段: Q1=%.3f%% Q5=%.3f%% diff=%+.3f%% (n=%d)"
                         % (tq[1] * 100, tq[5] * 100, (tq[5] - tq[1]) * 100, len(tr)))

    out = os.path.join(HERE, "exclude_diag_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("\n报告: %s | 总用时 %.0fs" % (out, time.time() - t0))


if __name__ == "__main__":
    main()
