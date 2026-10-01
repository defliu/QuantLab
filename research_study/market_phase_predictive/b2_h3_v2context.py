# -*- coding: utf-8 -*-
"""
B 路线 Part 2: H3 — P10 V2 候选池 fwd 超额的 phase 分层检验(冻结判据)
====================================================================
预注册依据: 预注册方案_B路线预测力检验.md 第四节 H3 (2026-09-28 拍板)

冻结口径:
  V2 选股(v2.3 配置, 与 runner.py 同逻辑):
    universe : circ_mv<30亿(300000万) & pe_ttm>0 & pb>0 & 非ST & 非停牌(S/R/R&S)
    质量排雷 : bps>0 & roe>0 & profit_dedt>0 (fina_indicator, PIT: ann_date<=T)
    退市排雷 : 主板 total_mv<7.5亿 / 创业科创<4.5亿 剔除; 距退市日<=30天剔除; 北交所豁免红线
    评分     : 纯行业中性 BP z-score (z_weight=1.0, hp 已去除; hp 缺失回退=纯 z, 与 V2a 等价)
    持仓     : 评分 top 80, 等权
  目标: eK(T) = top80 逐股 fwdK 对数均值 - 小盘池基线(P17 口径)逐股 fwdK 对数均值
  主判据(冻结): OOS(2024-01-01~2026-09-11), phase_is 退潮组(climax+ebb) vs 基准组(repair),
    非重叠抽样(间隔>=20 交易日位), 单边 Welch t (方向: 退潮组更低);
    门限 p<0.10 且 (mean_repair - mean_retreat) >= 0.005 (-0.5pp/20日) -> H3 通过
  描述性: fwd5(间隔>=5), 全期, phase_prod 口径, 逐阶段表
局限(预注册第八节披露): 每日重算 top80, 未含 buffer/持有期状态机, 检验 alpha 的
  phase 稳定性而非实盘净值复刻。
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_10_价值小盘V2")

import factors.market_phase  as _mp_unused  # noqa: F401,E402  (确保 factors 包可导入)
from strategy.scoring import V2Scorer      # noqa: E402

ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
ASTOCK_BASIC = "D:/astock/basic/stock_basic.parquet"
ASTOCK_FIN = "D:/astock/finance/fina_indicator.parquet"
B1_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_phase_b_route.csv")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- 冻结参数 ----
CAL_START = "2020-08-24"
OOS_START = "2024-01-01"
OOS_END = "2026-09-11"
N_STOCKS = 80
MV_MAX = 300000.0          # 万元 (30亿)
DELIST_MV_MAIN = 75000.0
DELIST_MV_GEMSTAR = 45000.0
DELIST_NEAR_DAYS = 30
H3_P = 0.10
H3_DIFF = 0.005
GAP20, GAP5 = 20, 5


def log(msg):
    print(msg, flush=True)


def nonoverlap(s, cal_pos, gap):
    picked, last = [], -10 ** 9
    for d, v in s.sort_index().items():
        if pd.isna(v):
            continue
        p = cal_pos[d]
        if p - last >= gap:
            picked.append(float(v))
            last = p
    return np.asarray(picked, dtype=float)


def main():
    t0 = time.time()
    res = {}

    # ---------- 1. b1 标签 + 基线 ----------
    log("[1] 读 b1 标签 ...")
    b1 = pd.read_csv(B1_CSV, index_col=0, parse_dates=True)
    b1.index.name = "trade_date"

    # ---------- 2. 面板 ----------
    log("[2] 读面板(8 列) ...")
    daily = pd.read_parquet(ASTOCK_DAILY, columns=[
        "pe_ttm", "pb", "circ_mv", "total_mv", "is_st", "suspend_type",
        "pct_chg", "listed_days"])
    daily = daily.reset_index()
    daily["ts_code"] = daily["ts_code"].astype("category")
    d_min = pd.Timestamp(CAL_START)
    daily = daily[daily["trade_date"] >= d_min - pd.Timedelta(days=40)]
    log("    rows=%d, %s ~ %s" % (len(daily), daily["trade_date"].min().date(),
                                  daily["trade_date"].max().date()))

    susp = daily["suspend_type"].fillna("N")
    ok = (~daily["is_st"].astype(bool)) & (~susp.isin(["S", "R", "R&S"]))

    # 涨跌/前向收益宽表(全股票)
    log("[3] 全市场对数收益宽表 ...")
    rt = pd.DataFrame({"trade_date": daily["trade_date"].values,
                       "ts_code": daily["ts_code"].astype(str).values,
                       "r": np.log1p(daily["pct_chg"].astype(float).values / 100.0)})
    wide_r = rt.pivot_table(index="trade_date", columns="ts_code", values="r", aggfunc="first")
    wide_r = wide_r.sort_index()
    del rt
    # 小盘池基线(P17 口径)
    pool_mask = ((daily["circ_mv"] < 500000) & (daily["is_st"] != 1)
                 & (daily["listed_days"].fillna(1e9) >= 60))
    pm_wide = pd.DataFrame({"trade_date": daily["trade_date"].values,
                            "ts_code": daily["ts_code"].astype(str).values,
                            "m": pool_mask.values})
    pool_wide = pm_wide.pivot_table(index="trade_date", columns="ts_code", values="m",
                                    aggfunc="first").sort_index()
    pool_wide = pool_wide.reindex(index=wide_r.index, columns=wide_r.columns).fillna(False)
    del pm_wide

    def fwd_log(K):
        acc = None
        for k in range(1, K + 1):
            sk = wide_r.shift(-k)
            acc = sk if acc is None else acc.add(sk)
        return acc

    fw5 = fwd_log(5)
    fw20 = fwd_log(20)
    base5 = fw5.where(pool_wide).mean(axis=1)
    base20 = fw20.where(pool_wide).mean(axis=1)
    log("    基线就绪 took %.0fs" % (time.time() - t0))

    # ---------- 4. 行业/退市映射 ----------
    basic = pd.read_parquet(ASTOCK_BASIC)
    ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))
    delist_map = {}
    if "delist_date" in basic.columns:
        for c, v in zip(basic["ts_code"], basic["delist_date"]):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            s = str(v).strip().split(" ")[0]
            if s in ("", "nan", "None", "NaT"):
                continue
            try:
                delist_map[c] = pd.to_datetime(s, format="%Y%m%d")
            except Exception:
                pass
    log("[4] 行业 %d 只 / 退市日期 %d 只" % (len(ind_map), len(delist_map)))

    # ---------- 5. 财务质量事件流(ann_date 排序, 逐日推进) ----------
    log("[5] 财务质量事件流 ...")
    fin = pd.read_parquet(ASTOCK_FIN,
                          columns=["ts_code", "ann_date", "bps", "roe", "profit_dedt"])
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin["qual"] = (fin["bps"] > 0) & (fin["roe"] > 0) & (fin["profit_dedt"] > 0)
    fin = fin.sort_values("ann_date")
    fin_events = [(d, dict(zip(g["ts_code"].astype(str), g["qual"])))
                  for d, g in fin.groupby("ann_date")]
    event_dates = np.array([e[0] for e in fin_events])
    log("    财务事件日 %d 个 (%s ~ %s)" % (len(fin_events),
                                           event_dates.min().date(), event_dates.max().date()))

    # ---------- 6. 评分器 ----------
    scorer = V2Scorer(ind_map=ind_map, z_weight=1.0, hp_weight=0.0,
                      hp_window=36, hp_min=12, method="bp")

    # ---------- 7. 逐日候选 + 超额 ----------
    log("[6] 逐日 V2 top%d 候选超额 ..." % N_STOCKS)
    trade_days = [d for d in wide_r.index if d >= d_min]
    panel_by_day = {}
    for col in ("pe_ttm", "pb", "circ_mv", "total_mv"):
        w = daily.pivot_table(index="trade_date", columns="ts_code", values=col,
                              aggfunc="first")
        panel_by_day[col] = w.reindex(index=wide_r.index, columns=wide_r.columns)
    ok_wide = pd.DataFrame({"trade_date": daily["trade_date"].values,
                            "ts_code": daily["ts_code"].astype(str).values,
                            "v": ok.values}).pivot_table(
        index="trade_date", columns="ts_code", values="v", aggfunc="first")
    ok_wide = ok_wide.reindex(index=wide_r.index, columns=wide_r.columns).fillna(False)

    # 退市红线: per-code 阈值(主板/创业科创), 北交所豁免
    codes = wide_r.columns
    thr_map = {}
    for c in codes:
        cs = str(c)
        if cs.endswith(".BJ"):
            thr_map[c] = np.inf
        elif cs.startswith("30") or cs.startswith("688"):
            thr_map[c] = DELIST_MV_GEMSTAR
        else:
            thr_map[c] = DELIST_MV_MAIN
    thr_arr = pd.Series([thr_map.get(c, np.inf) for c in codes], index=codes)
    delist_codes = list(delist_map.keys())

    qual = {}
    ev_ptr = 0
    n_ev = len(fin_events)
    rows = []
    for i, d in enumerate(trade_days):
        while ev_ptr < n_ev and event_dates[ev_ptr] <= d:
            qual.update(fin_events[ev_ptr][1])
            ev_ptr += 1
        pe = panel_by_day["pe_ttm"].loc[d]
        pb = panel_by_day["pb"].loc[d]
        cmv = panel_by_day["circ_mv"].loc[d]
        tmv = panel_by_day["total_mv"].loc[d]
        # 退市红线: total_mv < 红线 -> 剔除 (runner._delist_hit 同语义);
        # NaN/<=0 视为通过(runner 同口径); 北交所 thr=inf 恒通过
        tmv_ok = ~(tmv < thr_arr)
        m = (ok_wide.loc[d] & (cmv > 0) & (cmv < MV_MAX)
             & (pe > 0) & (pb > 0) & tmv_ok)
        cand = [c for c in m.index[m.values] if qual.get(c, False)]
        # 退市排雷
        keep = []
        for c in cand:
            dd = delist_map.get(c)
            if dd is not None and d >= dd - pd.Timedelta(days=DELIST_NEAR_DAYS):
                continue
            keep.append(c)
        cand = keep
        if not cand:
            rows.append({"trade_date": d, "e5": np.nan, "e20": np.nan, "n_cand": 0})
            continue
        score = scorer.score(d, cand, pb.loc[cand]).dropna()
        if len(score) == 0:
            rows.append({"trade_date": d, "e5": np.nan, "e20": np.nan, "n_cand": len(cand)})
            continue
        top = list(score.sort_values(ascending=False).index[:N_STOCKS])
        e5 = float(fw5.loc[d, top].mean() - base5.loc[d])
        e20 = float(fw20.loc[d, top].mean() - base20.loc[d])
        rows.append({"trade_date": d, "e5": e5, "e20": e20,
                     "n_cand": len(cand), "n_top": len(top)})
        if (i + 1) % 200 == 0:
            log("    %d/%d (%s) took %.0fs" % (i + 1, len(trade_days), d.date(),
                                               time.time() - t0))
        scorer.clear_cache()

    edf = pd.DataFrame(rows).set_index("trade_date").sort_index()
    out_csv = os.path.join(OUT_DIR, "h3_daily_excess.csv")
    edf.to_csv(out_csv, encoding="utf-8-sig")
    log("    已保存 %s (rows=%d, took %.0fs)" % (out_csv, len(edf), time.time() - t0))

    # ---------- 8. 分层检验 ----------
    log("")
    log("========== H3 V2 语境调节(主判据 2) ==========")
    df = edf.join(b1[["phase_is", "phase_prod"]])
    cal_pos = pd.Series(np.arange(len(wide_r.index)), index=wide_r.index)

    def group_test(sub, phase_col, lo, hi, gap, label, fwd_col="e20"):
        seg = sub.loc[lo:hi]
        ret = seg[seg[phase_col].isin(["climax", "ebb"])][fwd_col]
        rep = seg[seg[phase_col] == "repair"][fwd_col]
        xr = nonoverlap(ret, cal_pos, gap)
        xp = nonoverlap(rep, cal_pos, gap)
        if len(xr) < 2 or len(xp) < 2:
            log("  [%s] 样本不足: 退潮 %d 点 / repair %d 点" % (label, len(xr), len(xp)))
            return None
        t, p = stats.ttest_ind(xr, xp, alternative="less", equal_var=False)
        diff = float(xp.mean() - xr.mean())
        log("  [%s] 退潮组 %d 点 mean=%+.4f ≈ %+.2f%% | repair %d 点 mean=%+.4f ≈ %+.2f%%"
            % (label, len(xr), xr.mean(), np.expm1(xr.mean()) * 100,
               len(xp), xp.mean(), np.expm1(xp.mean()) * 100))
        log("        差(repair-退潮)=%+.4f ≈ %+.2fpp | Welch 单边 t=%.3f p=%.4f"
            % (diff, diff * 100, t, p))
        return {"n_retreat": int(len(xr)), "n_repair": int(len(xp)),
                "mean_retreat": float(xr.mean()), "mean_repair": float(xp.mean()),
                "diff": diff, "t": float(t), "p": float(p)}

    gate = group_test(df, "phase_is", OOS_START, OOS_END, GAP20, "主判据 OOS e20 phase_is")
    h3_pass = bool(gate is not None and gate["p"] < H3_P and gate["diff"] >= H3_DIFF)
    if gate is not None:
        log("  冻结门限: p<%.2f 且 差>=%.3f -> %s" % (
            H3_P, H3_DIFF, "PASS" if h3_pass else "FAIL"))
    res["h3_gate"] = gate
    res["h3_pass"] = h3_pass

    log("")
    log("---------- 描述性披露(不构成判据) ----------")
    res["desc"] = {}
    for label, (col, lo, hi, gap, fc) in {
        "全期 e20 phase_is": ("phase_is", CAL_START, None, GAP20, "e20"),
        "OOS e20 phase_prod": ("phase_prod", OOS_START, OOS_END, GAP20, "e20"),
        "OOS e5 phase_is(gap5)": ("phase_is", OOS_START, OOS_END, GAP5, "e5"),
        "全期 e5 phase_is(gap5)": ("phase_is", CAL_START, None, GAP5, "e5"),
    }.items():
        hi = hi or df.index.max()
        r = group_test(df, col, lo, hi, gap, label, fc)
        if r:
            res["desc"][label] = r
    # 逐阶段均值表
    for col in ("phase_is", "phase_prod"):
        tab = df.loc[OOS_START:OOS_END].groupby(col)[["e5", "e20"]].agg(["count", "mean"])
        log("  OOS 逐阶段超额(%s):" % col)
        for ph, row in tab.iterrows():
            log("    %-8s n=%-4d e5=%+.4f e20=%+.4f" % (
                ph, int(row[("e5", "count")]),
                row[("e5", "mean")], row[("e20", "mean")]))
        res["desc"]["stage_%s" % col] = {
            str(k): [float(row[("e5", "mean")]), float(row[("e20", "mean")])]
            for k, row in tab.iterrows()}
    # 缺失披露
    nan_days = int(df["e20"].isna().sum())
    log("  候选缺失日: %d / %d" % (nan_days, len(df)))
    res["desc"]["nan_days"] = nan_days
    res["desc"]["total_days"] = int(len(df))

    with open(os.path.join(OUT_DIR, "b2_results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    log("")
    log("========== b2 完成 (took %.0fs) H3 判定: %s ==========" % (
        time.time() - t0, "PASS" if h3_pass else "FAIL"))


if __name__ == "__main__":
    main()
