# -*- coding: utf-8 -*-
"""XVAL5 工作包 C —— 红线作用池差异 → 部署结果偏差量化（A/B 反事实）。

口径 A（引擎现状，scan_rotate_cost_real.py:221-223）：
    Top100(by prob) → Top10(by prob) → 红线过滤 → 持仓（红线作用池 = Top10）
口径 B（部署现状，deploy_predict_g2.py:178-297）：
    Top100(by prob) → 红线过滤（位置过滤默认关，PF_MODE_G2 未设）→ total Top10 → 持仓
    （红线作用池 = Top100；rebalance 从池中按 total_new 降序买入，simulate 同款 best-total-first）

两口径锁定（任务书 §5.2 逐项）：同一模型 / 同一面板与 F3/F6 数据源（评分输入全部来自
同一张 top100 表）/ 同一 516 日 / 同一持有期与退出（N=10, STOP-7%, TP+15%, fixed）/
同一持仓上限 TOP=2 / 同一成本（佣金万2最低5元+印花+过户+滑点0.1%）/ 同一初始资金 10 万。
唯一差异 = 红线作用池（Top10 vs Top100）+ 由此带来的候选排序来源。

模型：主口径 0915（任务书指定，lgb_model_v3_g2_strong_real_20260915_1522t.txt）；
      副口径 0825（复用 xval_de_bt_cache.parquet，稳健性对照，亦是引擎历史锚点模型）。
市场基准：按日期 join（修正 align_market 缺陷后的口径）。
"""
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import data_config as DC          # noqa: E402
import xval_de_p345 as P345       # noqa: E402
import xval_de_scorecard as SCV   # noqa: E402

MODEL_0915 = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META_0915 = os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260915.json")
CACHE_0915 = os.path.join(HERE, "xval5_c_top100_0915.parquet")
CACHE_0825 = P345.CACHE
CACHE_MKT = P345.CACHE_MKT
OUT_JSON = os.path.join(HERE, "xval5_c_results_20260929.json")

THR_GRID = (50, 52, 55, 58, 60, 62)
THR_MAIN = 60.0
SIM_COLS = ["total_new", "fwd_ret", "prob", "open_next", "up_limit_next", "vol_next", "suspend_next"]


# ================================================================ build 0915
def build_top100_0915():
    """与 xval_de_p345.build() 同构，仅换 0915 模型/特征，并额外保留 SC_F5/prob_rank。"""
    t0 = time.time()
    feat_cols = json.load(open(META_0915, encoding="utf-8"))["feature_cols"]
    cols = ["trade_date", "ts_code", "main_net", "industry_pct", "fwd_ret",
            "pos_250", "volume_ratio", "fc_days_since", "ex_days_since",
            "fc_pchange", "dv_year_sum", "rsi6", "macd_hist"] + feat_cols
    cols = list(dict.fromkeys(cols))
    print("[build0915] 读面板（in-range）...", flush=True)
    panel = pd.read_parquet(P345.PANEL, columns=cols)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    panel = panel[(panel["trade_date"] >= P345.START) & (panel["trade_date"] <= P345.END)].copy()
    print(f"    in-range rows={len(panel):,}  ({time.time()-t0:.0f}s)", flush=True)

    import lightgbm as lgb
    booster = lgb.Booster(model_file=MODEL_0915)
    print("[build0915] 模型推理 one-shot ...", flush=True)
    panel["prob"] = booster.predict(panel[feat_cols].astype("float32").values)
    print(f"    done ({time.time()-t0:.0f}s)", flush=True)

    panel = panel.sort_values(["trade_date", "prob"], ascending=[True, False], kind="stable")
    top100 = panel.groupby("trade_date", sort=True).head(100).copy()
    top100["prob_rank"] = top100.groupby("trade_date")["prob"].rank(ascending=False, method="first")

    print("[build0915] 读 MAIN_DAILY（可执行 + F6 est）...", flush=True)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=[
        "open", "high", "up_limit", "down_limit", "vol", "suspend_timing",
        "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["ts_code"].isin(set(panel["ts_code"].unique()))]
    daily = daily[daily["trade_date"] >= P345.START - pd.Timedelta(days=45)]
    daily = daily.sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1).set_index(["trade_date", "ts_code"])

    idx = pd.MultiIndex.from_arrays([top100["trade_date"], top100["ts_code"]])
    est = daily.reindex(idx)[["pe_ttm", "turnover_rate", "open_next", "up_limit_next",
                              "vol_next", "suspend_next"]]
    for c in ("open_next", "up_limit_next", "vol_next", "suspend_next"):
        top100[c] = est[c].values
    pe, tu = est["pe_ttm"].values, est["turnover_rate"].values

    f1 = SCV.score_f1_vec(top100["pos_250"].values)
    f2 = SCV.score_f2_vec(top100["main_net"].values, top100["volume_ratio"].values)
    f3 = SCV.score_f3_vec(top100["fc_days_since"].values, top100["ex_days_since"].values,
                          top100["fc_pchange"].values, top100["dv_year_sum"].values)
    f4 = SCV.score_f4_vec(top100["rsi6"].values, top100["macd_hist"].values)
    f5 = SCV.score_f5_vec(top100["industry_pct"].values)
    f6 = SCV.score_f6_vec(pe, tu)
    top100["total_abs"] = SCV.total_new_vec(f1, f2, f3, f4, f5, f6)
    top100["SC_F5"] = f5

    keep = ["trade_date", "ts_code", "prob", "prob_rank", "fwd_ret", "total_abs", "SC_F5",
            "industry_pct", "open_next", "up_limit_next", "vol_next", "suspend_next"]
    top100[keep].to_parquet(CACHE_0915, index=False)
    print(f"[build0915] -> {CACHE_0915}  ({time.time()-t0:.0f}s)", flush=True)
    return top100[keep]


