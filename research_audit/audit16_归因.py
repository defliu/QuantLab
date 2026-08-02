# coding=utf-8
"""审计16：2026转负归因审计（风格beta vs 价值因子失效）
以 audit14_修正口径.py 为模板，复用全部数据加载/panel/财务PIT/缓存/评分/基准逻辑
输出：E:\\QuantLab\\research_audit\\audit16_结果.txt"""
import sys, time, os
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
import pandas as pd
import numpy as np
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

os.makedirs(r"E:\QuantLab\research_audit", exist_ok=True)
OUT = r"E:\QuantLab\research_audit\audit16_结果.txt"
_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    _log.append(s)

t0 = time.time()

# ========== 数据加载（原样复用 audit14） ==========
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
_cand_nofilter_cache = {}
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

def get_candidates_nofilter(d, dd):
    """无排雷：只保留市值/PE/PB过滤，不检查 bps/profit_dedt/roe"""
    if d in _cand_nofilter_cache:
        return _cand_nofilter_cache[d]
    m = (dd["circ_mv"] > 0) & (dd["circ_mv"] < 300000) & (dd["pe_ttm"] > 0) & (dd["pb"] > 0)
    r = m[m].index
    _cand_nofilter_cache[d] = r
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

# ========== 状态机 run_v2（原样复用） ==========
def run_v2(tx_cost=0.001, limit_handling=True, force_full_turn=False):
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

# ========== 基准构建（同口径等权，与 audit14 一致） ==========
log("============ 预计算：状态机V2收益 + 同池等权基准 ============")
v2_df = run_v2().set_index("date")

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
log("基准期数=%d  V2期数=%d" % (len(base), len(v2_df)))

# ========== A. 基准2026强弱（风格beta检验） ==========
log("\n============ A. 同池等权基准逐年收益 ============")
yearly_base = {}
for yr in range(2018, 2027):
    yr_start = pd.Timestamp("%d-01-01" % yr)
    yr_end = pd.Timestamp("%d-12-31" % yr)
    seg = base[(base.index >= yr_start) & (base.index <= yr_end)]
    if len(seg) == 0:
        yearly_base[yr] = None
        continue
    yearly_base[yr] = (1 + seg["ret"]).prod() - 1
    log("  %d: %+7.1f%%  (n=%d)" % (yr, yearly_base[yr] * 100, len(seg)))

log("\n2026定位：")
if yearly_base.get(2026) is not None:
    vals = {yr: v for yr, v in yearly_base.items() if v is not None}
    sorted_yrs = sorted(vals, key=vals.get)
    rank_2026 = sorted_yrs.index(2026) + 1
    log("基准2026=%+5.1f%%, 在%d年中排第%d（%s）" % (
        yearly_base[2026] * 100, len(vals), rank_2026,
        "最弱" if rank_2026 == len(vals) else "偏弱" if rank_2026 > len(vals) * 0.7 else "中等偏弱" if rank_2026 > len(vals) * 0.5 else "中等"))
else:
    log("2026无数据")

# ========== B. 月度超额归因 ==========
log("\n============ B. 月度超额归因（2024-01~2026-07） ============")
# V2 逐期收益与基准逐期收益对齐，按自然月重采样
# 策略每期有 e_date 标记（结算日），该期收益归属从上一期 e_date 到本期 e_date 的区间
# 将逐期收益按月归类：期 e_date 落在哪个月，收益归属该月
# 月度收益 = 月内各期 (1+r) 连乘 - 1

v2_aligned = v2_df[["period_return"]].copy()
v2_aligned.columns = ["ret"]
base_aligned = base[["ret"]].copy()

# 为每条记录标记年月
v2_aligned["ym"] = v2_aligned.index.to_period("M")
base_aligned["ym"] = base_aligned.index.to_period("M")

# 按月聚合（连乘）
def monthly_agg(grp):
    return (1 + grp["ret"]).prod() - 1

v2_monthly = v2_aligned.groupby("ym").apply(monthly_agg)
base_monthly = base_aligned.groupby("ym").apply(monthly_agg)

monthly = pd.DataFrame({"v2": v2_monthly, "base": base_monthly}).dropna()
monthly["excess"] = monthly["v2"] - monthly["base"]

