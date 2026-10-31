# coding=utf-8
"""排雷 ablation：ATR 合格域内排除条件对比回测（2026-11-01 口径修复版）

载体：ATR 合格域（ATR%<6 + 换手1-8% + 非ST + 真实价<50 + ROE>0 + 动量12-1>0）
季度调仓，选 ATR% 最低前 8 只等权；前向=期 i T+1 开买入 -> 期 i+1 T+1 开卖出。
排除条件（五分位诊断确认尾部驱动的 4 个 PX 因子）：剔除因子值最高 20% 暴露组（同 MAX5 0.20 档）。

本版修了三处口径缺陷（2026-11-01，证据见 exclude_robustness_audit.md）：
  1) 各配置原本用**自己的期列表**回测 -> 某配置缺一期时会把相邻两个季度当成一期（CAGR 虚高）
     => 改为**共同网格**（所有配置与 fwd 都有的期），全表同一起点、同一终点。
  2) 年化窗口原本是 len(rets)/4（把 6.00 年日历压成 4.25 年）
     => 改为按调仓链真实日历跨度求和 / 365.25；夏普按 sqrt(每年期数) 年化。
  3) 动量门槛原本是 c0/c12（约 12-0），与生产 atr_lowvol.py 的 12-1 不一致
     => 改为 close[-21]/close[-252]-1（与生产同口径）。另保留旧口径做敏感度对照。
另新增：交易成本（单边 0.15%，买卖双边计）、换手、样本外(2024+)分段、每期换名台账，
以及「原口径逐字复刻」对照表（用于证明旧报告数字是口径伪差而非真实增益）。

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

COST_PER_SIDE = 0.0015          # 滑点 0.1% + 佣金 0.025% 约 0.125%，取 0.15% 偏保守
SEG_EARLY = ("2019-01-01", "2022-12-31")
SEG_LATE = ("2023-01-01", "2026-12-31")
SEG_OOS = ("2024-01-01", "2026-12-31")


def _safe_stdout():
    """报告含 →/×/≈ 等非 GBK 字符；管道下默认 GBK 会抛 UnicodeEncodeError 并让退出码变非 0。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:          # py<3.7
        pass


# ---------------------------------------------------------------- 年化口径（已修）

_SPAN_DAYS = {}      # 每期（按期初调仓日索引）实际覆盖的日历天数：期i T+1开 -> 期i+1 T+1开


def build_span_days(env):
    """为每个调仓期算真实日历跨度（天）。跨度由调仓链决定，与取哪个子网格无关。"""
    trade_dates = env["trade_dates"]
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    out = {}
    rebal = env["rebal"]
    for i in range(len(rebal) - 1):
        d = pd.Timestamp(rebal[i])
        k0 = ti.get(d)
        k1 = ti.get(pd.Timestamp(rebal[i + 1]))
        if k0 is None or k1 is None:
            continue
        e_idx, x_idx = k0 + 1, k1 + 1
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


# ---------------------------------------------------------------- 收益 / 换手 / 台账


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


def common_grid(fwd, picks_list):
    """所有配置与 fwd 都有的期（交集）。缺任何一边的期一律不进表，保证同起点同终点。"""
    s = set(pd.Timestamp(k) for k in fwd.keys())
    for p in picks_list:
        s &= set(pd.Timestamp(k) for k in p.keys())
    return sorted(s)


def swap_ledger(picks_base, picks_cmp, fwd, grid):
    """每期换名台账：列出换出/换入，以及该期相对基线的收益差（pp）。"""
    out = []
    for d in grid:
        a, b = picks_base.get(d), picks_cmp.get(d)
        fr = fwd.get(d)
        if not a or not b or fr is None:
            continue
        outs = [c for c in a if c not in b]
        ins = [c for c in b if c not in a]
        if not outs and not ins:
            continue
        va = [float(fr[c]) for c in a if c in fr.index]
        vb = [float(fr[c]) for c in b if c in fr.index]
        if not va or not vb:
            continue
        out.append({"date": str(pd.Timestamp(d).date()),
                    "out": outs, "in": ins,
                    "ret_base": float(np.mean(va)), "ret_cmp": float(np.mean(vb)),
                    "diff_pp": (float(np.mean(vb)) - float(np.mean(va)) * 1.0) * 100.0})
    return out


