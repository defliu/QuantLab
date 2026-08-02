# coding=utf-8
"""审计17：V2 因子权重微调验证（z/hp 配比优化）
基于 audit14_修正口径.py 模板，参数化 score_v2 的 z/hp 权重，
测试五种配比：(0.5,0.5)/(0.7,0.3)/(0.8,0.2)/(0.6,0.4)/(1.0,0.0)
输出: audit17_结果.txt"""
import sys, time, os
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
import pandas as pd
import numpy as np
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

os.makedirs(r"E:\QuantLab\research_audit", exist_ok=True)
OUT = r"E:\QuantLab\research_audit\audit17_结果.txt"
_log = []
def log(*args, **kwargs):
    s = " ".join(str(a) for a in args)
    print(s, **kwargs)
    _log.append(s)

t0 = time.time()

# ===== 数据加载（与 audit14 一致） =====
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
log("panel: %s" % str(panel.shape))

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

def limit_pct(code):
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10

# ===== 参数化评分（核心改动） =====
def score_v2_weighted(d, cand, dd, w_z=0.5, w_hp=0.5):
    """参数化 z/hp 权重的 score_v2：score = z*w_z + hp*w_hp"""
    bp = 1.0 / dd.loc[cand]["pb"].replace(0, np.nan)
    inds = pd.Series(cand, index=cand).map(ind_map)
    t = pd.DataFrame({"bp": bp, "ind": inds}).dropna()
    z = t.groupby("ind")["bp"].transform(lambda x: (x - x.mean()) / (x.std() + 1e-9))
    hp = bp_hist_pct(d)
    if hp is None:
        return z
    r = z * w_z + hp.reindex(cand) * w_hp
    return r

# ===== 状态机 run_v2（与 audit14 一致，接受评分函数） =====
def run_v2(score_fn, tx_cost=0.001, limit_handling=True, force_full_turn=False):
    """状态机（真实口径）：期 i 结算上一期末持仓 [E_{i-1}->E_i]，结算日=E_i"""
    rows = []
    prev_holdings = {}
    prev_cost = 0.0
    prev_turnover, prev_sells, prev_buys = 0.0, 0, 0
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
                             "sells": prev_sells, "buys": prev_buys})
        dd = panel.loc[d]
        cand = get_candidates(d, dd)
        if len(cand) < 10:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue
        score = score_fn(d, cand, dd).dropna()
        if len(score) == 0:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue
        target = set(score.sort_values(ascending=False).head(80).index)
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
    return pd.DataFrame(rows)

# ===== 基准（同口径等权，与 audit14 一致） =====
log("============ 0. 基准（同口径等权） ============")
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

def nav_ret(per_ret_series, start, end):
    """区间累计收益：用 [start,end) 内的逐期收益连乘"""
    seg = per_ret_series[(per_ret_series.index >= start) & (per_ret_series.index < end)]
    if len(seg) == 0:
        return None, 0
    return (1 + seg).prod() - 1, len(seg)

# ===== 五种配比 =====
WEIGHTS = [
    (0.5, 0.5),
    (0.7, 0.3),
    (0.8, 0.2),
    (0.6, 0.4),
    (1.0, 0.0),
]

results = {}
for w_z, w_hp in WEIGHTS:
    label = "z=%.1f/hp=%.1f" % (w_z, w_hp)
    log("--- 运行 %s ---" % label)
    score_fn = lambda d, cand, dd, _wz=w_z, _whp=w_hp: score_v2_weighted(d, cand, dd, _wz, _whp)
    df = run_v2(score_fn, tx_cost=0.001, limit_handling=True).set_index("date")
    results[label] = df
    cum = (1 + df["period_return"]).cumprod() - 1
    years = (df.index[-1] - df.index[0]).days / 365.25
    ann = (1 + cum.iloc[-1]) ** (1 / years) - 1
    log("  累计=%7.1f%% 年化=%6.1f%% 期数=%d 平均换手=%.2f" % (
        cum.iloc[-1] * 100, ann * 100, len(df), df["turnover"].mean()))