def load_table(model_tag):
    if model_tag == "0915":
        if not os.path.exists(CACHE_0915):
            return build_top100_0915()
        df = pd.read_parquet(CACHE_0915)
    else:
        df = pd.read_parquet(CACHE_0825)
        df["prob_rank"] = df.sort_values(["trade_date", "prob"], ascending=[True, False],
                                         kind="stable").groupby("trade_date")["prob"] \
                            .rank(ascending=False, method="first")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    return df


# ================================================================ pools
def pool_a(df):
    """口径 A：Top10 by prob（= P345.make_per_day，红线在 simulate 内作用于该池）。"""
    return P345.make_per_day(df, "total_abs")


def pool_b(df, thr):
    """口径 B：Top100 by prob → 红线(total>=thr) → top10 by total（deploy L278/297 同序）。"""
    per_day = {}
    for d, g in df.groupby("trade_date", sort=True):
        pas = g[g["total_abs"] >= thr]
        b = pas.nlargest(10, "total_abs")
        gg = b.set_index("ts_code").rename(columns={"total_abs": "total_new"})
        per_day[pd.Timestamp(d)] = gg[SIM_COLS]
    return per_day


# ================================================================ simulate with codes
def simulate_c(dates, per_day, threshold, n=P345.N_HOLD, slip=P345.SLIP,
               open_map=None, down_map=None, top=P345.TOP):
    """P345.simulate 的逐行同构拷贝，唯一增强 = trades 记录 ts_code（用于成交票重合率）。"""
    M = len(dates)
    cash = 100000.0
    hold = {}
    trades, daily_ret = [], []
    prev_total = 100000.0
    n_skip = n_skip_down = n_delist = 0
    for i, d in enumerate(dates):
        row = per_day[d]
        if i > 0:
            for c in list(hold):
                h = hold[c]
                o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < M else None
                o_cur = open_map.get((d, c))
                if o_buy and o_cur and o_buy > 0:
                    h["value"] = h["invest"] * o_cur / o_buy
        for c in list(hold):
            h = hold[c]
            o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < M else None
            o_cur = open_map.get((d, c))
            if not (o_buy and o_cur and o_buy > 0):
                h["missing_days"] = h.get("missing_days", 0) + 1
                if h["missing_days"] >= P345.DELIST_DAYS:
                    amt = h["value"]
                    cash += amt * (1 - P345.DELIST_LOSS) - P345.sell_fee(amt, c, slip)
                    trades.append((h["buy_i"], i, amt * (1 - P345.DELIST_LOSS) / h["buy_val"] - 1, c))
                    del hold[c]
                    n_delist += 1
                continue
            h["missing_days"] = 0
            ret = o_cur / o_buy - 1
            down = down_map.get((d, c)) if down_map is not None else None
            one_word_down = down is not None and not (isinstance(down, float) and np.isnan(down)) \
                and o_cur <= down + 1e-9
            if one_word_down:
                n_skip_down += 1
                continue
            if (i - h["buy_i"]) >= n + 1:
                sell = True
            elif (i - h["buy_i"]) < 2:
                sell = False
            else:
                sell = (ret <= P345.STOP or ret >= P345.TP)
            if sell:
                amt = h["value"]
                cash += amt - P345.sell_fee(amt, c, slip)
                trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1, c))
                del hold[c]
        while len(hold) < top:
            cand = row[~row.index.isin(hold)]
            cand = cand[cand["total_new"] >= threshold]
            if len(cand) == 0:
                break
            ok = ~(cand["vol_next"].isna() | (cand["vol_next"] <= 0)
                   | cand["suspend_next"].notna()
                   | (cand["up_limit_next"].notna() & cand["open_next"].notna()
                      & (cand["open_next"] >= cand["up_limit_next"])))
            n_skip += int((~ok).sum())
            cand = cand[ok]
            if len(cand) == 0:
                break
            best = cand.nlargest(1, "total_new").index[0]
            k = top - len(hold)
            budget = cash * 0.95 / k
            fee = P345.buy_fee(budget, best, slip)
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1, c))
    return trades, np.array(daily_ret), n_skip, n_skip_down, n_delist


