# coding=utf-8
"""排雷 ablation：ATR 合格域内排除条件对比回测（DE R2 §2 简化版）

载体：ATR 合格域（ATR%<6 + 换手1-8% + 非ST + 真实价<50 + ROE>0 + 动量12-1>0）
季度调仓，选 ATR% 最低前 8 只等权；前向=期 i T+1 开买入 -> 期 i+1 T+1 开卖出。
排除条件（五分位诊断确认尾部驱动的 4 个 PX 因子）：剔除因子值最高 20% 暴露组（同 MAX5 0.20 档）。

对比：基线 vs 各单条件 vs 组合，指标=复利CAGR/最大回撤/卡玛(CAGR口径)/分段(2019-22/2023-26)。

用法：
  D:\\Python311\\python.exe research_factor_mining/run_exclude_ablation.py
"""
import os, sys, time
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

EXCLUDE_FACTORS = ["PX_MAX20", "PX_AMT_RATIO_20D", "PX_VOL_STD20", "PX_LISTED_NEW"]
EXCLUDE_PCT = 0.20
N_HOLD = 8
START, END = "2019-01-01", "2026-09-11"


def main():
    t0 = time.time()
    panel = RS.load_panel(START, END)
    north_ratio, north_vol = RS.load_northbound()
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    rebal = RS.get_rebalance_dates(panel, freq="Q")
    codes_all = set(panel.index.get_level_values("ts_code"))
    fin_wide = RS.load_finance_wide(rebal, codes_all)
    lhb_net, lhb_rate, lhb_flag, lhb_amt_rate, lhb_buy_ratio = RS.load_lhb()
    inst_net, inst_flag = RS.load_inst()
    res_cnt, res_rate, res_tp, res_eps = RS.load_res()

    close_by_code, pctchg_by_code, turnover_by_code, limit_flag_by_code = {}, {}, {}, {}
    open_by_code, amount_by_code, high_by_code, low_by_code = {}, {}, {}, {}
    for code, g in panel.groupby(level=1):
        g = g.droplevel(1).sort_index()
        close_by_code[code] = g["close"]
        pctchg_by_code[code] = g["close"].pct_change()
        turnover_by_code[code] = g["turnover_rate"] if "turnover_rate" in g.columns else None
        open_by_code[code] = g["open"]
        amount_by_code[code] = g["amount"]
        high_by_code[code] = g["high"]
        low_by_code[code] = g["low"]
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

    # 逐季 ATR 合格域选股（每期保存 code 列表）
    def atr_pct(date, code):
        s = close_by_code.get(code)
        h = high_by_code.get(code)
        lo = low_by_code.get(code)
        if s is None or h is None or lo is None or len(s) < 15:
            return None
        t = pd.Timestamp(date)
        pos = s.index.searchsorted(t)
        if pos < 15:
            return None
        seg_c = s.iloc[pos - 15:pos]
        seg_h = h.iloc[pos - 15:pos]
        seg_l = lo.iloc[pos - 15:pos]
        pc = seg_c.shift(1)
        tr = pd.concat([seg_h - seg_l, (seg_h - pc).abs(), (seg_l - pc).abs()], axis=1).max(axis=1)
        if tr.dropna().empty or seg_c.iloc[-1] <= 0:
            return None
        return float(tr.dropna().mean() / seg_c.iloc[-1])

    def momentum_12_1(date, code):
        s = close_by_code.get(code)
        if s is None or len(s) < 252:
            return None
        t = pd.Timestamp(date)
        pos = s.index.searchsorted(t)
        if pos < 252:
            return None
        c0, c1, c12 = s.iloc[pos - 1], s.iloc[pos - 21], s.iloc[pos - 252]
        if c0 > 0 and c12 > 0:
            return float(c0 / c12 - 1.0)
        return None

    def build_eligible(d, cand, fin_wide_t):
        """返回 [(code, atr_pct)] 已过合格域"""
        dd = panel.loc[pd.Timestamp(d)]
        out = []
        for c in cand:
            close = dd.loc[c]["close"]
            if close <= 0 or close >= 50.0:      # 真实价<50（panel close 为不复权价）
                continue
            to = turnover_by_code.get(c)
            if to is None or to.get(pd.Timestamp(d), np.nan) is np.nan:
                to_val = dd.loc[c].get("turnover_rate", np.nan)
            else:
                to_val = float(to.loc[:pd.Timestamp(d)].iloc[-1])
            if not (1.0 <= to_val <= 8.0):       # 换手1-8%
                continue
            roe = fin_wide_t.get(c, np.nan)
            if roe is None or np.isnan(roe) or roe <= 0:   # ROE>0
                continue
            ap = atr_pct(d, c)
            if ap is None or ap <= 0 or ap > 0.06:         # ATR%<6
                continue
            m12 = momentum_12_1(d, c)
            if m12 is None or m12 <= 0:                     # 动量12-1>0
                continue
            out.append((c, ap))
        return out

    # 计算每期选股（基线）
    fwd = RS.per_period_forward_returns(panel, trade_dates, rebal)
    _rebal_used = [d for d in rebal if pd.Timestamp(d) in fwd]
    picks_base = {}
    picks_excl = {f: {} for f in EXCLUDE_FACTORS}
    picks_all = {}
    for d in _rebal_used:
        dd = panel.loc[pd.Timestamp(d)]
        cand = RS.get_candidates(dd)
        if len(cand) < 100:
            continue
        fin_t = fin_wide["roe"].loc[pd.Timestamp(d)]
        eligible = build_eligible(d, cand, fin_t)
        if len(eligible) < N_HOLD:
            continue
        eligible.sort(key=lambda r: r[1])
        base = [c for c, _ in eligible[:N_HOLD]]
        picks_base[pd.Timestamp(d)] = base
        # 各排除条件：对 eligible 全体算因子值，剔除最高 20% 后取 ATR 最低 N_HOLD
        for fn in EXCLUDE_FACTORS:
            try:
                vals = getattr(FD, fn.lower())(pd.Timestamp(d), dd, [c for c, _ in eligible], ctx)
            except Exception:
                vals = None
            if vals is None or len(vals) < N_HOLD:
                continue
            sub = vals.dropna()
            if len(sub) < N_HOLD + 2:
                continue
            thr = sub.quantile(1.0 - EXCLUDE_PCT)
            keep = [c for c, _ in eligible if sub.get(c, -np.inf) <= thr]
            keep.sort(key=lambda c: dict(eligible)[c])
            if len(keep) >= N_HOLD:
                picks_excl[fn][pd.Timestamp(d)] = keep[:N_HOLD]
        # 组合：4 条件都剔除
        keep = [c for c, _ in eligible]
        for fn in EXCLUDE_FACTORS:
            try:
                vals = getattr(FD, fn.lower())(pd.Timestamp(d), dd, [c for c, _ in eligible], ctx)
            except Exception:
                vals = None
            if vals is not None:
                sub = vals.dropna()
                if len(sub) >= N_HOLD + 2:
                    thr = sub.quantile(1.0 - EXCLUDE_PCT)
                    keep = [c for c in keep if sub.get(c, -np.inf) <= thr]
        keep.sort(key=lambda c: dict(eligible)[c])
        if len(keep) >= N_HOLD:
            picks_all[pd.Timestamp(d)] = keep[:N_HOLD]

    # 组合净值
    def backtest(picks):
        nav = 1.0
        rets = []
        dates = sorted(picks.keys())
        for i in range(len(dates) - 1):
            d0, d1 = dates[i], dates[i + 1]
            codes = picks[d0]
            e0 = panel.loc[pd.Timestamp(d0) + pd.Timedelta(days=0)]
            # 下期 T+1 开卖出：用 d1 次一交易日开
            ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
            d0_i = ti[pd.Timestamp(d0)]
            d1_i = ti[pd.Timestamp(d1)]
            e_buy = trade_dates[d0_i + 1] if d0_i + 1 < len(trade_dates) else None
            e_sell = trade_dates[d1_i + 1] if d1_i + 1 < len(trade_dates) else None
            if e_buy is None or e_sell is None:
                continue
            buy_o = panel.loc[e_buy, "open"]
            sell_o = panel.loc[e_sell, "open"]
            rs = []
            for c in codes:
                bo, so = buy_o.get(c), sell_o.get(c)
                if bo and so and bo > 0 and so > 0:
                    rs.append(so / bo - 1.0)
            if rs:
                rets.append(float(np.mean(rs)))
        if not rets:
            return {"n": 0, "cagr": 0, "mdd": 0, "calmar": 0, "sharpe": 0}
        navs = np.cumprod(np.array(rets) + 1.0)
        nav = navs[-1]
        years = len(rets) / 4.0
        cagr = nav ** (1.0 / years) - 1.0 if years > 0 and nav > 0 else 0.0
        peak = np.maximum.accumulate(np.concatenate([[1.0], navs]))
        mdd = float((navs / peak[1:] - 1.0).min())
        mu = np.mean(rets)
        sd = np.std(rets) if len(rets) > 1 else 1e-9
        sharpe = mu / sd * np.sqrt(4.0) if sd > 0 else 0.0
        return {"n": len(rets), "cagr": cagr, "mdd": mdd,
                "calmar": cagr / abs(mdd) if mdd < 0 else 0, "sharpe": sharpe}

    # 分段
    def seg_split(picks, a, b):
        a, b = pd.Timestamp(a), pd.Timestamp(b)
        return {d: v for d, v in picks.items() if a <= pd.Timestamp(d) <= b}

    lines = []
    lines.append("# 排雷 ablation：ATR 合格域排除条件对比\n")
    lines.append("- 生成: %s | 载体: ATR合格域(ATR%%<6+换手1-8%%+真实价<50+ROE>0+动量12-1>0) 季频 ATR%%最低前%d 等权" % (time.strftime("%Y-%m-%d %H:%M:%S"), N_HOLD))
    lines.append("- 前向: 期i T+1开 -> 期i+1 T+1开 | 排除: 因子值最高 %d%% 暴露组剔除（同 MAX5 0.20 档）\n" % (EXCLUDE_PCT * 100))
    lines.append("| 配置 | 全期CAGR | 全期回撤 | 卡玛 | 夏普 | 2019-22CAGR | 2023-26CAGR | vs基线CAGR |")
    lines.append("|---|---|---|---|---|---|---|---|")

    configs = [("基线", picks_base)]
    for fn in EXCLUDE_FACTORS:
        configs.append(("排除%s" % fn, picks_excl[fn]))
    configs.append(("组合排除4", picks_all))

    base_cagr = None
    for name, picks in configs:
        r_full = backtest(picks)
        r_early = backtest(seg_split(picks, "2019-01-01", "2022-12-31"))
        r_late = backtest(seg_split(picks, "2023-01-01", "2026-12-31"))
        if name == "基线":
            base_cagr = r_full["cagr"]
        delta = (r_full["cagr"] - base_cagr) * 100 if base_cagr is not None else 0.0
        lines.append("| %s | %.2f%% | %.2f%% | %.2f | %.2f | %.2f%% | %.2f%% | %+.2fpp |"
                     % (name, r_full["cagr"] * 100, r_full["mdd"] * 100,
                        r_full["calmar"], r_full["sharpe"],
                        r_early["cagr"] * 100, r_late["cagr"] * 100, delta))
    lines.append("\n- 每期选股期数: 基线=%d" % len(picks_base))
    for fn in EXCLUDE_FACTORS:
        lines.append("  - %s: %d 期" % (fn, len(picks_excl[fn])))
    lines.append("  - 组合: %d 期" % len(picks_all))

    out = os.path.join(HERE, "exclude_ablation_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("\n报告: %s | 总用时 %.0fs" % (out, time.time() - t0))


if __name__ == "__main__":
    main()
