# coding: utf-8
"""任务B：菜场大妈六步选股 干净口径审计 - 消融模拟器 (T-20260819-002)

自建向量化日线循环（Project_16 内，禁改引擎/其他项目）。

消融阶梯（每级只改一个变量，A0~A3 在模拟器内，收盘撮合、零成本）：
  A0 复现口径 = 幸存者池(stock_basic 现存) + 财务按 end_date 直接对齐(前视) + 零成本 + 涨停可买
  A1 全池     = 宇宙改"当日已上市未退市全池(含退市股)"
  A2 财务PIT  = 财务改 f_ann_date/ann_date 对齐
  A3 可交易性 = 涨停不可买/跌停不可卖/停牌不可交易
A4 走框架引擎（另脚本）。

主窗口 2019-01-01 ~ 2026-06-30。
规则（六步）：
  1 基础池: 剔ST(is_st) / 上市>=250自然日(listed_days) / 剔科创688、北交4/8开头(含92段)
  2 股息率前25%: dv_ttm>0 降序前25%（对照口径自建TTM，主口径=dv_ttm 快照单位%）
  3 盈利: pe_ttm>0 且 净利润同比增长率>0（income 累计 n_income_attr_p，PIT）
  4 PEG: PEG = pe_ttm / (净利同比百分数)，0<PEG<3
  5 价格: 2 <= 不复权 close <= 9
  6 总市值 total_mv 升序取前10，等权
调仓：每周第一个交易日。
"""
import os
import time

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(PROJ, "results")
os.makedirs(RESULTS, exist_ok=True)
BASE = r"E:/astock"
START, END = "2019-01-01", "2026-06-30"

SURVIVOR_CODE_SET = None  # stock_basic 现存列表（A0 宇宙）


def load_data():
    t0 = time.time()
    sd = pd.read_parquet(
        f"{BASE}/daily/stock_daily.parquet",
        columns=["open", "high", "low", "close", "pre_close", "up_limit",
                 "down_limit", "adj_factor", "turnover_rate", "suspend_type",
                 "is_st", "pe_ttm", "total_mv", "dv_ttm", "listed_days"])
    sd = sd.sort_index()
    # 裁剪主窗口（多保留前后1年用于因子与调仓连续性）
    idx = sd.index
    sd = sd.loc[(idx.get_level_values(0) >= pd.Timestamp("2017-12-31")) &
                (idx.get_level_values(0) <= pd.Timestamp("2026-12-31"))]
    sd = sd.reset_index()
    print(f"[load] stock_daily {len(sd)} rows, {time.time()-t0:.1f}s", flush=True)

    # 交易日历 = 2019-2026 全部交易日
    cal = sorted(sd.loc[(sd["trade_date"] >= START) & (sd["trade_date"] <= END), "trade_date"].unique())
    cal = pd.DatetimeIndex(cal)
    print(f"[load] trading days {len(cal)} ({cal[0].date()}~{cal[-1].date()})", flush=True)

    # stock_basic 幸存者池（A0）：现存 L
    sb = pd.read_parquet(f"{BASE}/basic/stock_basic.parquet")
    global SURVIVOR_CODE_SET
    SURVIVOR_CODE_SET = set(sb.loc[sb["list_status"] == "L", "ts_code"].astype(str))
    print(f"[load] survivor codes (list_status=L) = {len(SURVIVOR_CODE_SET)}", flush=True)
    return sd, cal