# ================================================================ candidate layer
def exec_ok(g):
    return ~(g["vol_next"].isna() | (g["vol_next"] <= 0)
             | g["suspend_next"].notna()
             | (g["up_limit_next"].notna() & g["open_next"].notna()
                & (g["open_next"] >= g["up_limit_next"])))


def candidate_layer(df, dates, thr):
    """任务书 §5.3 候选层：日均候选数 / 重合率 / 引擎漏票性质。"""
    by_day = {pd.Timestamp(d): g for d, g in df.groupby("trade_date", sort=True)}
    rows = []
    missed = []
    for d in dates:
        g = by_day[d]
        top10 = g.nlargest(10, "prob", keep="first")
        a_raw = g[g["total_abs"] >= thr]
        a_cand = top10[top10["total_abs"] >= thr]
        a_cand_x = a_cand[exec_ok(a_cand)]
        b_cand = a_raw.nlargest(10, "total_abs")
        b_cand_x = b_cand[exec_ok(b_cand)]
        picks_a = a_cand_x.nlargest(2, "total_abs").index
        picks_b = b_cand_x.nlargest(2, "total_abs").index
        inter = len(set(picks_a) & set(picks_b))
        uni = len(set(picks_a) | set(picks_b))
        rows.append(dict(
            date=d, a_pass_raw=len(a_cand), b_pass_raw=len(a_raw),
            a_cand=len(a_cand_x), b_cand=len(b_cand_x),
            pick2_inter=inter, pick2_union=uni,
            b_only_days=int(len(b_cand_x) > 0 and len(a_cand_x) == 0)))
        miss = b_cand_x.loc[~b_cand_x.index.isin(set(picks_a) | set(a_cand_x.index))]
        for code, r in miss.iterrows():
            missed.append(dict(date=d, ts_code=code, prob=float(r["prob"]),
                               prob_rank=float(r["prob_rank"]) if "prob_rank" in r else np.nan,
                               total=float(r["total_abs"]),
                               SC_F5=float(r["SC_F5"]) if "SC_F5" in r else np.nan))
    c = pd.DataFrame(rows)
    m = pd.DataFrame(missed)
    jacc = (c["pick2_inter"] / c["pick2_union"].clip(lower=1)).mean()
    return dict(
        a_pass_daily_mean=float(c["a_pass_raw"].mean()),
        b_pass_daily_mean=float(c["b_pass_raw"].mean()),
        a_cand_daily_mean=float(c["a_cand"].mean()),
        b_cand_daily_mean=float(c["b_cand"].mean()),
        pick2_overlap_daily=float(c["pick2_inter"].mean()),
        pick2_jaccard_mean=float(jacc),
        days_b_only=int(c["b_only_days"].sum()),
        n_days=len(dates),
        n_missed=len(m),
        missed_prob_mean=float(m["prob"].mean()) if len(m) else np.nan,
        missed_prob_median=float(m["prob"].median()) if len(m) else np.nan,
        missed_prob_rank_median=float(m["prob_rank"].median()) if len(m) else np.nan,
        missed_total_mean=float(m["total"].mean()) if len(m) else np.nan,
        missed_f5_mean=float(m["SC_F5"].mean()) if len(m) and m["SC_F5"].notna().any() else np.nan,
        missed_f5_dist={str(k): int(v) for k, v in
                        m["SC_F5"].value_counts().sort_index().items()} if len(m) else {},
    )


