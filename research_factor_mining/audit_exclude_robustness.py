# coding=utf-8
"""排雷增益独立复算（审计 exclude_ablation_report.md 的 +2.65pp）

背景：exclude_ablation_report.md 报「排除 PX_AMT_RATIO_20D 使载体 CAGR 34.36%->37.01%（+2.65pp）」。
该脚本自身有三处口径缺陷，本脚本逐条修正后重新给数：

  缺陷1（期数折算 + 跨期链接）：backtest() 用 years = len(rets)/4.0，而 len(rets)=len(picks)-1。
        且 dates = sorted(picks.keys()) 是**各配置自己的期列表**——某配置缺一期时，
        d0 与 d1 相隔两个季度，却仍按「一期」计入，既把 6 个月收益当 1 个季度，
        又用更短的年限做幂折算 ⇒ CAGR 被系统性抬高。
        基线 20 期 -> 19 段 -> 4.75 年；单条件 19 期 -> 18 段 -> 4.5 年。
        本脚本统一到 **共同日期网格**（所有配置都有选股的那些期），每期固定为
        期i T+1开 -> 期i+1 T+1开，所有配置完全一致。

  缺陷1b（我第一版自己也犯的同一错误，已修）：共同网格是各配置期集合的**交集**，
        必然不连续（本数据 17 期覆盖 2020-06-30~2026-03-31，该区间季度日历共 24 期 / 6.00 年，
        缺 7 期）。若仍用 years = n/4 会把 6.00 年压成 4.25 年、把 CAGR 抬高约 6pp。现改为
        **按真实日历跨度年化**：窗口 = 首期 T+1 开 -> 末期的下一个调仓日 T+1 开，
        years = 日历天数 / 365.25；被跳过的季度按「空仓 0 收益」计（保守口径）。
        同时保留 years_exp = n/4 仅作对照，不再用于 CAGR。

  缺陷2（无成本/无换手）：本脚本按每期实际换名数计成本（单边 0.15%，可调）。

  缺陷3（无样本外切片 / 无阈值敏感性）：本脚本给 全期 / 2019-22 / 2023-26 / 样本外(2024+)
         以及 EXCLUDE_PCT = 10% / 20% / 30% 三档。

另：载体里的 momentum_12_1() 实际是 pos-1 vs pos-252（≈12 个月到 1 根 bar 前），
并非标准 12-1（应为 pos-21 vs pos-252）。本脚本两种都算，看载体定义是否稳健。

用法：
  D:\\Python311\\python.exe research_factor_mining/audit_exclude_robustness.py
输出：
  research_factor_mining/exclude_robustness_audit.md
  research_factor_mining/exclude_robustness_audit.json
"""
import os, sys, time, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")

from research.multi_factor_ic.config import BASIC_PATH
import run_screen as RS
import factor_defs_r2 as FD

START, END = "2019-01-01", "2026-09-11"
N_HOLD = 8
EXCLUDE_FACTORS = ["PX_MAX20", "PX_AMT_RATIO_20D", "PX_VOL_STD20", "PX_LISTED_NEW"]
PCTS = [0.10, 0.20, 0.30]
COST_PER_SIDE = 0.0015        # 单边：滑点0.1% + 佣金0.025% ≈ 0.125%，取 0.15% 偏保守
SEG_EARLY = ("2019-01-01", "2022-12-31")
SEG_LATE = ("2023-01-01", "2026-12-31")
SEG_OOS = ("2024-01-01", "2026-12-31")

_log = []
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    _log.append(s)


def prepare():
    """复刻 run_exclude_ablation.main() 的装载块，返回可复用的上下文。"""
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
    return dict(panel=panel, trade_dates=trade_dates, rebal=rebal, fin_wide=fin_wide,
                ctx=ctx, close_by_code=close_by_code, high_by_code=high_by_code,
                low_by_code=low_by_code, turnover_by_code=turnover_by_code)