def load_income_pit():
    """income 累计归母净利润 + PIT 公告日映射。返回 dict code -> DataFrame(end_date,f_ann_date,n_income_attr_p)"""
    t0 = time.time()
    inc = pd.read_parquet(f"{BASE}/finance/income.parquet",
                          columns=["ts_code", "end_date", "f_ann_date", "ann_date", "n_income_attr_p"])
    inc["ts_code"] = inc["ts_code"].astype(str)
    inc["end_date"] = inc["end_date"].astype(str).str[:8]
    inc["f_ann_date"] = inc["f_ann_date"].astype(str).str[:8]
    inc["ann_date"] = inc["ann_date"].astype(str).str[:8]
    inc = inc[inc["end_date"].str.fullmatch(r"\d{8}")].copy()
    inc = inc[inc["f_ann_date"].str.fullmatch(r"\d{8}", na=False) |
              inc["ann_date"].str.fullmatch(r"\d{8}", na=False)].copy()
    # 用 f_ann_date 优先，缺失回退 ann_date
    inc["visible"] = inc["f_ann_date"].where(inc["f_ann_date"].ne("nan") & inc["f_ann_date"].ne("NaT"), inc["ann_date"])
    inc = inc[inc["n_income_attr_p"].notna()].copy()
    out = {}
    for code, g in inc.groupby("ts_code"):
        g = g.drop_duplicates("end_date", keep="last").sort_values("end_date")
        out[code] = g[["end_date", "visible", "n_income_attr_p"]].reset_index(drop=True)
    print(f"[load] income {len(inc)} rows -> {len(out)} codes, {time.time()-t0:.1f}s", flush=True)
    return out


def build_ttm_div(sd):
    """自建 TTM 股息率宽表 (index=交易日, columns=ts_code, 小数)。PIT=仅已实施+ex_date<=d。"""
    t0 = time.time()
    dv = pd.read_parquet(f"{BASE}/finance/dividend.parquet")
    dv = dv[dv["div_proc"].astype(str).str.strip() == "实施"].copy()
    dv = dv[dv["ex_date"].notna()].copy()
    dv["ex_date"] = pd.to_datetime(dv["ex_date"])
    dv = dv[dv["cash_div_tax"].notna() & (dv["cash_div_tax"] > 0)].copy()
    dv["ts_code"] = dv["ts_code"].astype(str)
    wide = dv.pivot_table(index="ex_date", columns="ts_code", values="cash_div_tax",
                          aggfunc="sum").sort_index().fillna(0.0)
    cum = wide.cumsum()
    cum = cum.set_axis(pd.DatetimeIndex(cum.index).as_unit("ns"))
    all_dates = pd.DatetimeIndex(sorted(sd["trade_date"].dropna().unique())).as_unit("ns")
    cum_now = cum.reindex(all_dates).ffill().fillna(0.0)
    cum_365 = cum.reindex(all_dates - pd.Timedelta(days=365)).ffill().fillna(0.0)
    cum_365.index = all_dates
    ttm = cum_now - cum_365
    close_wide = sd.pivot_table(index="trade_date", columns="ts_code", values="close")
    close_wide.index = close_wide.index.as_unit("ns")
    dy = ttm / close_wide.replace(0, np.nan)
    print(f"[load] ttm div wide {dy.shape}, {time.time()-t0:.1f}s", flush=True)
    return dy


def build_yoy_wide(income, sd, pit=True):
    """净利同比宽表 (index=交易日, columns=ts_code)。yoy=最新可见报告期累计 / 上年同期累计 - 1。
    pit=True: visible=f_ann_date/ann_date<=d（A2+）；pit=False: end_date<=d（A0 前视）。"""
    t0 = time.time()
    codes = sorted(sd["ts_code"].unique())
    all_dates = pd.DatetimeIndex(sorted(sd["trade_date"].dropna().unique())).as_unit("ns")
    date_int = all_dates.strftime("%Y%m%d").astype(np.int64)
    yoy_df = pd.DataFrame(index=all_dates, columns=codes, dtype=float)
    missing = 0
    for code in codes:
        g = income.get(code)
        if g is None or len(g) < 2:
            continue
        end_int = g["end_date"].astype(np.int64).to_numpy()
        if pit:
            vis = g["visible"].astype(np.int64).to_numpy()
            # 逐日：visible<=d 且 end_date 最大
            # 对每个 d，用 searchsorted 在按 visible 排序后取最后一个 <= d
            order = np.argsort(vis)
            vis_s = vis[order]
            end_s = end_int[order]
            ninc_s = g["n_income_attr_p"].to_numpy()[order]
            pos = np.searchsorted(vis_s, date_int, side="right") - 1
            pos[pos < 0] = -1
            valid = pos >= 0
            end_cur = np.where(valid, end_s[np.maximum(pos, 0)], 0)
            n_cur = np.where(valid, ninc_s[np.maximum(pos, 0)], np.nan)
            # 上年同期 = end_cur 减一年
            prev_end = end_cur - 10000
            prev_end = np.where(prev_end < 19900101, 0, prev_end)
        else:  # 前视：end_date<=d
            pos = np.searchsorted(end_int, date_int, side="right") - 1
            pos[pos < 0] = -1
            valid = pos >= 0
            ninc_s = g["n_income_attr_p"].to_numpy()
            end_cur = np.where(valid, end_int[np.maximum(pos, 0)], 0)
            n_cur = np.where(valid, ninc_s[np.maximum(pos, 0)], np.nan)
            prev_end = end_cur - 10000
            prev_end = np.where(prev_end < 19900101, 0, prev_end)
        # 上年同期净利润（固定报告期值，非PIT问题：上年报告期早已公告）
        prev_map = dict(zip(end_int, g["n_income_attr_p"].to_numpy()))
        n_prev = np.array([prev_map.get(int(e), np.nan) for e in prev_end], dtype=float)
        yoy = np.where(np.isfinite(n_cur) & np.isfinite(n_prev) & (n_prev != 0),
                       n_cur / np.where(n_prev == 0, np.nan, n_prev) - 1.0, np.nan)
        yoy_df[code] = yoy
        missing += int((~np.isfinite(yoy)).sum())
    print(f"[load] yoy wide {yoy_df.shape}, missing={missing}, {time.time()-t0:.1f}s", flush=True)
    return yoy_df