# ================================================================ result layer
def result_layer(dates, per_day, thr, mkt_by_date, open_map, down_map):
    tr, dr, *_ = simulate_c(dates, per_day, thr, open_map=open_map, down_map=down_map)
    mkt_seg = np.array([mkt_by_date[d] for d in dates])
    ex_d = dr - mkt_seg
    nav = float(np.prod(1 + dr))
    years = len(dates) / 252.0
    rets = np.array([t[2] for t in tr])
    holds = np.array([t[1] - t[0] for t in tr])
    return dict(
        n_trades=len(tr), daily_ex=float(np.nanmean(ex_d)),
        ann_ex=float(np.nanmean(ex_d)) * 252,
        cagr=float(nav ** (1 / years) - 1) if nav > 0 else float("nan"),
        win_rate=float((rets > 0).mean()) if len(rets) else np.nan,
        trades_per_day=len(tr) / len(dates),
        avg_hold_days=float(holds.mean()) if len(holds) else np.nan,
        mdd=P345.mdd(dr),
        p5_daily=float(np.percentile(dr, 5)), worst_daily=float(dr.min()),
        trade_codes=[(int(t[0]), int(t[1]), float(t[2]), str(t[3])) for t in tr],
        daily_ret=dr.tolist())


def trade_overlap(res_a, res_b, dates):
    """成交票重合率：同日同票 = 重合（买入日+代码对齐）。"""
    a = {(dates[t[0]], t[3]) for t in res_a["trade_codes"]}
    b = {(dates[t[0]], t[3]) for t in res_b["trade_codes"]}
    inter = len(a & b)
    return dict(n_a=len(a), n_b=len(b), inter=inter, union=len(a | b),
                jaccard=float(inter / len(a | b)) if (a | b) else np.nan)


