# coding=utf-8
"""审计12：V2-B 三项硬验证 —— 换手率/成本敏感性、涨跌停状态机模拟、鲁棒性(牛熊/压力/容量)
输出：E:\QuantLab\research_audit\audit12_结果.txt"""
import sys, time, os
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
import pandas as pd
import numpy as np
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

os.makedirs(r"E:\QuantLab\research_audit", exist_ok=True)
OUT = r"E:\QuantLab\research_audit\audit12_结果.txt"
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

def run_v2(tx_cost=0.001, limit_handling=True):
    rows = []
    prev_holdings = {}  # code -> 基价(买入价或上期E日open)
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
        # 结算上一期末持仓的区间收益 [E_{i-1} -> E_i]
        if prev_holdings:
            rets = []
            for code, base in prev_holdings.items():
                xo = e_row["open"].get(code)
                if xo is not None and xo > 0 and base is not None and base > 0:
                    rets.append(xo / base - 1.0)
            if len(rets) > 0:
                rows.append({"date": x_date, "period_return": np.mean(rets) - prev_cost,
                             "n": len(prev_holdings), "turnover": prev_turnover})
        # 调仓日 d 收盘评分 -> 目标
        dd = panel.loc[d]
        cand = get_candidates(d, dd)
        if len(cand) < 10:
            prev_holdings, prev_cost, prev_turnover = {}, 0.0, 0.0
            continue
        score = score_v2(d, cand, dd).dropna()
        if len(score) == 0:
            prev_holdings, prev_cost, prev_turnover = {}, 0.0, 0.0
            continue
        target = set(score.sort_values(ascending=False).head(80).index)
        # 先卖后买（E日 open 成交）
        sells, buys = 0, 0
        new_holdings = {}
        for code, base in prev_holdings.items():
            if code in target:
                new_holdings[code] = e_row["open"].get(code, base)
                continue
            xo, xpc = e_row["open"].get(code), e_row["prev_close"].get(code)
            xl = e_row["low"].get(code)
            stuck = (limit_handling and xo is not None and xpc is not None and xpc > 0
                     and xl is not None and xo <= xpc * (1 - limit_pct(code) + 0.005)
                     and xo <= xl * 1.001)
            if stuck:
                new_holdings[code] = xo if xo is not None else base  # 跌停卖不出，顺延盯市
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
    return pd.DataFrame(rows)

def metrics(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    cum = (1 + df["period_return"]).cumprod() - 1
    years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    ann = (1 + cum.iloc[-1]) ** (1 / years) - 1
    dd = (cum + 1).div((cum + 1).cummax()) - 1
    sharpe = df["period_return"].mean() / df["period_return"].std() * np.sqrt(6) if df["period_return"].std() > 0 else 0
    return {"累计": cum.iloc[-1], "年化": ann, "回撤": dd.min(), "夏普": sharpe,
            "期数": len(df), "平均换手": df["turnover"].mean()}

def fmt(m):
    return "累计=%6.1f%% 年化=%6.1f%% 回撤=%6.1f%% 夏普=%4.2f 期数=%d 平均换手=%.2f" % (
        m["累计"] * 100, m["年化"] * 100, m["回撤"] * 100, m["夏普"], m["期数"], m["平均换手"])

log("============ 1. 同口径等权基准 ============")
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

log("============ 2. 成本敏感性（涨跌停处理开） ============")
res = {}
for tc in [0.0005, 0.001, 0.002, 0.003, 0.005]:
    df = run_v2(tx_cost=tc, limit_handling=True)
    df["date"] = pd.to_datetime(df["date"])
    m = metrics(df)
    merged = df.set_index("date")["period_return"].to_frame().join(base.set_index("date")["ret"])
    ex = (1 + merged["period_return"]).cumprod() / (1 + merged["ret"]).cumprod() - 1
    years = (merged.index[-1] - merged.index[0]).days / 365.25
    ann_ex = ((1 + ex.iloc[-1]) ** (1 / years) - 1) * 100
    log("单边成本%.4f: %s  年化超额=%5.1f%%" % (tc, fmt(m), ann_ex))
    res[tc] = (df, ann_ex)

log("============ 3. 涨跌停处理对比（单边成本0.001） ============")
df_on = run_v2(tx_cost=0.001, limit_handling=True)
df_off = run_v2(tx_cost=0.001, limit_handling=False)
df_on["date"] = pd.to_datetime(df_on["date"])
df_off["date"] = pd.to_datetime(df_off["date"])
log("处理涨跌停: %s" % fmt(metrics(df_on)))
log("忽略涨跌停: %s" % fmt(metrics(df_off)))

log("============ 4. 牛熊/压力窗口（单边成本0.001，含涨跌停） ============")
merged = df_on.set_index("date")["period_return"].to_frame().join(base.set_index("date")["ret"]).dropna()
for label, s, e in [("牛市2019-2021", "2019-01-01", "2022-01-01"),
                    ("熊市2022-2023", "2022-01-01", "2024-01-01"),
                    ("震荡2024-2025", "2024-01-01", "2026-01-01"),
                    ("2026至今", "2026-01-01", "2027-01-01"),
                    ("压力:2024Q1微盘崩盘", "2024-01-01", "2024-04-01"),
                    ("压力:2026Q2微盘暴跌", "2026-04-01", "2026-07-01")]:
    mm = merged[(merged.index >= s) & (merged.index < e)]
    if len(mm) < 2:
        continue
    sy = (1 + mm["period_return"]).prod() - 1
    by = (1 + mm["ret"]).prod() - 1
    log("%-22s 策略=%+7.1f%% 基准=%+7.1f%% 超额=%+7.1f%% (n=%d)" % (label, sy * 100, by * 100, (sy - by) * 100, len(mm)))

log("============ 5. 容量估算（持仓80只） ============")
d_last = rebal[-2]
dd = panel.loc[d_last]
cand = get_candidates(d_last, dd)
score = score_v2(d_last, cand, dd).dropna()
top80 = score.sort_values(ascending=False).head(80).index
amt = panel.loc[trade_dates[ti[pd.Timestamp(d_last)]], "amount"]
for capital in [10e4, 100e4, 500e4, 1000e4]:
    per = capital / 80
    ratios = []
    for c in top80:
        a = amt.get(c)
        if a and a > 0:
            ratios.append(per / (a * 1000))
    r = np.array(ratios)
    pct = np.percentile(r, [10, 50, 90])
    log("资金 %6d万: 单票%6d元, 占日均成交额 P10=%.3f%% P50=%.3f%% P90=%.3f%%" % (
        capital / 1e4, per, pct[0] * 100, pct[1] * 100, pct[2] * 100))

ld = basic[basic["ts_code"].isin(codes_all)]
log("============ 6. 幸存者偏差确认 ============")
log("全池 %d 只，其中退市(list_status=D) %d 只，回测已纳入" % (len(codes_all), (ld["list_status"] == "D").sum()))
log("\n总用时 %.0fs" % (time.time() - t0))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(_log))