neg_months = []
for ym, row in monthly.iterrows():
    if ym.start_time >= pd.Timestamp("2024-01-01") and ym.start_time <= pd.Timestamp("2026-07-31"):
        marker = ""
        if row["excess"] < 0:
            marker = " **负**"
            neg_months.append(str(ym))
        log("  %s  V2=%+6.1f%%  基准=%+6.1f%%  超额=%+6.1f%%%s" % (
            ym, row["v2"] * 100, row["base"] * 100, row["excess"] * 100, marker))

log("\n2026负超额月份：%s" % (", ".join(neg_months) if neg_months else "无"))
# 判断均匀衰减 vs 事件性
m26 = monthly[(monthly.index >= pd.Period("2026-01", "M")) & (monthly.index <= pd.Period("2026-07", "M"))]
if len(m26) > 0:
    neg_count_26 = (m26["excess"] < 0).sum()
    worst_26 = m26["excess"].min()
    log("2026共%d个月有数据，%d个月超额为负，最差月份超额=%+5.1f%%" % (
        len(m26), neg_count_26, worst_26 * 100))
    if neg_count_26 <= 2 and worst_26 < -0.03:
        log("归因判断：事件性（少数月份崩跌主导）")
    elif neg_count_26 >= len(m26) * 0.6:
        log("归因判断：均匀衰减（多数月份小幅跑输）")
    else:
        log("归因判断：混合型（部分月事件冲击+部分月持续小幅跑输）")

# ========== C. BP因子分层检验 ==========
log("\n============ C. BP因子分层检验（行业中性logBP z-score 5分组） ============")

def bp_quintile_returns(window_label, start_str, end_str):
    """计算BP行业中性z-score 5分组的逐期等权收益，窗口内累乘"""
    q_rets = {q: [] for q in range(1, 6)}  # Q1最低~Q5最高
    for i in range(len(rebal) - 1):
        d = rebal[i]
        nxt = rebal[i + 1]
        e_idx = ti[pd.Timestamp(d)] + 1
        x_idx = ti[pd.Timestamp(nxt)] + 1
        if x_idx >= len(trade_dates):
            break
        e_date_ts = pd.Timestamp(trade_dates[e_idx])
        x_date_ts = pd.Timestamp(trade_dates[x_idx])
        if x_date_ts < pd.Timestamp(start_str) or e_date_ts >= pd.Timestamp(end_str):
            continue
        dd = panel.loc[d]
        cand = get_candidates(d, dd)
        if len(cand) < 30:
            continue
        # 行业中性 logBP z
        bp = 1.0 / dd.loc[cand]["pb"].replace(0, np.nan)
        inds = pd.Series(cand, index=cand).map(ind_map)
        t = pd.DataFrame({"bp": bp, "ind": inds}).dropna()
        z = t.groupby("ind")["bp"].transform(lambda x: (x - x.mean()) / (x.std() + 1e-9))
        # 分5组
        z_sorted = z.sort_values()
        n = len(z_sorted)
        q_size = n // 5
        q_assign = pd.Series(index=z_sorted.index, dtype=int)
        for q in range(1, 6):
            start_pos = (q - 1) * q_size
            end_pos = q * q_size if q < 5 else n
            q_assign.iloc[start_pos:end_pos] = q
        # 每组等权收益：E日open买入，下期E日open卖出
        e_open = panel.loc[trade_dates[e_idx], "open"]
        x_open = panel.loc[trade_dates[x_idx], "open"]
        for q in range(1, 6):
            q_codes = q_assign[q_assign == q].index
            rets = []
            for c in q_codes:
                eo = e_open.get(c)
                xo = x_open.get(c)
                if eo is not None and xo is not None and eo > 0 and xo > 0:
                    rets.append(xo / eo - 1.0)
            if len(rets) > 0:
                q_rets[q].append(np.mean(rets))
    # 累乘
    result = {}
    for q in range(1, 6):
        if len(q_rets[q]) > 0:
            result["Q%d" % q] = (1 + np.array(q_rets[q])).prod() - 1
        else:
            result["Q%d" % q] = None
    if result["Q5"] is not None and result["Q1"] is not None:
        result["Q5-Q1"] = result["Q5"] - result["Q1"]
    else:
        result["Q5-Q1"] = None
    return result

windows = [("全期2018-2026", "2018-01-01", "2027-01-01"),
           ("2024+", "2024-01-01", "2027-01-01"),
           ("2026", "2026-01-01", "2027-01-01")]