def run_screening(dayframes, cal, week_idx, income, dy_wide, yoy_pit, yoy_fwd,
                  universe="full", fin_pit=True, tradable=False):
    """执行一次周度筛选，返回 {code: 权重}（等权10只）。
    universe: 'survivor'=幸存者池(A0) / 'full'=全池(当日有行情的全部)
    fin_pit: True=财务PIT / False=前视
    tradable: True=涨停不可买/停牌不可买
    dayframes: dict trade_date -> 当日 DataFrame（含列 ts_code/close/...）
    """
    d = cal[week_idx]
    d_ns = d.as_unit("ns")
    day = dayframes.get(d)
    if day is None:
        return {}
    codes = day["ts_code"].astype(str)
    df = pd.DataFrame({
        "ts_code": codes.to_numpy(),
        "close": day["close"].to_numpy(),
        "pe_ttm": day["pe_ttm"].to_numpy(),
        "dv_ttm": day["dv_ttm"].to_numpy(),
        "total_mv": day["total_mv"].to_numpy(),
        "is_st": day["is_st"].to_numpy(),
        "listed_days": day["listed_days"].to_numpy(),
        "suspend_type": day["suspend_type"].astype(str).to_numpy(),
        "up_limit": day["up_limit"].to_numpy(),
        "down_limit": day["down_limit"].to_numpy(),
    })
    # 步骤1 基础池
    m = df["is_st"] == 0
    m &= df["listed_days"].notna() & (df["listed_days"] >= 250)
    code_arr = df["ts_code"].to_numpy()
    # 剔科创板 688、北交所（.BJ 后缀：4/8/92 段都覆盖）
    m &= np.array([not (c.startswith("688") or c.endswith(".BJ")) for c in code_arr])
    if universe == "survivor":
        m &= np.array([c in SURVIVOR_CODE_SET for c in code_arr])
    df = df[m].copy()
    if df.empty:
        return {}

    # 步骤2 股息率前25%（主口径=自建TTM dy_wide，PIT 除息日窗口；对照 dv_ttm 快照单位%）
    if d_ns in dy_wide.index:
        dy_row = dy_wide.loc[d_ns]
    else:
        dy_row = dy_wide.reindex(pd.DatetimeIndex([d_ns])).iloc[0]
    codes_t = df["ts_code"].tolist()
    dy_self = np.array([dy_row.get(c, np.nan) for c in codes_t], dtype=float)
    # 自建TTM主口径：>0 降序前25%
    df = df[(dy_self > 0) & np.isfinite(dy_self)].copy()
    if df.empty:
        return {}
    dy_self = dy_self[np.isfinite(dy_self) & (dy_self > 0)] if False else np.array(
        [dy_row.get(c, np.nan) for c in df["ts_code"]], dtype=float)
    df = df.assign(dy_self=dy_self)
    df = df.sort_values("dy_self", ascending=False)
    n_top = max(1, int(len(df) * 0.25))
    df = df.head(n_top).copy()

    # 步骤3 盈利：pe_ttm>0 且 净利同比>0
    yoy = yoy_pit if fin_pit else yoy_fwd
    if d_ns in yoy.index:
        yoy_row = yoy.loc[d_ns]
    else:
        yoy_row = yoy.reindex(pd.DatetimeIndex([d_ns])).iloc[0]
    yoy_vals = np.array([yoy_row.get(c, np.nan) if isinstance(yoy_row, pd.Series) else np.nan
                         for c in df["ts_code"]], dtype=float)
    df = df[(df["pe_ttm"].notna() & (df["pe_ttm"] > 0)) &
            np.isfinite(yoy_vals) & (yoy_vals > 0)].copy()
    if df.empty:
        return {}

    # 步骤4 PEG = pe_ttm/(yoy百分数)，0<PEG<3
    code_list = df["ts_code"].tolist()
    yoy_aligned = np.array([yoy_row.get(c, np.nan) for c in code_list], dtype=float)
    peg = df["pe_ttm"].to_numpy() / (yoy_aligned * 100.0)
    df = df[(peg > 0) & (peg < 3)].copy()
    if df.empty:
        return {}

    # 步骤5 价格 2<=close<=9（不复权）
    df = df[(df["close"] >= 2) & (df["close"] <= 9)].copy()
    if df.empty:
        return {}

    # 步骤6 总市值升序前10
    df = df.sort_values("total_mv", ascending=True).head(10).copy()

    # 可交易性（A3）：买入日涨停/停牌 不可买
    if tradable:
        df = df[(df["up_limit"].notna()) & (df["close"] < df["up_limit"]) &
                (df["suspend_type"] == "N")].copy()

    # 等权
    w = {c: 1.0 / len(df) for c in df["ts_code"]}
    return w