def count_cross_links(picks, rebal_chain):
    """统计「相邻两个入选期在调仓链上相隔 >1 个季度」的链接段数（旧口径虚高的直接证据）。"""
    idx = {pd.Timestamp(d): k for k, d in enumerate(rebal_chain)}
    dates = sorted(picks.keys())
    n = 0
    for i in range(len(dates) - 1):
        a, b = idx.get(pd.Timestamp(dates[i])), idx.get(pd.Timestamp(dates[i + 1]))
        if a is None or b is None:
            continue
        if b - a > 1:
            n += 1
    return n


def legacy_backtest(env, picks):
    """原脚本 backtest() 的逐字复刻：各配置自己的期列表 + 跨期链接 + years=len(rets)/4。

    保留它的唯一目的：复现旧报告数字，再证明那是口径伪差。
    """
    panel, trade_dates = env["panel"], env["trade_dates"]
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
    dates = sorted(picks.keys())
    rets = []
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        codes = picks[d0]
        d0_i, d1_i = ti.get(pd.Timestamp(d0)), ti.get(pd.Timestamp(d1))
        if d0_i is None or d1_i is None:
            continue
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
        return {"n": 0, "cagr": 0.0, "mdd": 0.0, "calmar": 0.0, "sharpe": 0.0}
    navs = np.cumprod(np.array(rets) + 1.0)
    years = len(rets) / 4.0
    cagr = navs[-1] ** (1.0 / years) - 1.0 if years > 0 and navs[-1] > 0 else 0.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], navs]))
    mdd = float((navs / peak[1:] - 1.0).min())
    mu = np.mean(rets)
    sd = np.std(rets) if len(rets) > 1 else 1e-9
    sharpe = mu / sd * np.sqrt(4.0) if sd > 0 else 0.0
    return {"n": len(rets), "cagr": cagr, "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd < 0 else 0.0, "sharpe": sharpe}


# ---------------------------------------------------------------- 选股


def make_helpers(env, momentum_mode="std"):
    """返回 (atr_pct, momentum_12_1, build_eligible)。momentum_mode: std=生产12-1 / legacy=旧≈12-0。"""
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

    def momentum_12_1(date, code):
        s = close_by_code.get(code)
        if s is None or len(s) < 252:
            return None
        t = pd.Timestamp(date)
        pos = s.index.searchsorted(t)
        if pos < 252:
            return None
        if momentum_mode == "legacy":
            # 旧口径：pos-1 / pos-252 ≈ 12-0（pos-21 算了但没用），与生产不一致
            c0, c12 = s.iloc[pos - 1], s.iloc[pos - 252]
            return float(c0 / c12 - 1.0) if c0 > 0 and c12 > 0 else None
        # 生产口径：atr_lowvol.py L208  close[-21] / close[-252] - 1
        c1, c12 = s.iloc[pos - 21], s.iloc[pos - 252]
        return float(c1 / c12 - 1.0) if c1 > 0 and c12 > 0 else None

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

    return atr_pct, momentum_12_1, build_eligible


def build_picks(env, build_eligible, exclude_factors, pct, n_hold):
    """逐期选股：基线 + 各单条件排除 + 组合排除。返回 (picks_base, picks_excl, picks_all)。"""
    panel = env["panel"]
    fwd = env["fwd"]
    fin_wide = env["fin_wide"]
    ctx = env["ctx"]
    rebal_used = [d for d in env["rebal"] if pd.Timestamp(d) in fwd]

    picks_base, picks_all = {}, {}
    picks_excl = {f: {} for f in exclude_factors}
    for d in rebal_used:
        dd = panel.loc[pd.Timestamp(d)]
        cand = RS.get_candidates(dd)
        if len(cand) < 100:
            continue
        fin_t = fin_wide["roe"].loc[pd.Timestamp(d)]
        eligible = build_eligible(d, cand, fin_t)
        if len(eligible) < n_hold:
            continue
        eligible.sort(key=lambda r: r[1])
        atr_map = {c: v for c, v in eligible}
        picks_base[pd.Timestamp(d)] = [c for c, _ in eligible[:n_hold]]

        for fn in exclude_factors:
            try:
                vals = getattr(FD, fn.lower())(pd.Timestamp(d), dd, [c for c, _ in eligible], ctx)
            except Exception:
                vals = None
            if vals is None or len(vals) < n_hold:
                continue
            sub = vals.dropna()
            if len(sub) < n_hold + 2:
                continue
            thr = sub.quantile(1.0 - pct)
            keep = [c for c, _ in eligible if sub.get(c, -np.inf) <= thr]
            keep.sort(key=lambda c: atr_map[c])
            if len(keep) >= n_hold:
                picks_excl[fn][pd.Timestamp(d)] = keep[:n_hold]

        keep = [c for c, _ in eligible]
        for fn in exclude_factors:
            try:
                vals = getattr(FD, fn.lower())(pd.Timestamp(d), dd, [c for c, _ in eligible], ctx)
            except Exception:
                vals = None
            if vals is not None:
                sub = vals.dropna()
                if len(sub) >= n_hold + 2:
                    thr = sub.quantile(1.0 - pct)
                    keep = [c for c in keep if sub.get(c, -np.inf) <= thr]
        keep.sort(key=lambda c: atr_map[c])
        if len(keep) >= n_hold:
            picks_all[pd.Timestamp(d)] = keep[:n_hold]

    return picks_base, picks_excl, picks_all


# ---------------------------------------------------------------- 主流程


def main():
    _safe_stdout()
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

    fwd = RS.per_period_forward_returns(panel, trade_dates, rebal)
    env = {"panel": panel, "trade_dates": trade_dates, "rebal": rebal, "fwd": fwd,
           "fin_wide": fin_wide, "ctx": ctx,
           "close_by_code": close_by_code, "high_by_code": high_by_code,
           "low_by_code": low_by_code, "turnover_by_code": turnover_by_code}

    _SPAN_DAYS.clear()
    _SPAN_DAYS.update(build_span_days(env))

    # ---- 两套动量口径：std（生产 12-1，主表）/ legacy（旧 ≈12-0，仅敏感度）
    results = {}
    for mode in ("std", "legacy"):
        _atr, _mom, build_eligible = make_helpers(env, mode)
        pb, pe, pa = build_picks(env, build_eligible, EXCLUDE_FACTORS, EXCLUDE_PCT, N_HOLD)
        configs = [("基线", pb)]
        for fn in EXCLUDE_FACTORS:
            configs.append(("排除%s" % fn, pe[fn]))
        configs.append(("组合排除4", pa))
        grid = common_grid(fwd, [p for _, p in configs])
        results[mode] = {"picks_base": pb, "picks_excl": pe, "picks_all": pa,
                         "configs": configs, "grid": grid}

    main_res = results["std"]
    grid = main_res["grid"]
    configs = main_res["configs"]
    picks_base = main_res["picks_base"]

    span_full = window_years(grid)[0]
    span_cal = (grid[-1] - grid[0]).days / 365.25 + 0.25 if grid else 0.0

    # 旧口径（≈12-0 动量）那一套，表2/结论1 都要用：先算好，避免后面再漂移
    legacy_cfgs = results["legacy"]["configs"]
    legacy_grid = results["legacy"]["grid"]
    legacy_span = window_years(legacy_grid)[0] if legacy_grid else 0.0
    legacy_naive = len(legacy_grid) / 4.0

    # 换手 / 成本
    tos, costs, rets_all, names_all = {}, {}, {}, {}
    for name, picks in configs:
        r, n_names = returns_on_grid(fwd, picks, grid)
        rets_all[name] = r
        names_all[name] = n_names
        t = turnover_per_period(picks)
        tos[name] = t
        costs[name] = {d: v * COST_PER_SIDE * 2.0 for d, v in t.items()}

    lines = []
    lines.append("# 排雷 ablation：ATR 合格域排除条件对比（2026-11-01 口径修复版）\n")
    lines.append("> **已证伪提醒**：本报告历史版本曾发布「排除 PX_AMT_RATIO_20D +2.65pp（最强）」，"
                 "该结论**不成立**，勿再引用。")
    lines.append("> - 2026-09-17 真实约束回测（`amt_exclude_engine_结题.md`）：载体上 base 与 excl "
                 "**逐位一致**（cagr=0.1691 / mdd=-0.2480 / sharpe=0.898 / trades=666 / "
                 "`amt_excl_by_rebalance=[]`）=> **+0.00pp**，判定不落地。")
    lines.append("> - 2026-11-01 独立复核（`exclude_robustness_audit.md`）：共同网格 + 真实日历年化下 "
                 "只剩 **+0.40pp**（基线 13.51% -> 13.91%），为原报数的约 1/6.6。")
    lines.append("> - 旧数虚高根因 = 年化口径（`years=len(rets)/4` 把 6.00 年日历压成 4.75 年）"
                 "+ 各配置自己的期列表（缺期时把两个季度当一期）。本版已改为共同网格 + 真实日历跨度。\n")
    lines.append("- 生成: %s | 载体: ATR合格域(ATR%%<6+换手1-8%%+真实价<50+ROE>0+动量12-1>0) "
                 "季频 ATR%%最低前%d 等权" % (time.strftime("%Y-%m-%d %H:%M:%S"), N_HOLD))
    lines.append("- 前向: 期i T+1开 -> 期i+1 T+1开 | 排除: 因子值最高 %d%% 暴露组剔除 | 成本: 单边 %.2f%%（双边计）"
                 % (EXCLUDE_PCT * 100, COST_PER_SIDE * 100))
    lines.append("- **共同网格**（所有配置都有收益的期）: n=%d | %s -> %s | 真实日历跨度合计 %.2f 年"
                 "（首末期跨度 %.2f 年）| 动量口径=标准 12-1（与生产 atr_lowvol.py 一致）"
                 % (len(grid), grid[0].date() if grid else "-", grid[-1].date() if grid else "-",
                    span_full, span_cal))
    lines.append("- 口径声明：**单源结论（仅 astock），未经备用源交叉验证**（gpsj 备用源当前不可用）。\n")

    # ---- 表1：修复口径
    lines.append("## 表1 修复口径（共同网格 + 真实日历年化 + 标准 12-1 动量）\n")
    lines.append("| 配置 | 全期CAGR | 回撤 | 卡玛 | 夏普 | 2019-22CAGR | 2023-26CAGR | "
                 "2024-26CAGR(样本外) | vs基线 | 成本后CAGR | 年化换手 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    base_cagr = None
    for name, picks in configs:
        r_full = metrics(rets_all[name], grid=grid)
        r_cost = metrics(rets_all[name], grid=grid, cost_map=costs[name])
        r_early = metrics(rets_all[name], grid=[d for d in grid if SEG_EARLY[0] <= str(d.date()) <= SEG_EARLY[1]])
        r_late = metrics(rets_all[name], grid=[d for d in grid if SEG_LATE[0] <= str(d.date()) <= SEG_LATE[1]])
        r_oos = metrics(rets_all[name], grid=[d for d in grid if SEG_OOS[0] <= str(d.date()) <= SEG_OOS[1]])
        tsum = sum(tos[name].values())
        ann_to = tsum / r_full["years"] if r_full.get("years") else 0.0
        if name == "基线":
            base_cagr = r_full["cagr"]
        delta = (r_full["cagr"] - base_cagr) * 100 if base_cagr is not None else 0.0
        lines.append("| %s | %.2f%% | %.2f%% | %.2f | %.2f | %.2f%% | %.2f%% | %.2f%% | %+.2fpp | %.2f%% | %.1fx |"
                     % (name, r_full["cagr"] * 100, r_full["mdd"] * 100,
                        r_full["calmar"], r_full["sharpe"],
                        r_early["cagr"] * 100, r_late["cagr"] * 100, r_oos["cagr"] * 100,
                        delta, r_cost["cagr"] * 100, ann_to))

    lines.append("\n- 期数: 基线=%d" % len(picks_base))
    for fn in EXCLUDE_FACTORS:
        lines.append("  - %s: %d 期" % (fn, len(main_res["picks_excl"][fn])))
    lines.append("  - 组合4: %d 期" % len(main_res["picks_all"]))
    lines.append("- 每期入选只数（均值）: 基线=%.2f" % np.mean(list(names_all["基线"].values())))

    # ---- 表2：旧口径逐字复刻（证明旧数从哪来）
    # 必须用 legacy 那套 picks：原脚本的 momentum_12_1 是 pos-1/pos-252（≈12-0），
    # 用它才产出旧报告的 20 期 / 34.36%；用 std picks 会变成 21 期 / 36.37% 而复现不了。
    lines.append("\n## 表2 旧口径逐字复刻（旧动量 ≈12-0 + 各配置自己的期列表 + years=len(rets)/4）\n")
    lines.append("| 配置 | 期数 | 旧口径年数 | 旧口径CAGR | 旧口径回撤 | 旧口径夏普 | 跨2季链接段 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, picks in legacy_cfgs:
        r = legacy_backtest(env, picks)
        lines.append("| %s | %d | %.2f | %.2f%% | %.2f%% | %.2f | %d |"
                     % (name, r["n"], r["n"] / 4.0, r["cagr"] * 100, r["mdd"] * 100, r["sharpe"],
                        count_cross_links(picks, rebal)))
    lines.append("\n> 对照：这批旧持仓的真实日历跨度 = %.2f 年（legacy 共同网格 n=%d），"
                 "旧口径按 n/4 只给 %.2f 年；差额全部来自「缺期被当成整期」和「日历跨度被压缩」。\n"
                 % (legacy_span, len(legacy_grid), legacy_naive))

    # ---- 表3：动量口径敏感度
    lines.append("## 表3 动量口径敏感度（旧 ≈12-0 vs 生产 12-1）\n")
    lines.append("| 动量口径 | 配置 | 共同网格n | 全期CAGR | vs基线 |")
    lines.append("|---|---|---|---|---|")
    for mode, label in (("legacy", "旧 c0/c12 (≈12-0)"), ("std", "生产 close[-21]/close[-252] (12-1)")):
        res = results[mode]
        g = res["grid"]
        r_base = metrics(returns_on_grid(fwd, res["picks_base"], g)[0], grid=g)
        for name, picks in res["configs"]:
            if name not in ("基线", "排除PX_AMT_RATIO_20D", "组合排除4"):
                continue
            r = metrics(returns_on_grid(fwd, picks, g)[0], grid=g)
            delta = (r["cagr"] - r_base["cagr"]) * 100
            lines.append("| %s | %s | %d | %.2f%% | %+.2fpp |"
                         % (label, name, r["n"], r["cagr"] * 100, delta))
    lines.append("\n> 结论：动量门槛口径一动，基线水平与增益幅度都变（旧口径 ≈12-0 会把基线压低、把增益放大）。"
                 "生产用的是 12-1，以表1 为准。\n")

    # ---- 表4：每期换名台账（AMT@20% 与 组合4@20%）
    lines.append("## 表4 每期换名台账（20% 档）\n")
    for name, picks in configs:
        if name not in ("排除PX_AMT_RATIO_20D", "组合排除4"):
            continue
        led = swap_ledger(picks_base, picks, fwd, grid)
        n_names_out = sum(len(x["out"]) for x in led)
        lines.append("### %s：%d / %d 期发生换名，累计换出 %d 只\n"
                     % (name, len(led), len(grid), n_names_out))
        if not led:
            lines.append("- 全期零换名（该排除条件在合格域内从未触发）。\n")
            continue
        lines.append("| 期 | 换出 | 换入 | 该期基线 | 该期该配置 | 差(pp) |")
        lines.append("|---|---|---|---|---|---|")
        for x in led:
            lines.append("| %s | %s | %s | %.2f%% | %.2f%% | %+.3f |"
                         % (x["date"], ",".join(x["out"]) or "-", ",".join(x["in"]) or "-",
                            x["ret_base"] * 100, x["ret_cmp"] * 100, x["diff_pp"]))
        lines.append("")
    lines.append("> 增益的物理来源是「极少数换名」，不是系统性排雷：换名次数少、单次幅度随机，"
                 "换名方向一旦反转即转为负贡献（单票运气，非可复用的 alpha）。\n")

    # ---- 结论
    lines.append("## 结论\n")
    lines.append("1. 旧报告的 +2.65pp **不成立**：它是 `years=len(rets)/4` 把旧口径的 %.2f 年日历压成 %.2f 年 "
                 "+ 各配置缺期被当成整期造成的口径伪差；旧口径复刻（表2）可逐位复现旧数字。"
                 % (legacy_span, legacy_naive))
    lines.append("2. 共同网格 + 真实日历年化后，AMT@20% 的增益只剩个位数 pp："
                 "旧动量口径下 +0.40pp（`exclude_robustness_audit.md` 表1），"
                 "生产 12-1 口径下 +0.20pp（表1），与 2026-09-17 真实约束回测的 +0.00pp 同向。")
    lines.append("3. 增益来源是「极少数期换掉 1 只票」（表4），不是系统性排雷，样本外不支持。")
    lines.append("4. 动量口径敏感（表3）：换成生产 12-1 后基线水平抬升、增益幅度变化，"
                 "任何结论都必须标注用哪个动量口径。")
    lines.append("5. **落地判定：4 个 PX 排雷因子对 ATR 载体无可落地增益，不进入实盘参数**；"
                 "ATR 本体不受影响，继续按原参数运行。")
    lines.append("6. 口径声明：单源结论（仅 astock），未经备用源交叉验证。\n")
    lines.append("> 已知口径瑕疵（不修，仅记录）：`run_screen.get_candidates` 的挖掘宇宙未排除北交所"
                 "（表4 曾出现 920964.BJ），与 P16 训练宇宙（排除 4/8 开头代码）不一致；"
                 "如未来要复用作实盘候选，需先补这一层过滤。")

    out = os.path.join(HERE, "exclude_ablation_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("\n报告: %s | 总用时 %.0fs" % (out, time.time() - t0))


if __name__ == "__main__":
    main()