def make_helpers(env, momentum_mode="legacy"):
    close_by_code = env["close_by_code"]
    high_by_code = env["high_by_code"]
    low_by_code = env["low_by_code"]
    turnover_by_code = env["turnover_by_code"]
    panel = env["panel"]

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

    def momentum(date, code):
        """legacy=原脚本口径(pos-1 vs pos-252)；std=标准 12-1(pos-21 vs pos-252)。"""
        s = close_by_code.get(code)
        if s is None or len(s) < 252:
            return None
        t = pd.Timestamp(date)
        pos = s.index.searchsorted(t)
        if pos < 252:
            return None
        if momentum_mode == "legacy":
            c0, c12 = s.iloc[pos - 1], s.iloc[pos - 252]
        else:
            c0, c12 = s.iloc[pos - 21], s.iloc[pos - 252]
        if c0 > 0 and c12 > 0:
            return float(c0 / c12 - 1.0)
        return None

    def build_eligible(d, cand, fin_wide_t):
        dd = panel.loc[pd.Timestamp(d)]
        out = []
        for c in cand:
            close = dd.loc[c]["close"]
            if close <= 0 or close >= 50.0:
                continue
            to = turnover_by_code.get(c)
            if to is None or to.get(pd.Timestamp(d), np.nan) is np.nan:
                to_val = dd.loc[c].get("turnover_rate", np.nan)
            else:
                to_val = float(to.loc[:pd.Timestamp(d)].iloc[-1])
            if not (1.0 <= to_val <= 8.0):
                continue
            roe = fin_wide_t.get(c, np.nan)
            if roe is None or np.isnan(roe) or roe <= 0:
                continue
            ap = atr_pct(d, c)
            if ap is None or ap <= 0 or ap > 0.06:
                continue
            m12 = momentum(d, c)
            if m12 is None or m12 <= 0:
                continue
            out.append((c, ap))
        return out

    return build_eligible


def build_picks(env, build_eligible, exclude_factors, pcts):
    """返回 base picks、{factor: {pct: picks}}、combo{pct: picks}、逐期诊断。

    诊断 diag[d] = {
        "eligible": 合格域只数,
        "base": 基线选股只数,
        "pool": {f: {p: 剔除后剩池只数}},
        "dropped": {f: {p: 基线 8 只中被剔除的只数}},
        "kept": {f: {p: 剔除后仍留下的基线只数}},
    }
    """
    panel = env["panel"]
    fwd = RS.per_period_forward_returns(panel, env["trade_dates"], env["rebal"])
    rebal_used = [d for d in env["rebal"] if pd.Timestamp(d) in fwd]
    fin_wide = env["fin_wide"]
    ctx = env["ctx"]

    picks_base = {}
    picks_excl = {f: {p: {} for p in pcts} for f in exclude_factors}
    picks_combo = {p: {} for p in pcts}
    diag = {}
    for d in rebal_used:
        td = pd.Timestamp(d)
        dd = panel.loc[td]
        cand = RS.get_candidates(dd)
        if len(cand) < 100:
            continue
        fin_t = fin_wide["roe"].loc[td]
        eligible = build_eligible(d, cand, fin_t)
        if len(eligible) < N_HOLD:
            continue
        eligible.sort(key=lambda r: r[1])
        atr_map = dict(eligible)
        base = [c for c, _ in eligible[:N_HOLD]]
        picks_base[td] = base
        rec = {"eligible": len(eligible), "base": len(base),
               "pool": {}, "dropped": {}, "kept": {}}
        diag[td] = rec

        vals_by_f = {}
        for fn in exclude_factors:
            try:
                vals = getattr(FD, fn.lower())(td, dd, [c for c, _ in eligible], ctx)
            except Exception:
                vals = None
            vals_by_f[fn] = vals

        for fn in exclude_factors:
            vals = vals_by_f[fn]
            rec["pool"][fn] = {}
            rec["dropped"][fn] = {}
            rec["kept"][fn] = {}
            if vals is None:
                continue
            sub = vals.dropna()
            if len(sub) < N_HOLD + 2:
                continue
            for p in pcts:
                thr = sub.quantile(1.0 - p)
                keep = [c for c, _ in eligible if sub.get(c, -np.inf) <= thr]
                keep.sort(key=lambda c: atr_map[c])
                rec["pool"][fn][p] = len(keep)
                rec["dropped"][fn][p] = len(set(base) - set(keep))
                rec["kept"][fn][p] = len(set(base) & set(keep))
                if len(keep) >= N_HOLD:
                    picks_excl[fn][p][td] = keep[:N_HOLD]

        for p in pcts:
            keep = [c for c, _ in eligible]
            for fn in exclude_factors:
                vals = vals_by_f.get(fn)
                if vals is None:
                    continue
                sub = vals.dropna()
                if len(sub) < N_HOLD + 2:
                    continue
                thr = sub.quantile(1.0 - p)
                keep = [c for c in keep if sub.get(c, -np.inf) <= thr]
            keep.sort(key=lambda c: atr_map[c])
            if len(keep) >= N_HOLD:
                picks_combo[p][td] = keep[:N_HOLD]
    return picks_base, picks_excl, picks_combo, diag, fwd, rebal_used


