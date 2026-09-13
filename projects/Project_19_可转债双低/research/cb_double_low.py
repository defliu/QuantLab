# -*- coding: utf-8 -*-
"""Project_19 可转债双低 - 轻量回测（W2-1）。

设计（对齐 SPEC_可转债双低_20260912.md §三/§四）：
  - 三硬前置：delist_date∪CEASE_DATE∪vol>0 三保险；双锚点溢价率(--conv-mode)；
    强赎表东财 NOTICE_DATE（公告日 T+1 强制卖出）
  - P0 排雷：R1 评级>=AA-(issue_rating 作 PIT) / R4a 强赎公告即卖 / R5 正股 ST 兜底
  - 信号：T 日收盘双低排序 topN -> T+1 日 open 成交（严格无前视）
  - 调仓：月频（默认）；成本佣金万2双边、无印花税、T+0
  - 输出：分年超额表 + 预注册证伪判据 5 条判定

用法：
  python research/cb_double_low.py [--freq month|week] [--topn 20]
                                   [--conv-mode conv|first] [--start 20190101]
                                   [--drop-best] [--oos] [--verbose]
"""
import argparse
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
ASTOCK_DAILY = r"D:\astock\daily\stock_daily.parquet"
COST = 0.0002  # 佣金万2双边
RATING_OK = {"AA-", "AA", "AA+", "AAA"}


# ----------------------------------------------------------------------
# 数据加载（离线数据包：D:/astock/cb/）
# ----------------------------------------------------------------------
def _d8(x):
    """float/str/datetime 日期 -> 'YYYYMMDD'（处理 20020628.0 / 2018-09-04 / datetime64）"""
    if x is None or pd.isna(x):
        return ""
    if hasattr(x, "strftime"):  # datetime64/datetime
        return x.strftime("%Y%m%d")
    s = str(x)
    if "." in s:
        s = s.split(".")[0]
    return s.replace("-", "").replace(" ", "")


def load_all(variant="baseline"):
    CB_DIR = r"D:\astock\cb"
    basic = pd.read_parquet(os.path.join(CB_DIR, "cb_list.parquet")).reset_index()
    daily = pd.read_parquet(os.path.join(CB_DIR, "cb_daily.parquet")).reset_index()
    call = pd.read_parquet(os.path.join(CB_DIR, "cb_call.parquet")).reset_index()

    # 日期归一 'YYYYMMDD'
    for col in ("list_date", "delist_date", "maturity_date"):
        if col in basic.columns:
            basic[col] = basic[col].map(_d8)
    daily["trade_date"] = daily["trade_date"].map(_d8)
    call["ann_date"] = call["ann_date"].map(_d8)

    # 强赎公告表 -> {ts_code: 最早触发公告日}
    # 严格口径(baseline/其他变体)：任何强赎类信号（实施/提示/已满足）即规避——与东财 NOTICE_DATE 旧口径对齐（2026-09-13 首轮可复现基准）
    # 宽松口径(loose_call / prem_0_loose)：仅"实施强赎/到期赎回"才规避（强赎公告前继续持有）
    if variant in ("loose_call", "prem_0_loose"):
        trigger_kw = ("实施强赎", "到期赎回")
    else:
        trigger_kw = ("强赎", "到期赎回")
    call = call[call["is_call"].astype(str).apply(
        lambda s: any(k in s for k in trigger_kw) and "不强赎" not in s)]
    notice_map = {}
    for code, d in zip(call["ts_code"], call["ann_date"]):
        if d:
            key = str(code)
            if key not in notice_map or d < notice_map[key]:
                notice_map[key] = d

    # 正股：只读转债正股相关券（不复权 close + is_st）
    stk_codes = [c for c in basic["stk_code"].dropna().unique() if str(c)]
    stk = pd.read_parquet(ASTOCK_DAILY,
                          columns=["trade_date", "ts_code", "close", "is_st"],
                          filters=[("ts_code", "in", stk_codes)])
    stk = stk.reset_index()
    stk["trade_date"] = stk["trade_date"].map(_d8)

    return basic, daily, stk, notice_map


