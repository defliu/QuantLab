# coding=utf-8
"""审计13：2026 两口径矛盾根因 —— 逐期独立(-2.6%) vs 状态机(+14.7%)
对照变体：持仓延续 / 跌停顺延 / 强制全换，聚焦 2026 窗口期收益明细
输出：E:\QuantLab\research_audit\audit13_结果.txt"""
import sys, time, os
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
import pandas as pd
import numpy as np
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

os.makedirs(r"E:\QuantLab\research_audit", exist_ok=True)
OUT = r"E:\QuantLab\research_audit\audit13_结果.txt"
_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    _log.append(s)

t0 = time.time()
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

def run_v2(tx_cost=0.001, limit_handling=True, force_full_turn=False):
    """状态机：期 i 结算上一期末持仓 [E_{i-1}->E_i] 区间收益；交易与结算两阶段。
    force_full_turn=True: 强制每期全换（不留持仓延续），隔离"持仓延续"因素"""
    rows = []
    prev_holdings = {}
    prev_cost = 0.0
    for i in range(len(rebal) - 1):
        d = rebal[i]
        nxt = rebal[i + 1]
        e_idx = ti[pd.Timestamp(d)] + 1
        x_idx = ti[pd.Timestamp(nxt)] + 1
        if x_idx >= len(trade_dates):
            break
        e_date, x_date = trade_dates[e_idx], trade_dates[x_idx]
        e_row = panel.loc[e_date]
        if prev_holdings:
            rets = []
            for code, base in prev_holdings.items():
                xo = e_row["open"].get(code)
                if xo is not None and xo > 0 and base is not None and base > 0:
                    rets.append(xo / base - 1.0)
            if len(rets) > 0:
                rows.append({"date": x_date, "period_return": np.mean(rets) - prev_cost,
                             "n": len(prev_holdings), "turnover": prev_turnover,
                             "sells": prev_sells, "buys": prev_buys})
        dd = panel.loc[d]
        cand = get_candidates(d, dd)
        if len(cand) < 10:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue
        score = score_v2(d, cand, dd).dropna()
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

def run_independent(tx_cost=0.001):
    """逐期独立（audit11 口径）：每期 E 日 open 全换买入 top80，x 日 open 卖出，无持仓延续/无涨跌停处理"""
    rows = []
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
        if len(cand) < 10:
            continue
        score = score_v2(d, cand, dd).dropna()
        if len(score) == 0:
            continue
        top = score.sort_values(ascending=False).head(80).index
        rets, n = [], 0
        for c in top:
            eo, xo = e_open.get(c), x_open.get(c)
            if eo is not None and xo is not None and eo > 0 and xo > 0:
                rets.append(xo / eo - 1.0)
                n += 1
        if len(rets) == 0:
            continue
        rows.append({"date": trade_dates[x_idx], "period_return": np.mean(rets) - 2.0 * tx_cost,
                     "n": n, "turnover": 2.0, "sells": n, "buys": n})
    return pd.DataFrame(rows)

def fmt(m):
    return "累计=%7.1f%% 年化=%6.1f%% 回撤=%6.1f%% 夏普=%4.2f 期数=%d 平均换手=%.2f 平均n=%.1f" % (
        m["累计"] * 100, m["年化"] * 100, m["回撤"] * 100, m["夏普"], m["期数"], m["平均换手"], m["平均n"])

def metrics(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    cum = (1 + df["period_return"]).cumprod() - 1
    years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    ann = (1 + cum.iloc[-1]) ** (1 / years) - 1
    dd = (cum + 1).div((cum + 1).cummax()) - 1
    sharpe = df["period_return"].mean() / df["period_return"].std() * np.sqrt(6) if df["period_return"].std() > 0 else 0
    return {"累计": cum.iloc[-1], "年化": ann, "回撤": dd.min(), "夏普": sharpe,
            "期数": len(df), "平均换手": df["turnover"].mean(), "平均n": df["n"].mean()}

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
        base_rows.append({"date": trade_dates[x_idx], "ret": np.mean([x_open[c] / e_open[c] - 1.0 for c in valid])})
base = pd.DataFrame(base_rows)
base["date"] = pd.to_datetime(base["date"])
b_cum = (1 + base["ret"]).cumprod() - 1
b_years = (base["date"].iloc[-1] - base["date"].iloc[0]).days / 365.25
log("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100, ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100))

variants = {
    "逐期独立(全换/无顺延)": run_independent(),
    "状态机(延续+顺延)": run_v2(tx_cost=0.001, limit_handling=True),
    "状态机(延续/无顺延)": run_v2(tx_cost=0.001, limit_handling=False),
    "状态机(全换+顺延)": run_v2(tx_cost=0.001, limit_handling=True, force_full_turn=True),
}
for name, df in variants.items():
    df["date"] = pd.to_datetime(df["date"])

log("============ 2. 四变体全期对照 ============")
for name, df in variants.items():
    m = metrics(df)
    log("%-22s %s" % (name, fmt(m)))

log("============ 3. 四变体 2026 窗口对照 ============")
for name, df in variants.items():
    m26 = df[(df["date"] >= "2026-01-01")]
    if len(m26) == 0:
        log("%-22s 2026无数据" % name)
        continue
    cum = (1 + m26["period_return"]).cumprod() - 1
    b26 = base[(base["date"] >= "2026-01-01")]
    bcum = (1 + b26["ret"]).cumprod() - 1
    log("%-22s 策略=%+7.1f%% 基准=%+7.1f%% 超额=%+7.1f%% (n=%d)" % (
        name, cum.iloc[-1] * 100, bcum.iloc[-1] * 100, (cum.iloc[-1] - bcum.iloc[-1]) * 100, len(m26)))

log("============ 4. 2026 逐期收益明细 ============")
for name, df in variants.items():
    m26 = df[(df["date"] >= "2026-01-01")]
    if len(m26) == 0:
        continue
    log("--- %s ---" % name)
    for _, r in m26.iterrows():
        log("  %s  收益=%+6.1f%%  n=%3d  卖出=%3d 买入=%3d" % (
            pd.Timestamp(r["date"]).strftime("%Y-%m-%d"), r["period_return"] * 100, r["n"], r["sells"], r["buys"]))

log("\n总用时 %.0fs" % (time.time() - t0))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(_log))