for label, s, e in windows:
    log("--- %s ---" % label)
    res = bp_quintile_returns(label, s, e)
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5-Q1"]:
        v = res.get(q)
        if v is not None:
            log("  %s: %+7.1f%%" % (q, v * 100))
    # 判断
    q51 = res.get("Q5-Q1")
    if label == "2026" and q51 is not None:
        if q51 > 0:
            log("  判断：2026 BP因子仍有效（Q5-Q1=%+5.1f%%>0）" % (q51 * 100))
        elif q51 > -0.01:
            log("  判断：2026 BP因子基本失效（Q5-Q1≈0）")
        else:
            log("  判断：2026 BP因子反向（Q5-Q1=%+5.1f%%<0，价值因子失效！）" % (q51 * 100))

# ========== D. 历史分位因子分层 ==========
log("\n============ D. BP历史分位因子分层（bp_hist_pct 5分组） ============")

def bphist_quintile_returns(window_label, start_str, end_str):
    """用 bp_hist_pct 分5组，每组等权收益"""
    q_rets = {q: [] for q in range(1, 6)}
    for i in range(len(rebal) - 1):
        d = rebal[i]
        nxt = rebal[i + 1]
        e_idx = ti[pd.Timestamp(d)] + 1
        x_idx = ti[pd.Timestamp(nxt)] + 1
        if x_idx >= len(trade_dates):
            break
        e_date_ts = pd.Timestamp(trade_dates[e_idx])
        x_date_ts = pd.Timestamp(trade_dates[x_idx])
        if x_date_ts < pd.Timestamp(start_str) or e_date_ts >= pd.Timestamp(end_str):
            continue
        dd = panel.loc[d]
        cand = get_candidates(d, dd)
        if len(cand) < 30:
            continue
        hp = bp_hist_pct(d)
        if hp is None:
            continue
        hp_cand = hp.reindex(cand).dropna()
        if len(hp_cand) < 30:
            continue
        # 分5组
        hp_sorted = hp_cand.sort_values()
        n = len(hp_sorted)
        q_size = n // 5
        q_assign = pd.Series(index=hp_sorted.index, dtype=int)
        for q in range(1, 6):
            start_pos = (q - 1) * q_size
            end_pos = q * q_size if q < 5 else n
            q_assign.iloc[start_pos:end_pos] = q
        e_open = panel.loc[trade_dates[e_idx], "open"]
        x_open = panel.loc[trade_dates[x_idx], "open"]
        for q in range(1, 6):
            q_codes = q_assign[q_assign == q].index
            rets = []
            for c in q_codes:
                eo = e_open.get(c)
                xo = x_open.get(c)
                if eo is not None and xo is not None and eo > 0 and xo > 0:
                    rets.append(xo / eo - 1.0)
            if len(rets) > 0:
                q_rets[q].append(np.mean(rets))
    result = {}
    for q in range(1, 6):
        if len(q_rets[q]) > 0:
            result["Q%d" % q] = (1 + np.array(q_rets[q])).prod() - 1
        else:
            result["Q%d" % q] = None
    if result["Q5"] is not None and result["Q1"] is not None:
        result["Q5-Q1"] = result["Q5"] - result["Q1"]
    else:
        result["Q5-Q1"] = None
    return result

for label, s, e in windows:
    log("--- %s ---" % label)
    res = bphist_quintile_returns(label, s, e)
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q5-Q1"]:
        v = res.get(q)
        if v is not None:
            log("  %s: %+7.1f%%" % (q, v * 100))
    q51 = res.get("Q5-Q1")
    if label == "2026" and q51 is not None:
        if q51 > 0:
            log("  判断：2026历史分位因子仍有效（Q5-Q1=%+5.1f%%>0）" % (q51 * 100))
        elif q51 > -0.01:
            log("  判断：2026历史分位因子基本失效（Q5-Q1≈0）")
        else:
            log("  判断：2026历史分位因子反向（Q5-Q1=%+5.1f%%<0）" % (q51 * 100))