# ----------------------------------------------------------------------
# 每日 universe（向量化）
# ----------------------------------------------------------------------
def build_universe(basic, daily, stk, notice_map, start, variant="baseline",
                   end="", min_listed_days=0):
    """返回 daily（每日 PIT 过滤 + 双低分）。溢价率直接用离线 cb_over_rate（历史真实口径）。

    variant 改良 ablation：
      baseline   : double_low = close + prem（纯双低基线）
      prem_0_5   : double_low = close + 0.5*prem（低溢价增强）
      prem_0     : double_low = close（纯低价格）
      prem_1_5   : double_low = close + 1.5*prem（更保守双低）
      rating_aa  : 评级下限提至 AA（排除 AA-）
      window_2_5 : 剩余期限限定 2~5 年
      loose_call : 强赎规避改宽松口径——仅"实施强赎/到期赎回"才卖（在 load_all 处理，此处同 baseline 计算）
      prem_0_loose: 组合变体——纯低价格(α=0) + 宽松强赎规避
    """
    daily = daily[daily["trade_date"] >= start].copy()
    if end:
        daily = daily[daily["trade_date"] <= end].copy()
    daily["vol"] = daily["vol"].fillna(0.0)
    daily = daily[daily["vol"] > 0]  # 僵尸行杀手（三保险之三）
    daily = daily[daily["close"] > 0]

    # 每券基础信息（PIT 静态部分）
    bmap = basic.set_index("ts_code")
    daily["cb_type"] = daily["ts_code"].map(bmap["cb_type"])
    daily = daily[daily["cb_type"] == "CB"]  # 排除 EB/定向
    daily["list_date"] = daily["ts_code"].map(bmap["list_date"])
    daily["maturity_date"] = daily["ts_code"].map(bmap["maturity_date"])
    daily["issue_rating"] = daily["ts_code"].map(bmap["issue_rating"])
    daily["stk_code"] = daily["ts_code"].map(bmap["stk_code"])
    daily["delist_date"] = daily["ts_code"].map(bmap["delist_date"])
    daily["notice_date"] = daily["ts_code"].map(
        lambda c: notice_map.get(str(c), ""))

    # 正股 join（不复权收盘 + ST）
    stk_close = stk.set_index(["trade_date", "ts_code"])["close"]
    stk_st = stk.set_index(["trade_date", "ts_code"])["is_st"]
    daily["stk_close"] = [stk_close.get((d, s), np.nan)
                          for d, s in zip(daily["trade_date"], daily["stk_code"])]
    daily["stk_st"] = [stk_st.get((d, s), 1) for d, s in zip(daily["trade_date"],
                                                             daily["stk_code"])]
    daily = daily.dropna(subset=["stk_close"])
    daily = daily[daily["stk_close"] > 0]

    # 溢价率（离线官方历史口径）+ 双低分；过滤异常溢价率
    daily["prem"] = daily["cb_over_rate"].astype(float)
    daily = daily[(daily["prem"] >= -50) & (daily["prem"] <= 500)]
    alpha = {"baseline": 1.0, "prem_0_5": 0.5, "prem_0": 0.0,
             "prem_1_5": 1.5, "prem_0_loose": 0.0}.get(variant, 1.0)
    daily["double_low"] = daily["close"] + alpha * daily["prem"]

    # P0 排雷（向量化）
    rating_ok = RATING_OK
    if variant == "rating_aa":
        rating_ok = {"AA", "AA+", "AAA"}
    daily["ok_rating"] = daily["issue_rating"].isin(rating_ok)
    if variant == "window_2_5":
        # 剩余期限 2~5 年（730~1826 天）
        daily["ok_mature"] = (
            daily["maturity_date"].fillna("99991231").ge(
                daily["trade_date"].apply(lambda d: _add_days(d, 730)))
        ) & (
            daily["maturity_date"].fillna("99991231").le(
                daily["trade_date"].apply(lambda d: _add_days(d, 1826)))
        )
    else:
        daily["ok_mature"] = daily["maturity_date"].fillna("99991231") >= \
            daily["trade_date"].apply(lambda d: _add_days(d, 183))  # 剩余>=0.5年
    daily["ok_notice"] = (daily["notice_date"] == "") | (daily["trade_date"] < daily["notice_date"])
    daily["ok_delist"] = daily["delist_date"].fillna("").eq("") | \
        (daily["trade_date"] < daily["delist_date"].fillna("99991231"))
    daily["ok_st"] = daily["stk_st"] == 0
    if min_listed_days > 0:
        # 次新券排雷（DE R4 建议）：上市满 N 个交易日才入选
        daily["ok_listed"] = daily["list_date"].fillna("").ne("") & (
            daily["trade_date"] >= daily["list_date"].apply(
                lambda d: _add_days(d, min_listed_days) if d else "00000000"))
    else:
        daily["ok_listed"] = daily["list_date"].fillna("").eq("") | \
            (daily["trade_date"] >= daily["list_date"].fillna("00000000"))

    daily["pass"] = daily[["ok_rating", "ok_mature", "ok_notice",
                           "ok_delist", "ok_st", "ok_listed"]].all(axis=1)
    return daily[daily["pass"]]