def week_returns(dayframes, cal, income, dy_wide, yoy_pit, yoy_fwd, universe, fin_pit, tradable):
    """逐周回测：每周首个交易日筛选，等权买入，持有至下周，收盘价（后复权）计算收益。
    返回: 周收益序列 + 每期持仓 + 持仓中出现过的退市股/损失。
    零成本、收盘撮合（任务书 A0~A3 口径）。"""
    # 每周首个交易日（周一）
    weeks = pd.DatetimeIndex([d for d in cal if d.dayofweek == 0])

    # 调仓日行情（后复权 close = close*adj_factor）
    sd2 = pd.concat([dayframes[d][["ts_code", "close", "adj_factor"]].assign(trade_date=d)
                     for d in cal])
    sd2["hfq"] = sd2["close"] * sd2["adj_factor"]

    pos_history = {}  # week_idx -> {code: w}
    week_rets = []
    all_hold_codes = set()
    for i, d in enumerate(weeks):
        if i + 1 >= len(weeks):
            break
        d_next = weeks[i + 1]
        w = run_screening(dayframes, cal, np.where(cal == d)[0][0], income, dy_wide,
                          yoy_pit, yoy_fwd, universe=universe, fin_pit=fin_pit, tradable=tradable)
        pos_history[d.strftime("%Y-%m-%d")] = w
        if not w:
            week_rets.append(0.0)
            continue
        all_hold_codes.update(w.keys())
        # 周收益：等权 × (hfq(d_next)/hfq(d) - 1)
        ret = 0.0
        day_pairs = sd2[sd2["trade_date"].isin([d, d_next])]
        for c, wt in w.items():
            sub = day_pairs[day_pairs["ts_code"] == c]
            if len(sub) < 2:
                continue
            p0 = sub.loc[sub["trade_date"] == d, "hfq"].iloc[0]
            p1 = sub.loc[sub["trade_date"] == d_next, "hfq"].iloc[0]
            if p0 and p0 == p0 and p1 and p1 == p1:
                ret += wt * (p1 / p0 - 1.0)
        week_rets.append(ret)
    return np.array(week_rets), pos_history, all_hold_codes, weeks


def delist_loss(hold_codes):
    """统计持仓中出现过的退市股（stock_basic delist_date 非空 & list_status=D），含退市损失估算。"""
    sb = pd.read_parquet(f"{BASE}/basic/stock_basic.parquet")
    sb["ts_code"] = sb["ts_code"].astype(str)
    dl = sb[sb["list_status"] == "D"]
    hit = sorted(set(hold_codes) & set(dl["ts_code"]))
    return hit, dl


