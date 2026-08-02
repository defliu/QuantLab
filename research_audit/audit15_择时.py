# coding=utf-8
"""审计15：V3 = V2 + 风格择时验证脚本
在已验证的 V2-B 价值小盘策略之上，加入风格择时开关（空仓/满仓），验证能否修复 2026 年转负
择时信号：S1 BP滚动IC / S2 候选池动量 / S3 拥挤度
输出：E:\QuantLab\research_audit\audit15_结果.txt"""
import sys, time, os
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

os.makedirs(r"E:\QuantLab\research_audit", exist_ok=True)
OUT = r"E:\QuantLab\research_audit\audit15_结果.txt"
_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    _log.append(s)

t0 = time.time()

# ========== 复用 audit14 框架（数据加载/panel/财务PIT/缓存/score_v2） ==========
daily = pd.read_parquet(DAILY_PATH)
idx = daily.index
start_ts = pd.Timestamp(START_DATE).date()
codes_all = set(idx.get_level_values("ts_code")[idx.get_level_values("trade_date") >= start_ts].unique())
daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
idx = daily.index
daily = daily.loc[(idx.get_level_values("trade_date") >= start_ts) &
                  (idx.get_level_values("trade_date") <= pd.Timestamp(END_DATE).date())].copy()
idx = daily.index
prev_close = daily["close"].groupby(level=1).shift(1)
panel = pd.DataFrame({
    "close": daily["close"].values, "open": daily["open"].values,
    "high": daily["high"].values, "low": daily["low"].values,
    "pe_ttm": daily["pe_ttm"].values, "pb": daily["pb"].values,
    "circ_mv": daily["circ_mv"].values, "amount": daily["amount"].values,
    "prev_close": prev_close.values,
}, index=idx)
is_st = daily["is_st"].astype(bool)
suspend = daily["suspend_type"].fillna("N")
panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]
log("panel:", panel.shape)

basic = pd.read_parquet(r"E:/astock/basic/stock_basic.parquet")
ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))

fin = pd.read_parquet(r"E:/astock/finance/fina_indicator.parquet")
fin = fin[["ts_code", "end_date", "ann_date", "bps", "roe", "profit_dedt", "debt_to_assets"]].copy()
fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
fin = fin.dropna(subset=["ann_date"])
fin = fin[fin["ts_code"].isin(codes_all)]
fin = fin.sort_values(["ts_code", "ann_date"])
fin_by_code = {c: g for c, g in fin.groupby("ts_code")}

_fin_cache = {}
def fin_snapshot(date):
    d = pd.Timestamp(date)
    if d in _fin_cache:
        return _fin_cache[d]
    rows = {}
    for c, g in fin_by_code.items():
        g = g[g["ann_date"] <= d]
        if len(g) == 0:
            continue
        last = g.iloc[-1]
        rows[c] = (last["bps"], last["roe"], last["profit_dedt"], last["debt_to_assets"])
    _fin_cache[d] = rows
    return rows

trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
rebal = get_rebalance_dates(panel, freq="2M")

pb_wide = panel["pb"].unstack("ts_code")
pb_wide.index = pd.DatetimeIndex(pb_wide.index)
bp_wide = 1.0 / pb_wide.replace(0, np.nan)
bp_monthly = bp_wide.resample("ME").last()
month_dates = list(bp_monthly.index)

_bph_cache = {}
def bp_hist_pct(date):
    d = pd.Timestamp(date)
    if d in _bph_cache:
        return _bph_cache[d]
    w = [m for m in month_dates if m <= d][-36:]
    if len(w) < 12:
        _bph_cache[d] = None
        return None
    sub = bp_monthly.loc[w]
    r = (sub <= sub.iloc[-1]).mean(axis=0)
    _bph_cache[d] = r
    return r