# ===== 1. 五种配比全期/2024+/2026 超额对照表 =====
log("\n============ 1. 五种配比全期/2024+/2026 超额对照表 ============")
windows = [
    ("全期", "2018-01-01", "2027-01-01"),
    ("2024+", "2024-01-01", "2027-01-01"),
    ("2026", "2026-01-01", "2027-01-01"),
]
header = "%-14s  %12s  %12s  %12s" % ("配比", "全期超额", "2024+超额", "2026超额")
log(header)
log("-" * len(header))
for w_z, w_hp in WEIGHTS:
    label = "z=%.1f/hp=%.1f" % (w_z, w_hp)
    df = results[label]
    excesses = []
    for wname, s, e in windows:
        sr, sn = nav_ret(df["period_return"], s, e)
        br, bn = nav_ret(base["ret"], s, e)
        if sr is not None and br is not None:
            excesses.append((sr - br) * 100)
        else:
            excesses.append(None)
    log("%-14s  %+11.1f%%  %+11.1f%%  %+11.1f%%" % (
        label,
        excesses[0] if excesses[0] is not None else 0,
        excesses[1] if excesses[1] is not None else 0,
        excesses[2] if excesses[2] is not None else 0))

# ===== 2. 2026 逐期明细（每种配比） =====
log("\n============ 2. 2026 逐期明细 ============")
for w_z, w_hp in WEIGHTS:
    label = "z=%.1f/hp=%.1f" % (w_z, w_hp)
    df = results[label]
    m26 = df[df.index >= "2026-01-01"]
    if len(m26) == 0:
        log("--- %s: 无2026数据 ---" % label)
        continue
    log("--- %s ---" % label)
    for d, r in m26.iterrows():
        br2, _ = nav_ret(base["ret"], d.strftime("%Y-%m-%d"), (d + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        exc = (r["period_return"] - base["ret"].get(d, 0)) * 100 if d in base.index else None
        log("  %s  收益=%+6.1f%%  n=%3d  卖出=%3d 买入=%3d" % (
            d.strftime("%Y-%m-%d"), r["period_return"] * 100, r["n"], r["sells"], r["buys"]))

# ===== 3. 结论段 =====
log("\n============ 3. 结论 ============")
best_label = None
best_2026_exc = None
best_full_exc = None
for w_z, w_hp in WEIGHTS:
    label = "z=%.1f/hp=%.1f" % (w_z, w_hp)
    df = results[label]
    sr_full, _ = nav_ret(df["period_return"], "2018-01-01", "2027-01-01")
    br_full, _ = nav_ret(base["ret"], "2018-01-01", "2027-01-01")
    sr_2026, _ = nav_ret(df["period_return"], "2026-01-01", "2027-01-01")
    br_2026, _ = nav_ret(base["ret"], "2026-01-01", "2027-01-01")
    if sr_2026 is None or br_2026 is None:
        continue
    exc_2026 = (sr_2026 - br_2026) * 100
    exc_full = (sr_full - br_full) * 100 if sr_full is not None and br_full is not None else None
    if exc_2026 > 0:
        if best_2026_exc is None or exc_2026 > best_2026_exc:
            best_2026_exc = exc_2026
            best_label = label
            best_full_exc = exc_full
        elif exc_2026 == best_2026_exc and exc_full is not None and best_full_exc is not None and exc_full > best_full_exc:
            best_2026_exc = exc_2026
            best_label = label
            best_full_exc = exc_full

if best_label is not None:
    log("最优配比 = %s（2026 超额 = %+.1f%%，全期超额 = %+.1f%%）" % (best_label, best_2026_exc, best_full_exc))
else:
    log("所有配比 2026 超额均为负，无配比能使 2026 转正")

log("\n总用时 %.0fs" % (time.time() - t0))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(_log))