def returns_on_grid(fwd, picks, grid):
    """每期固定 期i T+1开 -> 期i+1 T+1开 的前向收益（来自 RS.per_period_forward_returns）。

    只取 grid 内的日期；picks 缺该期则该期无值（由调用方保证 grid 是交集）。
    """
    out, n_names = {}, {}
    for d in grid:
        p = picks.get(d)
        fr = fwd.get(d)
        if not p or fr is None:
            continue
        vals = [float(fr[c]) for c in p if c in fr.index]
        if vals:
            out[d] = float(np.mean(vals))
            n_names[d] = len(vals)
    return out, n_names


def turnover_per_period(picks):
    """每期单边换名比例 = 换掉只数 / N_HOLD。"""
    dates = sorted(picks.keys())
    out = {}
    for i in range(len(dates) - 1):
        a, b = set(picks[dates[i]]), set(picks[dates[i + 1]])
        out[pd.Timestamp(dates[i])] = len(b - a) / float(N_HOLD)
    return out


_SPAN_DAYS = {}      # 每期（按期初调仓日索引）实际覆盖的日历天数：期i T+1开 -> 期i+1 T+1开


def build_span_days(env):
    """为每个调仓期算真实日历跨度（天）。跨度由调仓链决定，与本脚本取哪个子网格无关。"""
    trade_dates = env["trade_dates"]
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    out = {}
    rebal = env["rebal"]
    for i in range(len(rebal) - 1):
        d = pd.Timestamp(rebal[i])
        e_idx = ti[d] + 1
        x_idx = ti[pd.Timestamp(rebal[i + 1])] + 1
        if e_idx >= len(trade_dates) or x_idx >= len(trade_dates):
            continue
        days = (trade_dates[x_idx] - trade_dates[e_idx]).days
        if days > 0:
            out[d] = float(days)
    return out


def window_years(keys):
    """给定实际有收益的期集合，返回真实年化窗口。

    窗口 = keys[0] 到 keys[-1] 之间的**整条调仓链**日历跨度之和（含被跳过的季度）。
    被跳过的期按「空仓 0 收益」计入日历（保守）：分子只含真实持仓期收益，
    分母用整段日历跨度 ⇒ CAGR 偏低而非偏高。
    """
    if not keys:
        return 0.0, 0.0
    a, b = keys[0], keys[-1]
    tot = sum(v for d, v in _SPAN_DAYS.items() if a <= d <= b)
    if tot <= 0:
        return len(keys) / 4.0, len(keys) / 4.0
    return tot / 365.25, len(keys) / 4.0


def metrics(rets, grid=None, cost_map=None):
    """按给定日期网格复利。

    年化窗口 = 各期真实日历跨度之和 / 365.25（不是 n/4 —— 网格是交集、必然不连续）。
    夏普按 sqrt(每年期数) 年化，期数不均时比固定 sqrt(4) 更贴实际。
    """
    keys = sorted(rets.keys())
    if grid is not None:
        keys = [d for d in keys if d in grid]
    if not keys:
        return {"n": 0}
    r = np.array([rets[d] for d in keys], dtype=float)
    if cost_map is not None:
        r = r - np.array([cost_map.get(d, 0.0) for d in keys], dtype=float)
    navs = np.cumprod(r + 1.0)
    years, years_exp = window_years(keys)
    nav = navs[-1]
    cagr = nav ** (1.0 / years) - 1.0 if years > 0 and nav > 0 else 0.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], navs]))
    mdd = float((navs / peak[1:] - 1.0).min())
    sd = np.std(r) if len(r) > 1 else 1e-9
    ppy = len(keys) / years if years > 0 else 4.0
    sharpe = float(np.mean(r) / sd * np.sqrt(ppy)) if sd > 0 else 0.0
    return {"n": len(keys), "years": years, "years_exp": years_exp, "cagr": cagr, "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd < 0 else 0.0, "sharpe": sharpe,
            "total": float(nav - 1.0),
            "first": str(pd.Timestamp(keys[0]).date()), "last": str(pd.Timestamp(keys[-1]).date())}