_cand_cache = {}
def get_candidates(d, dd):
    if d in _cand_cache:
        return _cand_cache[d]
    m = (dd["circ_mv"] > 0) & (dd["circ_mv"] < 300000) & (dd["pe_ttm"] > 0) & (dd["pb"] > 0)
    fs = fin_snapshot(d)
    mq = m.copy()
    for c in mq.index:
        r = fs.get(c)
        if r is None or not (r[0] > 0 and r[2] > 0 and r[1] > 0):
            mq[c] = False
    r = mq[mq].index
    _cand_cache[d] = r
    return r

_score_cache = {}
def score_v2(d, cand, dd):
    if d in _score_cache:
        return _score_cache[d]
    bp = 1.0 / dd.loc[cand]["pb"].replace(0, np.nan)
    inds = pd.Series(cand, index=cand).map(ind_map)
    t = pd.DataFrame({"bp": bp, "ind": inds}).dropna()
    z = t.groupby("ind")["bp"].transform(lambda x: (x - x.mean()) / (x.std() + 1e-9))
    hp = bp_hist_pct(d)
    r = z if hp is None else z * 0.5 + (hp.reindex(cand) * 0.5)
    _score_cache[d] = r
    return r

def limit_pct(code):
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10

# ========== 新增：择时信号计算（PIT 安全，只用 d 及以前数据） ==========

# 预计算所有期的收益和 BP 分位数（用于 S1 滚动 IC）
_period_returns = []  # [(d, e_date, x_date, {code: ret}), score_bp_percentile]
for i in range(len(rebal) - 1):
    d = rebal[i]
    nxt = rebal[i + 1]
    e_idx = ti[pd.Timestamp(d)] + 1
    x_idx = ti[pd.Timestamp(nxt)] + 1
    if e_idx >= len(trade_dates) or x_idx >= len(trade_dates):
        break
    dd = panel.loc[d]
    e_open = panel.loc[trade_dates[e_idx], "open"]
    x_open = panel.loc[trade_dates[x_idx], "open"]
    cand = get_candidates(d, dd)
    if len(cand) < 10:
        continue
    score = score_v2(d, cand, dd).dropna()
    if len(score) == 0:
        continue
    top = score.sort_values(ascending=False).head(80).index
    code_rets = {}
    for c in top:
        eo, xo = e_open.get(c), x_open.get(c)
        if eo is not None and xo is not None and eo > 0 and xo > 0:
            code_rets[c] = xo / eo - 1.0
    bp_vals = 1.0 / dd.loc[cand]["pb"].replace(0, np.nan)
    bp_pct = bp_vals.rank(pct=True)
    _period_returns.append({
        "d": d,
        "e_date": trade_dates[e_idx],
        "x_date": trade_dates[x_idx],
        "code_rets": code_rets,
        "bp_pct": bp_pct,
        "cand": cand,
    })

def signal_s1_bp_rolling_ic(d):
    """S1: BP 因子滚动 IC（价值因子有效性）
    取历史已完结的调仓期（d 之前的期），计算每期收益与 BP 分位数的秩相关
    用最近 8~12 个历史期算 IC，若最近一期滚动 IC 均值 < 0 → 空仓（signal=0）
    PIT 保证：只用 d 之前的已完结期，收益已实现"""
    hist = [p for p in _period_returns if p["x_date"] < pd.Timestamp(d).date()]
    if len(hist) < 8:
        return 1
    ic_vals = []
    for p in hist[-12:]:
        code_rets = p["code_rets"]
        bp_pct = p["bp_pct"]
        common = list(set(code_rets.keys()) & set(bp_pct.index))
        if len(common) < 10:
            continue
        rets = [code_rets[c] for c in common]
        bp_ranks = [bp_pct[c] for c in common]
        ic, _ = spearmanr(rets, bp_ranks)
        if not np.isnan(ic):
            ic_vals.append(ic)
    if len(ic_vals) == 0:
        return 1
    mean_ic = np.mean(ic_vals)
    return 0 if mean_ic < 0 else 1