def _add_days(d, days):
    import datetime as _dt
    try:
        t = _dt.datetime.strptime(str(d), "%Y%m%d")
        return (t + _dt.timedelta(days=days)).strftime("%Y%m%d")
    except ValueError:
        return "99991231"


# ----------------------------------------------------------------------
# 回测主循环
# ----------------------------------------------------------------------
def backtest(daily, idx, freq="month", topn=20, verbose=False):
    dates = sorted(daily["trade_date"].unique())
    idx_dates = set(idx.index)
    dates = [d for d in dates if d in idx_dates]
    if not dates:
        raise RuntimeError("no common dates")

    if freq == "month":
        months = sorted({d[:6] for d in dates})
        signal_days = [max(d for d in dates if d.startswith(m)) for m in months]
    else:
        # 周频：ISO 周的最后交易日
        groups = {}
        for d in dates:
            key = _iso_week(d)
            groups.setdefault(key, d)
        signal_days = [groups[k] for k in sorted(groups)]

    # 预索引：trade_date -> df（避免 70 万行逐日全扫）
    daily_by_date = {d: g for d, g in daily.groupby("trade_date")}

    nav = 1.0
    day_ret = {}
    costs = 0.0
    trades = 0

    targets = {}
    for t in signal_days:
        sub = daily_by_date[t].sort_values("double_low").head(topn) if t in daily_by_date else None
        if sub is not None and len(sub):
            w = 1.0 / len(sub)
            targets[t] = {c: w for c in sub["ts_code"].tolist()}
        else:
            targets[t] = {}

    daily_idx = {d: i for i, d in enumerate(dates)}
    # ---- 简化：直接按信号日+1 成交 ----
    exec_days = {}
    for t in signal_days:
        ti = daily_idx.get(t)
        if ti is not None and ti + 1 < len(dates):
            exec_days[t] = dates[ti + 1]

    holdings = {}          # ts_code -> weight（open 建仓）
    open_px = {}           # 建仓时 open
    prev_close = {}
    for i, d in enumerate(dates):
        today_cost = 0.0
        # 1) 换仓执行（在 exec 日开盘成交）
        for t, ed in exec_days.items():
            if ed == d:
                tgt = targets.get(t, {})
                # 权重变化（换仓成本）
                old_w = {c: holdings.get(c, 0.0) for c in set(holdings) | set(tgt)}
                new_w = {c: tgt.get(c, 0.0) for c in set(holdings) | set(tgt)}
                delta = sum(abs(new_w.get(c, 0.0) - old_w.get(c, 0.0)) for c in old_w)
                today_cost = delta * COST
                costs += today_cost
                trades += len(set(holdings) ^ set(tgt))
                holdings = dict(tgt)
                # 新买入以 open 记
                day_df = daily_by_date.get(d)
                if day_df is not None:
                    buys = set(tgt) - set(old_w)
                    if buys:
                        op = day_df.set_index("ts_code")["open"]
                        for c in buys:
                            open_px[c] = float(op.get(c, np.nan))
                break

        # 2) 当日组合收益
        ret = 0.0
        if holdings:
            sub = daily_by_date.get(d)
            if sub is not None:
                p = sub.set_index("ts_code")
                for c, w in holdings.items():
                    if c not in p.index:
                        ret += 0.0
                        continue
                    close_t = float(p.loc[c, "close"])
                    if c in open_px and not np.isnan(open_px[c]):
                        r = close_t / open_px[c] - 1.0  # 建仓日 open->close
                        open_px.pop(c, None)
                    elif c in prev_close and prev_close[c] > 0:
                        r = close_t / prev_close[c] - 1.0
                    else:
                        r = 0.0
                    ret += w * r
                    prev_close[c] = close_t
        # 清掉不在持仓的 prev_close
        if holdings:
            prev_close = {c: v for c, v in prev_close.items() if c in holdings}
        r_day = ret - today_cost
        nav *= (1.0 + r_day)
        day_ret[d] = r_day
        if verbose and i % 200 == 0:
            print("  day %s nav=%.4f" % (d, nav), flush=True)

    sr = pd.Series(day_ret)
    return nav, sr, costs, trades, targets