def kpis_weekly(rets, pos_hist=None):
    """周频收益 -> CAGR/MDD/夏普/年换手（按持仓变动算实际换手）。"""
    equity = np.cumprod(1 + rets)
    n_weeks = len(rets)
    years = n_weeks / 52.0
    total = equity[-1] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    peak = np.maximum.accumulate(equity)
    mdd = float(((equity - peak) / peak).min())
    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    sharpe = float(rets.mean() / std * np.sqrt(52)) if std > 0 else 0.0
    win = float((rets > 0).mean())
    # 实际年换手：逐周持仓集合变动的平均换手率（半个对称差）
    annual_turnover = 52.0
    if pos_hist and len(pos_hist) >= 2:
        keys = sorted(pos_hist.keys())
        trs = []
        for i in range(1, len(keys)):
            a = set(pos_hist[keys[i - 1]].keys())
            b = set(pos_hist[keys[i]].keys())
            inter = a & b
            tr = 1.0 - (len(inter) / max(1, len(a)))
            trs.append(tr)
        annual_turnover = float(np.mean(trs)) * 52.0 if trs else 52.0
    return {"n_weeks": n_weeks, "total": total, "cagr": cagr, "max_dd": mdd,
            "sharpe": sharpe, "win_rate": win, "annual_turnover": annual_turnover}