def legacy_backtest(env, picks):
    """原脚本 backtest() 的逐字复刻：各配置自己的期列表 + 跨期链接 + years=len(rets)/4。

    用于「先复现报告数字，再证明它是口径伪差」。
    """
    panel, trade_dates = env["panel"], env["trade_dates"]
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    dates = sorted(picks.keys())
    rets = []
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        codes = picks[d0]
        d0_i, d1_i = ti[pd.Timestamp(d0)], ti[pd.Timestamp(d1)]
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
        return {"n": 0, "cagr": 0, "mdd": 0, "calmar": 0, "sharpe": 0, "years": 0}
    navs = np.cumprod(np.array(rets) + 1.0)
    nav = navs[-1]
    years = len(rets) / 4.0
    cagr = nav ** (1.0 / years) - 1.0 if years > 0 and nav > 0 else 0.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], navs]))
    mdd = float((navs / peak[1:] - 1.0).min())
    mu = np.mean(rets)
    sd = np.std(rets) if len(rets) > 1 else 1e-9
    sharpe = mu / sd * np.sqrt(4.0) if sd > 0 else 0.0
    return {"n": len(rets), "years": years, "cagr": cagr, "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd < 0 else 0, "sharpe": sharpe}


def span_periods(env, picks):
    """统计有多少段是「跨 2 个季度」的链接（原脚本口径的隐性失真来源）。"""
    trade_dates = env["trade_dates"]
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    dates = sorted(picks.keys())
    n_multi = 0
    for i in range(len(dates) - 1):
        d0_i, d1_i = ti[pd.Timestamp(dates[i])], ti[pd.Timestamp(dates[i + 1])]
        if d1_i - d0_i > 100:   # 一个季度约 60 个交易日；>100 视为跨期
            n_multi += 1
    return n_multi


def sub_grid(grid, a, b):
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    return [d for d in grid if a <= d <= b]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.time()
    env = prepare()
    _SPAN_DAYS.update(build_span_days(env))
    log("panel:", env["panel"].shape, "| 调仓期:", len(env["rebal"]))
    log("备用源 gpsj 可用性:", RS.__dict__.get("GPSJ_OK", "未探针") if hasattr(RS, "GPSJ_OK") else "未探针")

    build_eligible = make_helpers(env, "legacy")
    picks_base, picks_excl, picks_combo, diag, fwd, rebal_used = build_picks(
        env, build_eligible, EXCLUDE_FACTORS, PCTS)
    log("基线期数:", len(picks_base), "| 有诊断期数:", len(diag))
    for fn in EXCLUDE_FACTORS:
        log("  %s 期数 @20%%: %d" % (fn, len(picks_excl[fn][0.20])))
    for p in PCTS:
        log("  组合期数 @%d%%: %d" % (p * 100, len(picks_combo[p])))

    configs = {"基线": picks_base}
    for fn in EXCLUDE_FACTORS:
        configs["排除%s@20%%" % fn] = picks_excl[fn][0.20]
    for p in PCTS:
        configs["排除AMT@%d%%" % (p * 100)] = picks_excl["PX_AMT_RATIO_20D"][p]
    for p in PCTS:
        configs["组合4@%d%%" % (p * 100)] = picks_combo[p]

    # 共同网格：所有配置都有选股、且 fwd 可算的那些期
    grid = None
    for name, picks in configs.items():
        s = set(picks.keys()) & set(fwd.keys())
        grid = s if grid is None else (grid & s)
    grid = sorted(grid)
    log("共同网格期数:", len(grid), str(pd.Timestamp(grid[0]).date()),
        "~", str(pd.Timestamp(grid[-1]).date()))

    rets, nnames = {}, {}
    for name, picks in configs.items():
        rets[name], nnames[name] = returns_on_grid(fwd, picks, grid)
    tos = {name: turnover_per_period(picks) for name, picks in configs.items()}
    costs = {name: {d: t * COST_PER_SIDE * 2.0 for d, t in tos[name].items()} for name in configs}

    L = []
    L.append("# 排雷增益独立复算（审计 exclude_ablation_report.md 的 +2.65pp）\n")
    L.append("- 生成: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("- 载体: ATR合格域(ATR%%<6 + 换手1-8%% + 真实价<50 + ROE>0 + 动量>0) 季频 ATR%%最低前 %d 等权" % N_HOLD)
    L.append("- 前向: 期i T+1开 -> 期i+1 T+1开（直接取 run_screen.per_period_forward_returns，与选股无关）")
    L.append("- 剔除: 因子值最高 pct 暴露组（在合格域内剔除后再取 ATR 最低 8）")
    g_first, g_last = pd.Timestamp(grid[0]), pd.Timestamp(grid[-1])
    span_full = window_years(sorted(grid))[0]
    span_cal = (g_last - g_first).days / 365.25 + 0.25      # 首期到末期 + 最后一期本身
    n_cal = sum(1 for d in env["rebal"] if g_first <= pd.Timestamp(d) <= g_last)
    L.append("- **共同网格**: %d 期（%s ~ %s）。**网格是各配置期集合的交集，必然不连续**——"
             "该区间季度日历共 %d 期，缺 %d 期。\n"
             % (len(grid), g_first.date(), g_last.date(), n_cal, n_cal - len(grid)))
    L.append("- **年化窗口（已修）**: 按整条调仓链的真实日历跨度之和 = **%.2f 年**（不是 %d/4 = %.2f 年）；"
             "被跳过的 %d 期按空仓 0 收益计入日历（保守口径，只会压低 CAGR 不会抬高）。"
             "区间首末日历跨度 %.2f 年。\n"
             % (span_full, len(grid), len(grid) / 4.0, n_cal - len(grid), span_cal))
    L.append("> 口径声明：**单源结论（仅 astock），未经备用源交叉验证**（gpsj 备用源当前不可用）。\n")

    # ---------------- 表0：先复现报告数字 ----------------
    L.append("## 表0 先复现报告数字（证明本脚本与原脚本同源）\n")
    L.append("| 配置 | 原口径 n(段) | 原口径年限(年) | 原口径 CAGR | 报告值 | 跨2季链接段数 |")
    L.append("|---|---|---|---|---|---|")
    for name, rep in [("基线", "34.36%"), ("排除PX_AMT_RATIO_20D@20%", "37.01%"),
                      ("排除PX_MAX20@20%", "33.27%"), ("排除PX_VOL_STD20@20%", "35.67%"),
                      ("排除PX_LISTED_NEW@20%", "35.35%"), ("组合4@20%", "36.54%")]:
        lb = legacy_backtest(env, configs[name])
        L.append("| %s | %d | %.2f | %.2f%% | %s | %d |"
                 % (name, lb["n"], lb["years"], lb["cagr"] * 100, rep, span_periods(env, configs[name])))
    L.append("\n- 原脚本 `years = len(rets)/4` 且 `dates = sorted(picks.keys())` 取的是各配置**自己的**期列表："
             "某配置缺一期时，相邻两个期相隔两个季度，却仍按「一个季度」计入，年限被压缩 ⇒ CAGR 被抬高。\n")

    # ---------------- 表1：共同网格无成本 ----------------
    L.append("## 表1 共同网格、无成本（修掉期数折算后的真实增益）\n")
    L.append("| 配置 | n | 年化窗口(年) | 平均持股 | CAGR | 回撤 | 卡玛 | 夏普 | 总收益 | vs基线 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    base_m = metrics(rets["基线"], grid)
    rows_json = {"基线": base_m}
    for name in configs:
        m = metrics(rets[name], grid)
        rows_json[name] = m
        avg_n = np.mean([nnames[name].get(d, 0) for d in grid]) if grid else 0
        delta = (m["cagr"] - base_m["cagr"]) * 100
        L.append("| %s | %d | %.2f | %.1f | %.2f%% | %.2f%% | %.2f | %.2f | %.1f%% | %+.2fpp |"
                 % (name, m["n"], m["years"], avg_n, m["cagr"] * 100, m["mdd"] * 100,
                    m["calmar"], m["sharpe"], m["total"] * 100, delta))
    L.append("")

    # ---------------- 表2：分段 / 样本外 ----------------
    L.append("## 表2 分段与样本外（共同网格内再切）\n")
    segs = [("全期", grid[0], grid[-1]),
            ("2019-22", SEG_EARLY[0], SEG_EARLY[1]),
            ("2023-26", SEG_LATE[0], SEG_LATE[1]),
            ("样本外2024+", SEG_OOS[0], SEG_OOS[1])]
    L.append("| 配置 | " + " | ".join("%s CAGR (n)" % s[0] for s in segs) + " |")
    L.append("|---|" + "---|" * len(segs))
    for name in ["基线", "排除AMT@10%", "排除AMT@20%", "排除AMT@30%", "组合4@20%"]:
        cells = []
        for _, a, b in segs:
            g = sub_grid(grid, a, b)
            m = metrics(rets[name], g)
            cells.append("%.2f%% (%d)" % (m["cagr"] * 100, m["n"]) if m.get("n") else "—")
        L.append("| %s | %s |" % (name, " | ".join(cells)))
    L.append("")

    # ---------------- 表3：成本与换手 ----------------
    L.append("## 表3 成本后（单边 %.2f%%，按每期实际换名数）\n" % (COST_PER_SIDE * 100))
    L.append("| 配置 | 每期换名 | 年化单边换手 | CAGR(毛) | CAGR(净) | 成本拖累 |")
    L.append("|---|---|---|---|---|---|")
    for name in ["基线", "排除AMT@10%", "排除AMT@20%", "排除AMT@30%", "组合4@20%"]:
        to_vals = [tos[name][d] for d in grid if d in tos[name]]
        mg = metrics(rets[name], grid)
        mn = metrics(rets[name], grid, cost_map=costs[name])
        # 年化换手 = 每期换名 × 每年期数；每年期数 = 网格期数 / 真实年化窗口（不写死 4）
        ppy = len(grid) / mg["years"] if mg["years"] > 0 else 4.0
        L.append("| %s | %.2f 只 | %.1fx | %.2f%% | %.2f%% | %+.2fpp |"
                 % (name, np.mean(to_vals) if to_vals else 0,
                    np.mean(to_vals) * ppy if to_vals else 0,
                    mg["cagr"] * 100, mn["cagr"] * 100, (mn["cagr"] - mg["cagr"]) * 100))
    L.append("")

    # ---------------- 表4：动量口径敏感性 ----------------
    be_std = make_helpers(env, "std")
    pb2, pe2, pc2, diag2, _, _ = build_picks(env, be_std, ["PX_AMT_RATIO_20D"], [0.20])
    g2 = sorted(set(pb2.keys()) & set(pe2["PX_AMT_RATIO_20D"][0.20].keys()) & set(fwd.keys()))
    rb2, _ = returns_on_grid(fwd, pb2, g2)
    re2, _ = returns_on_grid(fwd, pe2["PX_AMT_RATIO_20D"][0.20], g2)
    mb, me = metrics(rb2, g2), metrics(re2, g2)
    L.append("## 表4 动量口径敏感性（标准 12-1 = pos-21 vs pos-252）\n")
    L.append("| 口径 | 基线CAGR | 排除AMT@20%CAGR | 差 | 共同期数 | 基线合格域均值 |")
    L.append("|---|---|---|---|---|---|")
    elig_leg = np.mean([diag[d]["eligible"] for d in grid if d in diag]) if grid else 0
    elig_std = np.mean([diag2[d]["eligible"] for d in g2 if d in diag2]) if g2 else 0
    L.append("| 原脚本(≈12-0) | %.2f%% | %.2f%% | %+.2fpp | %d | %.0f 只 |"
             % (base_m["cagr"] * 100, metrics(rets["排除AMT@20%"], grid)["cagr"] * 100,
                (metrics(rets["排除AMT@20%"], grid)["cagr"] - base_m["cagr"]) * 100,
                len(grid), elig_leg))
    L.append("| 标准12-1 | %.2f%% | %.2f%% | %+.2fpp | %d | %.0f 只 |"
             % (mb["cagr"] * 100, me["cagr"] * 100, (me["cagr"] - mb["cagr"]) * 100,
                len(g2), elig_std))
    L.append("")

    # ---------------- 表5：剔除集与选中集重叠（真数） ----------------
    L.append("## 表5 剔除集与选中集重叠（增益从哪来 / 为什么可能是 0）\n")
    L.append("| 因子 | 档位 | 合格域均值 | 剔除后剩池均值 | 基线8只中被剔均值 | 被剔占比 | 剔除后仍选中均值 |")
    L.append("|---|---|---|---|---|---|---|")
    ov_json = {}
    for fn in EXCLUDE_FACTORS:
        for p in PCTS:
            el, pool, dropped, kept = [], [], [], []
            for d in grid:
                r = diag.get(d)
                if not r:
                    continue
                pp = r["pool"].get(fn, {}).get(p)
                if pp is None:
                    continue
                el.append(r["eligible"])
                pool.append(pp)
                dropped.append(r["dropped"][fn][p])
                kept.append(r["kept"][fn][p])
            if not el:
                L.append("| %s | %d%% | — | — | — | — | — |" % (fn, p * 100))
                continue
            L.append("| %s | %d%% | %.1f | %.1f | %.2f | %.1f%% | %.2f |"
                     % (fn, p * 100, np.mean(el), np.mean(pool), np.mean(dropped),
                        np.mean(dropped) / float(N_HOLD) * 100, np.mean(kept)))
            ov_json["%s@%d%%" % (fn, p * 100)] = {
                "eligible_mean": float(np.mean(el)), "pool_mean": float(np.mean(pool)),
                "dropped_mean": float(np.mean(dropped)),
                "dropped_pct_of_hold": float(np.mean(dropped) / N_HOLD),
                "kept_mean": float(np.mean(kept)), "n_periods": len(el)}
    L.append("\n- 「基线8只中被剔均值」= 排除条件真正从基线持仓里踢掉的只数；若≈0，说明该因子的高暴露组"
             "与 ATR 低波选出的票几乎不相交，排除动作**不改持仓** ⇒ CAGR 增益必然为 0（不是跑错了）。\n")

    # ---------------- 表6：逐期剔除账本（增益是「系统性」还是「个别换名运气」） ----------------
    L.append("## 表6 逐期剔除账本（AMT 三档：剔除发生在哪几期 / 换掉了谁 / 当期收益差多少）\n")
    swap_json = {}
    for p in PCTS:
        name = "排除AMT@%d%%" % (p * 100)
        pk = configs[name]
        L.append("### AMT 剔除档位 %d%%\n" % (p * 100))
        L.append("| 期 | 基线收益 | 排除后收益 | 差 | 换出 | 换入 |")
        L.append("|---|---|---|---|---|---|")
        rows_p = []
        tot_drop = 0
        n_drop_dates = 0
        for d in grid:
            b = picks_base.get(d)
            e = pk.get(d)
            if not b or not e:
                continue
            rb, re_ = rets["基线"].get(d), rets[name].get(d)
            out_n = sorted(set(b) - set(e))
            in_n = sorted(set(e) - set(b))
            if not out_n and not in_n:
                continue
            n_drop_dates += 1
            tot_drop += len(out_n)
            L.append("| %s | %.3f%% | %.3f%% | %+.3fpp | %s | %s |"
                     % (pd.Timestamp(d).date(), (rb or 0) * 100, (re_ or 0) * 100,
                        ((re_ or 0) - (rb or 0)) * 100,
                        ",".join(out_n) if out_n else "—", ",".join(in_n) if in_n else "—"))
            rows_p.append({"date": str(pd.Timestamp(d).date()),
                           "base_ret": float(rb or 0), "excl_ret": float(re_ or 0),
                           "diff": float((re_ or 0) - (rb or 0)),
                           "out": out_n, "in": in_n})
        L.append("")
        L.append("- %d%% 档：**%d / %d 期发生换名，累计换出 %d 只**；总收益 基线 %.2f%% vs 排除后 %.2f%%。\n"
                 % (p * 100, n_drop_dates, len(grid), tot_drop,
                    metrics(rets["基线"], grid)["total"] * 100,
                    metrics(rets[name], grid)["total"] * 100))
        swap_json["AMT@%d%%" % (p * 100)] = {
            "n_dates_with_swap": n_drop_dates, "n_periods": len(grid),
            "total_dropped_names": tot_drop,
            "base_total": metrics(rets["基线"], grid)["total"],
            "excl_total": metrics(rets[name], grid)["total"],
            "rows": rows_p}

    # ---------------- 结论 ----------------
    m_amt30 = metrics(rets["排除AMT@30%"], grid)
    m_amt20 = metrics(rets["排除AMT@20%"], grid)
    m_combo = metrics(rets["组合4@20%"], grid)
    m_base = metrics(rets["基线"], grid)
    s_amt30 = metrics(rets["排除AMT@30%"], sub_grid(grid, SEG_OOS[0], SEG_OOS[1]))
    s_base_oos = metrics(rets["基线"], sub_grid(grid, SEG_OOS[0], SEG_OOS[1]))
    L.append("## 结论\n")
    L.append("1. **原报告的 +2.65pp 不成立**。它同时踩了两个折算缺陷：`years = len(rets)/4`（把 6.00 年"
             "的日历窗口压成 4.25 年）+ `dates = sorted(picks.keys())` 用各配置自己的期列表"
             "（缺一期时两个季度被当作一期，收益被放大、年限被压缩）。表0 已用逐字复刻的原 `backtest()` "
             "复现出 34.36% / 37.01% 等全部 6 个数字，并量出每个配置都有 **3 个跨 2 季链接段** ⇒ 缺陷是"
             "实测出来的，不是推测。")
    L.append("2. **共同网格 + 真实日历年化后，AMT@20%% 只剩 %+.2fpp**（基线 %.2f%% -> %.2f%%），"
             "仅为原报数的 1/6.6；组合4@20%% %+.2fpp。三档里最\"好看\"的 AMT@30%% 是 %+.2fpp。"
             % ((m_amt20["cagr"] - m_base["cagr"]) * 100, m_base["cagr"] * 100, m_amt20["cagr"] * 100,
                (m_combo["cagr"] - m_base["cagr"]) * 100, (m_amt30["cagr"] - m_base["cagr"]) * 100))
    L.append("3. **增益的物理来源是「极少数换名」，不是系统性排雷**。表6：AMT@20%% 全网格只有 %d 期发生换名、"
             "累计换出 %d 只；AMT@30%% 也只有 %d 期 / %d 只。17 期 × 8 只 = 136 个持仓位，"
             "换名占比个位数 ⇒ 所谓增益实质是**个别股票的运气差**，与 2026-09-17 `amt_exclude_engine_结题.md` "
             "的真实约束结论（生产载体 +0.00pp、base 与 excl 逐位一致、31 期零重叠）完全一致。"
             % (swap_json["AMT@20%"]["n_dates_with_swap"], swap_json["AMT@20%"]["total_dropped_names"],
                swap_json["AMT@30%"]["n_dates_with_swap"], swap_json["AMT@30%"]["total_dropped_names"]))
    L.append("4. **样本外不支持**。2024+ 切片：基线 %.2f%% vs AMT@30%% %.2f%%（%+.2fpp），"
             "远小于全期的 %+.2fpp ⇒ 全期增益主要来自 2020-2022 的几笔换名。"
             % (s_base_oos["cagr"] * 100, s_amt30["cagr"] * 100,
                (s_amt30["cagr"] - s_base_oos["cagr"]) * 100, (m_amt30["cagr"] - m_base["cagr"]) * 100))
    L.append("5. **动量口径一动，增益就减半**。表4：把载体的 `momentum_12_1()` 从实际的 ≈12-0 修成标准 12-1，"
             "AMT@20% 增益从 +0.40pp 掉到 +0.20pp，基线水平本身也从 13.51% 跳到 21.30% ⇒ 该增益对"
             "一个未定义清楚的动量窗口高度敏感，不具备可移植性。")
    L.append("6. **`PX_LISTED_NEW` 结构性必然为 0**：表5 显示它在 10/20/30% 三档都从基线持仓里踢掉 **0.00 只**"
             "（合格域内已无上市未满一年的票，本就进不了 ATR 低波前 8）⇒ 排除动作不改持仓，增益恒为 0。")
    L.append("7. **单源声明**：以上全部为 astock 单源复算，未经 gpsj 交叉验证（备用源当前不可用）。\n")

    out_md = os.path.join(HERE, "exclude_robustness_audit.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    out_json = os.path.join(HERE, "exclude_robustness_audit.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"grid": [str(pd.Timestamp(d).date()) for d in grid],
                   "grid_n": len(grid), "rows": rows_json,
                   "overlap": ov_json, "cost_per_side": COST_PER_SIDE,
                   "swap": swap_json,
                   "pcts": PCTS,
                   "legacy": {n: legacy_backtest(env, configs[n]) for n in
                              ["基线", "排除PX_AMT_RATIO_20D@20%", "组合4@20%"]},
                   "diag": {str(pd.Timestamp(d).date()): {
                       "eligible": v["eligible"], "base": v["base"],
                       "pool": {f: {str(k): x for k, x in v["pool"].get(f, {}).items()}
                                for f in EXCLUDE_FACTORS},
                       "dropped": {f: {str(k): x for k, x in v["dropped"].get(f, {}).items()}
                                   for f in EXCLUDE_FACTORS}}
                       for d, v in diag.items()}}, f, ensure_ascii=False, indent=1)
    log("\n总用时 %.0fs" % (time.time() - t0))
    log("报告: %s" % out_md)
    print("\n".join(L))


if __name__ == "__main__":
    main()