def signal_s2_candidate_momentum(d):
    """S2: 候选池动量（趋势过滤）
    候选池内股票等权，取 d 日及之前 60 个交易日的收益率
    若 60 日收益 < 0 → 空仓
    PIT 保证：只用 d 日及以前的 close 数据"""
    d_ts = pd.Timestamp(d)
    d_idx = ti.get(d_ts)
    if d_idx is None or d_idx < 60:
        return 1
    start_idx = d_idx - 60
    start_date = trade_dates[start_idx]
    dd = panel.loc[d]
    cand = get_candidates(d, dd)
    if len(cand) < 10:
        return 1
    close_start = panel.loc[start_date, "close"]
    close_end = panel.loc[d, "close"]
    rets = []
    for c in cand:
        cs = close_start.get(c)
        ce = close_end.get(c)
        if cs is not None and ce is not None and cs > 0 and ce > 0:
            rets.append(ce / cs - 1.0)
    if len(rets) == 0:
        return 1
    mean_ret = np.mean(rets)
    return 0 if mean_ret < 0 else 1

def signal_s3_crowdedness(d):
    """S3: 候选池拥挤度（热度分位）
    候选池平均日成交额 amount，与过去 36 个月自身历史分布比较
    若当前分位 > 0.9（过热）→ 空仓
    PIT 保证：只用 d 日及以前的 amount 数据"""
    d_ts = pd.Timestamp(d)
    d_idx = ti.get(d_ts)
    if d_idx is None:
        return 1
    dd = panel.loc[d]
    cand = get_candidates(d, dd)
    if len(cand) < 10:
        return 1
    amount_now = dd["amount"].loc[cand].mean()
    amount_wide = panel["amount"].unstack("ts_code")
    amount_wide.index = pd.DatetimeIndex(amount_wide.index)
    amount_monthly = amount_wide.resample("ME").mean()
    month_dates_local = list(amount_monthly.index)
    w = [m for m in month_dates_local if m <= d_ts][-36:]
    if len(w) < 12:
        return 1
    cand_amount_hist = amount_monthly.loc[w, cand].mean(axis=1)
    pct = (cand_amount_hist <= amount_now).mean()
    return 0 if pct > 0.9 else 1

# ========== 状态机 run_v2 改造：支持择时信号 ==========
def run_v2_with_timing(signal_func=None, tx_cost=0.001, limit_handling=True, force_full_turn=False):
    """状态机（真实口径）+ 择时信号
    signal_func: 择时信号函数，返回 0（空仓）或 1（满仓）
    空仓期持仓 = {}，period_return 记 0（现金，无利息）
    清仓/建仓都计入成本 0.001 单边"""
    rows = []
    prev_holdings = {}
    prev_cost = 0.0
    prev_turnover, prev_sells, prev_buys = 0.0, 0, 0
    prev_signal = 1
    for i in range(len(rebal) - 1):
        d = rebal[i]
        e_idx = ti[pd.Timestamp(d)] + 1
        if e_idx >= len(trade_dates):
            break
        e_date = trade_dates[e_idx]
        e_row = panel.loc[e_date]
        if prev_holdings:
            rets = []
            for code, base in prev_holdings.items():
                xo = e_row["open"].get(code)
                if xo is not None and xo > 0 and base is not None and base > 0:
                    rets.append(xo / base - 1.0)
            if len(rets) > 0:
                rows.append({"date": pd.Timestamp(e_date),
                             "period_return": np.mean(rets) - prev_cost,
                             "n": len(prev_holdings), "turnover": prev_turnover,
                             "sells": prev_sells, "buys": prev_buys,
                             "signal": prev_signal})
        elif prev_signal == 0:
             rows.append({"date": pd.Timestamp(e_date),
                          "period_return": 0.0 - prev_cost,
                         "n": 0, "turnover": prev_turnover,
                         "sells": prev_sells, "buys": prev_buys,
                         "signal": 0})
        dd = panel.loc[d]
        cand = get_candidates(d, dd)
        if len(cand) < 10:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            prev_signal = 1
            continue
        score = score_v2(d, cand, dd).dropna()
        if len(score) == 0:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            prev_signal = 1
            continue
        target = set(score.sort_values(ascending=False).head(80).index)
        signal = signal_func(d) if signal_func is not None else 1
        if signal == 0:
            sells = len(prev_holdings)
            prev_holdings = {}
            prev_turnover = sells / 80.0
            prev_cost = prev_turnover * tx_cost
            prev_sells, prev_buys = sells, 0
            prev_signal = 0
            continue
        sells, buys = 0, 0
        new_holdings = {}
        for code, base in prev_holdings.items():
            if code in target and not force_full_turn:
                new_holdings[code] = e_row["open"].get(code, base)
                continue
            xo, xpc = e_row["open"].get(code), e_row["prev_close"].get(code)
            xl = e_row["low"].get(code)
            stuck = (limit_handling and xo is not None and xpc is not None and xpc > 0
                     and xl is not None and xo <= xpc * (1 - limit_pct(code) + 0.005)
                     and xo <= xl * 1.001)
            if stuck and not force_full_turn:
                new_holdings[code] = xo if xo is not None else base
            else:
                sells += 1
        for code in target:
            if code in new_holdings:
                continue
            eo, epc = e_row["open"].get(code), e_row["prev_close"].get(code)
            eh = e_row["high"].get(code)
            buyable = (not limit_handling or eo is None or epc is None or epc <= 0 or eh is None
                       or not (eo >= epc * (1 + limit_pct(code) - 0.005) and eo >= eh * 0.999))
            if buyable and eo is not None and eo > 0:
                new_holdings[code] = eo
                buys += 1
        prev_holdings = new_holdings
        prev_turnover = (sells + buys) / 80.0
        prev_cost = prev_turnover * tx_cost
        prev_sells, prev_buys = sells, buys
        prev_signal = signal
    return pd.DataFrame(rows)