# ========== E. 质量排雷贡献 ==========
log("\n============ E. 2026排雷vs不排雷候选池基准收益对比 ============")
filter_rows = []
nofilter_rows = []
for i in range(len(rebal) - 1):
    d = rebal[i]
    nxt = rebal[i + 1]
    e_idx = ti[pd.Timestamp(d)] + 1
    x_idx = ti[pd.Timestamp(nxt)] + 1
    if x_idx >= len(trade_dates):
        break
    e_date_ts = pd.Timestamp(trade_dates[e_idx])
    x_date_ts = pd.Timestamp(trade_dates[x_idx])
    if x_date_ts < pd.Timestamp("2026-01-01") or e_date_ts >= pd.Timestamp("2027-01-01"):
        continue
    dd = panel.loc[d]
    e_open = panel.loc[trade_dates[e_idx], "open"]
    x_open = panel.loc[trade_dates[x_idx], "open"]

    # 有排雷
    cand_f = get_candidates(d, dd)
    valid_f = [c for c in cand_f if e_open.get(c) is not None and x_open.get(c) is not None
               and e_open.get(c) > 0 and x_open.get(c) > 0]
    if len(valid_f) >= 30:
        filter_rows.append({"date": x_date_ts,
                            "ret": np.mean([x_open[c] / e_open[c] - 1.0 for c in valid_f]),
                            "n": len(valid_f)})

    # 无排雷
    cand_nf = get_candidates_nofilter(d, dd)
    valid_nf = [c for c in cand_nf if e_open.get(c) is not None and x_open.get(c) is not None
                and e_open.get(c) > 0 and x_open.get(c) > 0]
    if len(valid_nf) >= 30:
        nofilter_rows.append({"date": x_date_ts,
                              "ret": np.mean([x_open[c] / e_open[c] - 1.0 for c in valid_nf]),
                              "n": len(valid_nf)})

df_filter = pd.DataFrame(filter_rows).set_index("date") if filter_rows else pd.DataFrame()
df_nofilter = pd.DataFrame(nofilter_rows).set_index("date") if nofilter_rows else pd.DataFrame()

log("  期数：有排雷=%d  无排雷=%d" % (len(df_filter), len(df_nofilter)))
if len(df_filter) > 0:
    cum_f = (1 + df_filter["ret"]).prod() - 1
    seg_f = "  ".join(["%s=%+5.1f%%" % (d.strftime("%Y%m%d"), r["ret"] * 100) for d, r in df_filter.iterrows()])
    log("  有排雷  2026累计=%+7.1f%%  逐期: %s" % (cum_f * 100, seg_f))
if len(df_nofilter) > 0:
    cum_nf = (1 + df_nofilter["ret"]).prod() - 1
    seg_nf = "  ".join(["%s=%+5.1f%%" % (d.strftime("%Y%m%d"), r["ret"] * 100) for d, r in df_nofilter.iterrows()])
    log("  无排雷  2026累计=%+7.1f%%  逐期: %s" % (cum_nf * 100, seg_nf))

if len(df_filter) > 0 and len(df_nofilter) > 0:
    diff = cum_f - cum_nf
    if diff > 0.005:
        log("  判断：排雷在2026正贡献（有排雷累计高出%+5.1f%%），排雷未误杀" % (diff * 100))
    elif diff < -0.005:
        log("  判断：排雷在2026负贡献（有排雷累计低%+5.1f%%），排雷误杀导致部分转负" % (diff * 100))
    else:
        log("  判断：排雷在2026贡献中性（差异%+5.1f%%），转负主因不在排雷" % (diff * 100))

# ========== 结论段 ==========
log("\n============ 结论 ============")
# 汇总各判断
base_2026 = yearly_base.get(2026)
v2_2026_seg = v2_df[v2_df.index >= pd.Timestamp("2026-01-01")]
if len(v2_2026_seg) > 0:
    v2_2026_cum = (1 + v2_2026_seg["period_return"]).prod() - 1
else:
    v2_2026_cum = None

log("A. 基准2026=%s" % ("%+5.1f%%" % (base_2026 * 100) if base_2026 is not None else "N/A"))
log("B. 月度超额归因见上")
log("C. BP因子分层见上")
log("D. 历史分位分层见上")
log("E. 排雷贡献见上")

if base_2026 is not None and base_2026 < -0.05:
    log("\n综合判断：2026转负主因=风格beta（小盘池整体下跌，基准跌幅大），V2立项风险：中")
elif base_2026 is not None and base_2026 > 0 and v2_2026_cum is not None and v2_2026_cum < 0:
    log("\n综合判断：2026转负主因=因子失效（基准涨而V2跌），V2立项风险：高")
else:
    log("\n综合判断：需结合BP分层和月度归因进一步判定，详见上方各项分析")

log("\n总用时 %.0fs" % (time.time() - t0))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(_log))