def _iso_week(d):
    import datetime as _dt
    t = _dt.datetime.strptime(str(d), "%Y%m%d")
    return "%04d-%02d" % t.isocalendar()[:2]


# ----------------------------------------------------------------------
# 指标与报告
# ----------------------------------------------------------------------
def metrics(nav, sr, idx):
    # 策略净值序列（对齐 idx）
    dates = sorted(sr.index)
    nav_series = (1 + sr).cumprod()
    ret = nav_series.iloc[-1] - 1
    years = len(dates) / 244.0
    cagr = (nav_series.iloc[-1]) ** (1 / years) - 1 if years > 0 else 0
    dd = (nav_series / nav_series.cummax() - 1).min()
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    sharpe = sr.mean() / (sr.std() + 1e-12) * np.sqrt(244) if sr.std() > 0 else 0
    return {"total": ret, "cagr": cagr, "maxdd": dd, "calmar": calmar,
            "sharpe": sharpe, "years": years}


def yearly_table(sr, idx):
    idx = idx[idx.index.isin(sr.index)]
    strat_y = (1 + sr).groupby(sr.index.str[:4]).prod() - 1
    bench_y = (1 + idx.pct_change().fillna(0)).groupby(idx.index.str[:4]).prod() - 1
    ex = strat_y - bench_y
    out = pd.DataFrame({"策略": strat_y, "中证转债": bench_y, "超额": ex})
    return out.round(4)