# ========== 基准（同口径等权） ==========
log("============ 1. 基准（同口径等权） ============")
base_rows = []
for i in range(len(rebal) - 1):
    d = rebal[i]
    nxt = rebal[i + 1]
    e_idx = ti[pd.Timestamp(d)] + 1
    x_idx = ti[pd.Timestamp(nxt)] + 1
    if x_idx >= len(trade_dates):
        break
    dd = panel.loc[d]
    e_open = panel.loc[trade_dates[e_idx], "open"]
    x_open = panel.loc[trade_dates[x_idx], "open"]
    cand = get_candidates(d, dd)
    valid = [c for c in cand if e_open.get(c) is not None and x_open.get(c) is not None
             and e_open.get(c) > 0 and x_open.get(c) > 0]
    if len(valid) >= 30:
        base_rows.append({"date": pd.Timestamp(trade_dates[x_idx]),
                          "ret": np.mean([x_open[c] / e_open[c] - 1.0 for c in valid])})
base = pd.DataFrame(base_rows).set_index("date")
b_cum = (1 + base["ret"]).cumprod() - 1
b_years = (base.index[-1] - base.index[0]).days / 365.25
log("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100, ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100))

# ========== 运行择时变体 ==========
def combined_signal(d):
    """组合信号：任一触发即空仓"""
    s1 = signal_s1_bp_rolling_ic(d)
    s2 = signal_s2_candidate_momentum(d)
    s3 = signal_s3_crowdedness(d)
    return 0 if (s1 == 0 or s2 == 0 or s3 == 0) else 1

variants = {
    "无择时(V2)": run_v2_with_timing(signal_func=None).set_index("date"),
    "S1:BP滚动IC": run_v2_with_timing(signal_func=signal_s1_bp_rolling_ic).set_index("date"),
    "S2:候选池动量": run_v2_with_timing(signal_func=signal_s2_candidate_momentum).set_index("date"),
    "S3:拥挤度": run_v2_with_timing(signal_func=signal_s3_crowdedness).set_index("date"),
    "组合(S1或S2或S3)": run_v2_with_timing(signal_func=combined_signal).set_index("date"),
}

# ========== 净值法全期对照 ==========
log("\n============ 2. 净值法全期对照 ============")
for name, df in variants.items():
    cum = (1 + df["period_return"]).cumprod() - 1
    years = (df.index[-1] - df.index[0]).days / 365.25
    ann = (1 + cum.iloc[-1]) ** (1 / years) - 1
    empty_periods = len(df[df["signal"] == 0]) if "signal" in df.columns else 0
    log("%-22s 累计=%7.1f%% 年化=%6.1f%% 期数=%d 平均换手=%.2f 空仓期=%d" % (
        name, cum.iloc[-1] * 100, ann * 100, len(df), df["turnover"].mean(), empty_periods))

# ========== 净值法窗口超额 ==========
def nav_ret(per_ret_series, start, end):
    seg = per_ret_series[(per_ret_series.index >= start) & (per_ret_series.index < end)]
    if len(seg) == 0:
        return None, 0
    return (1 + seg).prod() - 1, len(seg)

log("\n============ 3. 净值法窗口超额（策略 vs 基准同窗口连乘） ============")
windows = [("全期2018-2026", "2018-01-01", "2027-01-01"),
           ("2026至今", "2026-01-01", "2027-01-01"),
           ("2024+", "2024-01-01", "2027-01-01"),
           ("牛市2019-2021", "2019-01-01", "2022-01-01"),
           ("熊市2022-2023", "2022-01-01", "2024-01-01"),
           ("震荡2024-2025", "2024-01-01", "2026-01-01"),
           ("压力:2024Q1", "2024-01-01", "2024-04-01"),
           ("压力:2026Q2", "2026-04-01", "2026-07-01")]
for label, s, e in windows:
    log("--- %s ---" % label)
    br, bn = nav_ret(base["ret"], s, e)
    for name, df in variants.items():
        sr, sn = nav_ret(df["period_return"], s, e)
        if sr is None or br is None:
            continue
        log("  %-22s 策略=%+7.1f%%  基准=%+7.1f%%  超额=%+7.1f%%  (n=%d/%d)" % (
            name, sr * 100, br * 100, (sr - br) * 100, sn, bn))

# ========== 2026 逐期收益明细 ==========
log("\n============ 4. 2026 逐期收益明细（净值法对齐） ============")
for name, df in variants.items():
    m26 = df[(df.index >= "2026-01-01")]
    if len(m26) == 0:
        continue
    log("--- %s ---" % name)
    for d, r in m26.iterrows():
        sig = r.get("signal", 1)
        log("  %s  收益=%+6.1f%%  n=%3d  危出=%3d 买入=%3d  信号=%d" % (
            d.strftime("%Y-%m-%d"), r["period_return"] * 100, r["n"], r["sells"], r["buys"], sig))

# ========== 结论段 ==========
log("\n============ 5. 结论 ============")
log("哪种择时能把 2026 超额拉回正数、全期超额损失多少：")
br_2026, _ = nav_ret(base["ret"], "2026-01-01", "2027-01-01")
br_full, _ = nav_ret(base["ret"], "2018-01-01", "2027-01-01")
for name, df in variants.items():
    sr_2026, _ = nav_ret(df["period_return"], "2026-01-01", "2027-01-01")
    sr_full, _ = nav_ret(df["period_return"], "2018-01-01", "2027-01-01")
    if sr_2026 is None or sr_full is None:
        continue
    ex_2026 = sr_2026 - br_2026 if br_2026 is not None else None
    ex_full = sr_full - br_full if br_full is not None else None
    ex_full_v2_nav = (1 + variants["无择时(V2)"]["period_return"]).prod() - 1
    ex_full_v2_ex = ex_full_v2_nav - br_full if br_full is not None else None
    sr_full_nav = (1 + df["period_return"]).prod() - 1
    ex_full = sr_full_nav - br_full if br_full is not None else None
    delta_full = (ex_full - ex_full_v2_ex) if ex_full is not None and ex_full_v2_ex is not None else None
    log("  %-22s 2026超额=%+6.1f%%  全期超额=%+7.1f%%  相对V2损失=%+6.1f%%" % (
        name, ex_2026 * 100 if ex_2026 is not None else 0,
        ex_full * 100 if ex_full is not None else 0,
        delta_full * 100 if delta_full is not None else 0))

log("\n总用时 %.0fs" % (time.time() - t0))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(_log))