# ================================================================ main
def main():
    t0 = time.time()
    print(f"[xval5_c] run start: {datetime.now():%Y-%m-%d %H:%M:%S}")
    mkt = pd.read_parquet(CACHE_MKT).reset_index()
    mkt["trade_date"] = pd.to_datetime(mkt["trade_date"])
    mkt_by_date = dict(zip(mkt["trade_date"], mkt["mkt"]))

    print("[xval5_c] MAIN_DAILY open/down_limit ...", flush=True)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "down_limit"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)

    results = {}
    for model_tag in ("0915", "0825"):
        df = load_table(model_tag)
        dates = sorted(pd.Timestamp(d) for d in df["trade_date"].unique())
        is_dates = [d for d in dates if d < P345.IS_CUT]
        oos_dates = [d for d in dates if d >= P345.IS_CUT]
        dd = daily[daily["ts_code"].isin(set(df["ts_code"].unique()))]
        dd = dd[dd["trade_date"] >= P345.START - pd.Timedelta(days=45)]
        dd = dd.set_index(["trade_date", "ts_code"])
        open_map = dd["open"].to_dict()
        down_map = dd["down_limit"].to_dict()
        del dd

        print(f"\n===== 模型 {model_tag}：{len(dates)} 日 {dates[0].date()}~{dates[-1].date()} =====")
        pa = pool_a(df)

        # 保真自检：simulate_c vs P345.simulate（A 池 thr60 FULL）
        tr_ref, dr_ref, *_ = P345.simulate(dates, pa, THR_MAIN, P345.N_HOLD, P345.SLIP,
                                           open_map, down_map)
        res_a_full = result_layer(dates, pa, THR_MAIN, mkt_by_date, open_map, down_map)
        assert len(tr_ref) == res_a_full["n_trades"], "simulate_c 笔数与 P345.simulate 不一致"
        assert float(np.max(np.abs(np.asarray(dr_ref)
                                   - np.array(res_a_full["daily_ret"])))) < 1e-12, \
            "simulate_c daily_ret 与 P345.simulate 不一致"
        print(f"[fidelity] simulate_c == P345.simulate PASS（{res_a_full['n_trades']} 笔）"
              f" | 0825 锚点核对：见下表")

        out = {}
        # ---- 候选层 ----
        out["candidate"] = candidate_layer(df, dates, THR_MAIN)
        c = out["candidate"]
        print(f"[候选层 thr60] A(Top10池)过线日均 {c['a_pass_daily_mean']:.2f} | "
              f"B(Top100池)过线日均 {c['b_pass_daily_mean']:.2f}")
        print(f"  可执行后候选 A {c['a_cand_daily_mean']:.2f} / B {c['b_cand_daily_mean']:.2f} | "
              f"最终选中票(pick2)日重合 {c['pick2_overlap_daily']:.2f}/2 | "
              f"Jaccard {c['pick2_jaccard_mean']:.3f}")
        print(f"  B 有候选而 A 无候选天数 {c['days_b_only']}/{c['n_days']} | "
              f"引擎漏票 {c['n_missed']} 只：prob 均值 {c['missed_prob_mean']:.4f} / "
              f"中位 prob_rank {c['missed_prob_rank_median']:.0f} / total 均值 "
              f"{c['missed_total_mean']:.1f} / F5 分布 {c['missed_f5_dist']}")

        # ---- 结果层 ----
        pb = pool_b(df, THR_MAIN)
        res = {}
        for seg_name, seg in (("IS", is_dates), ("OOS", oos_dates), ("FULL", dates)):
            res[f"A_{seg_name}"] = result_layer(seg, pa, THR_MAIN, mkt_by_date,
                                                open_map, down_map)
            res[f"B_{seg_name}"] = result_layer(seg, pb, THR_MAIN, mkt_by_date,
                                                open_map, down_map)
        out["results"] = {k: {kk: vv for kk, vv in v.items()}
                          for k, v in res.items()}
        out["trade_overlap_full"] = trade_overlap(res["A_FULL"], res["B_FULL"], dates)

        print("\n[结果层 thr60] 口径 | 段 | 笔数 | CAGR | 日均超额 | 年化超额 | 胜率 | "
              "笔/日 | 均持有天 | MDD | P5日 | 最差日")
        for seg in ("IS", "OOS", "FULL"):
            for mode in ("A", "B"):
                r = res[f"{mode}_{seg}"]
                print(f"  {mode} | {seg:4s} | {r['n_trades']:4d} | {r['cagr']:+.2%} | "
                      f"{r['daily_ex']:+.4%} | {r['ann_ex']:+.2%} | {r['win_rate']:.3f} | "
                      f"{r['trades_per_day']:.2f} | {r['avg_hold_days']:.1f} | "
                      f"{r['mdd']:.1%} | {r['p5_daily']:+.2%} | {r['worst_daily']:+.2%}")
        ov = out["trade_overlap_full"]
        print(f"  [成交票重合 FULL] A {ov['n_a']} 笔 / B {ov['n_b']} 笔 | 同日同票 "
              f"{ov['inter']} | Jaccard {ov['jaccard']:.3f}")

        # ---- 阈值敏感性（FULL）----
        sens = {}
        print("\n[阈值敏感性 FULL] thr | A笔数 | A日均超额 | A_MDD | B笔数 | B日均超额 | "
              "B_MDD | 超额差(B-A)")
        for thr in THR_GRID:
            pb_t = pool_b(df, float(thr))
            ra = result_layer(dates, pa, float(thr), mkt_by_date, open_map, down_map)
            rb = result_layer(dates, pb_t, float(thr), mkt_by_date, open_map, down_map)
            sens[str(thr)] = dict(
                A=dict(n=ra["n_trades"], ex=ra["daily_ex"], mdd=ra["mdd"], cagr=ra["cagr"]),
                B=dict(n=rb["n_trades"], ex=rb["daily_ex"], mdd=rb["mdd"], cagr=rb["cagr"]))
            print(f"  {thr} | {ra['n_trades']:4d} | {ra['daily_ex']:+.4%} | {ra['mdd']:.1%} | "
                  f"{rb['n_trades']:4d} | {rb['daily_ex']:+.4%} | {rb['mdd']:.1%} | "
                  f"{rb['daily_ex'] - ra['daily_ex']:+.4%}")
        out["sensitivity"] = sens
        results[model_tag] = out
        print(f"\n[{model_tag}] done ({time.time()-t0:.0f}s)", flush=True)

    # ---- 落盘（trade_codes/daily_ret 只保留 FULL，控制体积）----
    def slim(v):
        keep = ("n_trades", "daily_ex", "ann_ex", "cagr", "win_rate", "trades_per_day",
                "avg_hold_days", "mdd", "p5_daily", "worst_daily")
        return {k: (vv.item() if isinstance(vv, np.floating) else vv)
                for k, vv in v.items() if k in keep}

    dump = dict(run_started=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                thr_grid=list(THR_GRID), thr_main=THR_MAIN)
    for tag, out in results.items():
        dump[tag] = dict(
            candidate=out["candidate"],
            results={k: slim(v) for k, v in out["results"].items()},
            trade_overlap_full=out["trade_overlap_full"],
            sensitivity=out["sensitivity"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print(f"\n[xval5_c] results -> {OUT_JSON}  (total {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