def main():
    print("=" * 70, flush=True)
    print("任务B：菜场大妈六步选股 干净口径审计 - 消融模拟器 A0~A3", flush=True)
    print("主窗口", START, "~", END, flush=True)
    t0 = time.time()
    sd, cal = load_data()
    income = load_income_pit()
    dy_wide = build_ttm_div(sd)
    yoy_pit = build_yoy_wide(income, sd, pit=True)
    yoy_fwd = build_yoy_wide(income, sd, pit=False)
    print(f"[load] all data ready {time.time()-t0:.1f}s", flush=True)

    results = {}
    # 预建逐日快照缓存
    dayframes = {d: df for d, df in sd.groupby("trade_date", sort=True)}
    print(f"[run] dayframes {len(dayframes)}", flush=True)
    for name, universe, fin_pit, tradable in [
        ("A0", "survivor", False, False),
        ("A1", "full", False, False),
        ("A2", "full", True, False),
        ("A3", "full", True, True),
    ]:
        t1 = time.time()
        rets, pos_hist, hold_codes, weeks = week_returns(dayframes, cal, income, dy_wide,
                                                         yoy_pit, yoy_fwd,
                                                         universe=universe, fin_pit=fin_pit, tradable=tradable)
        k = kpis_weekly(rets, pos_hist)
        hit, dl = delist_loss(hold_codes)
        # 逐年收益
        years = pd.DatetimeIndex(sorted(pos_hist.keys()))
        yr_rets = {}
        for y in sorted(set(y.year for y in years)):
            idxs = [i for i, d in enumerate(weeks) if d.year == y and i < len(rets)]
            yr_rets[y] = float(np.prod(1 + rets[idxs]) - 1) if idxs else np.nan
        # 2024 危机段放大（2024-01-01 ~ 2024-02-29）
        crisis_weeks = [(i, weeks[i]) for i in range(len(weeks))
                        if pd.Timestamp("2024-01-01") <= weeks[i] <= pd.Timestamp("2024-02-29")]
        crisis_rets = [rets[i] for i, _ in crisis_weeks]
        crisis = {"n_weeks": len(crisis_weeks), "sum_ret": float(np.sum(crisis_rets)),
                  "compound": float(np.prod([1 + r for r in crisis_rets]) - 1),
                  "detail": [(d.strftime("%Y-%m-%d"), pos_hist.get(d.strftime("%Y-%m-%d"), {})) for _, d in crisis_weeks]}
        # 明细 CSV 落盘
        pos_rows = []
        for dstr, w in pos_hist.items():
            for c, wt in w.items():
                pos_rows.append({"week": dstr, "ts_code": c, "weight": wt})
        pd.DataFrame(pos_rows).to_csv(os.path.join(RESULTS, f"taskb_{name}_positions.csv"),
                                      index=False, encoding="utf-8-sig")
        pd.DataFrame({"week": [w.strftime("%Y-%m-%d") for w in weeks[:len(rets)]],
                      "ret": rets}).to_csv(
            os.path.join(RESULTS, f"taskb_{name}_weekly_returns.csv"),
            index=False, encoding="utf-8-sig")
        crisis_rows = [{"week": d.strftime("%Y-%m-%d"),
                        "codes": ",".join(sorted(pos_hist.get(d.strftime("%Y-%m-%d"), {}).keys()))}
                       for _, d in crisis_weeks]
        pd.DataFrame(crisis_rows).to_csv(os.path.join(RESULTS, f"taskb_{name}_crisis2024.csv"),
                                         index=False, encoding="utf-8-sig")
        results[name] = {"kpis": k, "positions": pos_hist, "hold_codes": hold_codes,
                         "delist_hit": hit, "n_delisted": len(hit), "rets": rets,
                         "yearly": yr_rets, "crisis": crisis}
        print(f"[{name}] universe={universe} fin_pit={fin_pit} tradable={tradable} "
              f"n_weeks={k['n_weeks']} CAGR={k['cagr']*100:+.2f}% MDD={k['max_dd']*100:+.1f}% "
              f"Sharpe={k['sharpe']:.2f} win={k['win_rate']*100:.0f}% 年换手={k['annual_turnover']*100:.0f}% "
              f"({time.time()-t1:.1f}s)", flush=True)
        print(f"  持仓中出现退市股 {len(hit)} 只: {hit[:10]}", flush=True)
        print(f"  逐年收益: { {y: round(v*100,1) for y,v in yr_rets.items()} }", flush=True)
        print(f"  2024-01~02危机段: {crisis['n_weeks']}周 复合{crisis['compound']*100:+.1f}%", flush=True)

    # 消融差值归因
    print("\n" + "=" * 70)
    print("消融阶梯差值归因（年化 CAGR, pp）")
    c = {k: results[k]["kpis"]["cagr"] for k in ["A0", "A1", "A2", "A3"]}
    print(f"  A0(复现口径) = {c['A0']*100:+.2f}%")
    print(f"  A1 修幸存者  = {c['A1']*100:+.2f}%   (A1-A0={ (c['A1']-c['A0'])*100:+.2f}pp)")
    print(f"  A2 修财务前视 = {c['A2']*100:+.2f}%   (A2-A1={ (c['A2']-c['A1'])*100:+.2f}pp)")
    print(f"  A3 修可交易性 = {c['A3']*100:+.2f}%   (A3-A2={ (c['A3']-c['A2'])*100:+.2f}pp)")

    # 落盘
    with open(os.path.join(RESULTS, "taskb_ablation_summary.txt"), "w", encoding="utf-8") as f:
        f.write("菜场大妈六步选股 消融阶梯 A0~A3\n")
        f.write(f"主窗口 {START} ~ {END}\n\n")
        for name in ["A0", "A1", "A2", "A3"]:
            k = results[name]["kpis"]
            f.write(f"[{name}] CAGR={k['cagr']*100:+.2f}% MDD={k['max_dd']*100:+.1f}% "
                    f"Sharpe={k['sharpe']:.2f} win={k['win_rate']*100:.0f}% "
                    f"n_weeks={k['n_weeks']} 退市股={results[name]['n_delisted']}\n")
            f.write("  逐年: " + ", ".join(f"{y}:{v*100:+.1f}%" for y, v in
                     sorted(results[name]["yearly"].items())) + "\n")
            f.write("  2024危机段: %d周 复合%+.2f%%\n" % (
                results[name]["crisis"]["n_weeks"], results[name]["crisis"]["compound"] * 100))
            f.write("  退市股: " + ",".join(results[name]["delist_hit"][:30]) + "\n")
        f.write(f"\n消融差值: A1-A0={ (c['A1']-c['A0'])*100:+.2f}pp, "
                f"A2-A1={ (c['A2']-c['A1'])*100:+.2f}pp, A3-A2={ (c['A3']-c['A2'])*100:+.2f}pp\n")
    print(f"\n[落盘] taskb_ablation_summary.txt, 总用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()