def judge_criteria(yearly, cagr, maxdd, conv_mode, freq, topn):
    """预注册证伪判据（SPEC §四），返回 {判据: PASS/FAIL/待定, 证据}"""
    res = {}
    neg_years = int((yearly["超额"] < 0).sum()) if len(yearly) else 99
    res["C1 负年份>=3 或全期超额<3%"] = (
        "PASS" if (neg_years < 3 and cagr >= 0.03) else "FAIL",
        "负年份=%d 全期CAGR=%.1f%%" % (neg_years, cagr * 100))
    ex_all = yearly["超额"]
    if len(ex_all) >= 2:
        best = ex_all.idxmax()
        ex_drop = ex_all.drop(best)
        res["C2 剔最佳年后超额<2%"] = (
            "PASS" if ex_drop.mean() >= 0.02 else "FAIL",
            "剔%s后均值=%.2f%%" % (best, ex_drop.mean() * 100))
    else:
        res["C2"] = ("待定", "样本不足")
    recent = ex_all[ex_all.index >= "2024"] if len(ex_all) else pd.Series(dtype=float)
    if len(recent) >= 2:
        res["C3 2024+ 超额<2%且单调下滑"] = (
            "PASS" if (recent.mean() >= 0.02 or len(recent) < 3) else "FAIL",
            "2024+均值=%.2f%%" % (recent.mean() * 100))
    else:
        res["C3"] = ("待定", "2024+样本不足")
    res["C4 全开年化<8%或回撤>-20%"] = (
        "PASS" if cagr >= 0.08 and maxdd >= -0.20 else "FAIL",
        "CAGR=%.1f%% 回撤=%.1f%%" % (cagr * 100, maxdd * 100))
    res["C5 样本外(OOS)"] = ("待定", "需 --oos 分窗")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", default="month", choices=["month", "week"])
    ap.add_argument("--topn", type=int, default=20)
    ap.add_argument("--start", default="20190101")
    ap.add_argument("--variant", default="baseline",
                    choices=["baseline", "prem_0_5", "prem_0", "prem_1_5",
                             "rating_aa", "window_2_5", "loose_call",
                             "prem_0_loose"])
    ap.add_argument("--end", default="", help="OOS 截断日 YYYYMMDD（空=到数据末日）")
    ap.add_argument("--min-list-days", type=int, default=0,
                    help="次新券排雷：上市满 N 个交易日才入选（0=关闭）")
    ap.add_argument("--cost", type=float, default=None,
                    help="覆盖 COST 单边成本（成本敏感度测试，如 0.0015）")
    ap.add_argument("--drop-best", action="store_true")
    ap.add_argument("--oos", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    global COST
    if args.cost is not None:
        COST = args.cost

    print("[load] freq=%s topn=%d variant=%s (offline cb_over_rate)" % (
        args.freq, args.topn, args.variant))
    basic, daily, stk, notice_map = load_all(args.variant)
    idx = pd.read_parquet(os.path.join(DATA, "cb_index.parquet"))
    idx["trade_date"] = idx["trade_date"].astype(str).str.replace("-", "", regex=False)
    idx = idx.sort_values("trade_date").set_index("trade_date")["close"]
    if args.end:
        idx = idx[idx.index <= args.end]
    print("[load] basic=%d daily=%d stk=%d notice=%d idx=%d" % (
        len(basic), len(daily), len(stk), len(notice_map), len(idx)))
    univ = build_universe(basic, daily, stk, notice_map, args.start,
                          args.variant, args.end, args.min_list_days)
    print("[universe] rows=%d dates=%d" % (len(univ), univ["trade_date"].nunique()))

    nav, sr, costs, trades, targets = backtest(univ, idx, args.freq, args.topn,
                                               args.verbose)
    m = metrics(nav, sr, idx)
    yt = yearly_table(sr, idx)
    print("=" * 70)
    print("[result] %s topn=%d variant=%s" % (args.freq, args.topn, args.variant))
    print("  total=%.1f%% CAGR=%.1f%% MaxDD=%.1f%% Calmar=%.2f Sharpe=%.2f" % (
        m["total"] * 100, m["cagr"] * 100, m["maxdd"] * 100, m["calmar"], m["sharpe"]))
    print("  换仓成本累计=%.2f%%  调仓次数=%d" % (costs * 100, trades))
    print(yt.to_string())
    print("=" * 70)
    j = judge_criteria(yt, m["cagr"], m["maxdd"], args.variant, args.freq, args.topn)
    for k, (v, ev) in j.items():
        print("  [%s] %s  %s" % (v, k, ev))

    out_dir = os.path.join(os.path.dirname(HERE), "results")
    os.makedirs(out_dir, exist_ok=True)
    tag = "%s_t%d_%s" % (args.freq, args.topn, args.variant)
    pd.DataFrame({"nav": (1 + sr).cumprod()}).to_csv(
        os.path.join(out_dir, "nav_%s.csv" % tag))
    yt.to_csv(os.path.join(out_dir, "yearly_%s.csv" % tag))
    print("[saved] results/nav_%s.csv & yearly_%s.csv" % (tag, tag))


if __name__ == "__main__":
    main()
