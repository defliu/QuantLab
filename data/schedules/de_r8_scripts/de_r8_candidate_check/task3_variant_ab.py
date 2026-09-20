# coding: utf-8
"""任务3：量化「红线后 prob top10（变体A=DE现口径） vs 红线后 total top10（变体B=实盘口径）」
在 516 天主窗口（2024-07-01 ~ 2026-08-14）的差异。

引擎：de_r8_common（G2 口径：enh2_n3_bt 面板 + 1522t 模型 + 真实评分卡 + 红线60
+ TOP2(K=2) + live_trail(引擎内置 STOP-7/TP15/TRAIL 8+8) + N=15）。
两变体共用同一 passed 池，仅 top10 选取键不同；买入侧同按 total 降序补足 K=2。
只读数据/模型/源码；中间产物写本目录。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
R8DIR = os.path.dirname(HERE)
PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
for p in (R8DIR, PROJ):
    if p not in sys.path:
        sys.path.insert(0, p)

import de_r8_common as R8       # noqa: E402
import scorecard_real as SR     # noqa: E402

strat = R8.STRATS["G2"]
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260915.json")
PANEL = strat["panel"]
REDLINE = strat["redline"]          # 60.0
K, N = 2, 15                        # TOP2 持仓（任务书）+ G2 持有期 N=15
START, END = R8.MAIN_START, R8.MAIN_END
TOP_LIST, POOL_N = R8.TOP_LIST, R8.POOL_N


def build_pools_ab(ctx):
    """逐日：top100(prob) → 真实评分卡 → 红线 → 变体A top10 by prob / 变体B top10 by total。
    同时统计历史口径（全市场 top10 by prob 过线数）与 passed 池大小。"""
    meta = json.load(open(META, encoding="utf-8"))
    feats = meta["feature_cols"]
    need = list(dict.fromkeys(feats + ["main_net", "industry_pct", "volume_ratio",
                                       "ts_code", "trade_date", "fwd_ret"]))
    panel = pd.read_parquet(PANEL, columns=need)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel[(panel["trade_date"] >= pd.Timestamp(START))
                  & (panel["trade_date"] <= pd.Timestamp(END))]
    booster = lgb.Booster(model_file=MODEL)
    per_a, per_b, dates = {}, {}, []
    bench = {}
    n_pass_list, n_hist_pass_list = [], []
    grouped = panel.groupby("trade_date", sort=True)
    n_all = len(grouped)
    for gi, (d, day) in enumerate(grouped):
        if len(day) < 20:
            continue
        dates.append(d)
        bench[d] = float(day["fwd_ret"].mean())
        day = day.copy()
        day["prob"] = booster.predict(day[feats].astype("float32").values)
        pool = day.nlargest(POOL_N, "prob").copy()
        idx = pd.MultiIndex.from_arrays([pool["trade_date"], pool["ts_code"].astype(str)])
        est = ctx["est"].reindex(idx)
        pool["total"] = SR.compute_real_scorecard(pool, est)["total_new_real"].values
        passed = pool[pool["total"] >= REDLINE]
        n_pass_list.append(len(passed))
        # 历史口径：全市场 prob top10 中过红线数（pool 的 top10 by prob = 全市场 top10 by prob）
        hist10 = pool.nlargest(TOP_LIST, "prob")
        n_hist_pass_list.append(int((hist10["total"] >= REDLINE).sum()))

        def frame(top):
            out = pd.DataFrame(index=pd.Index([], name="ts_code"),
                               columns=["total", "rank_score", "open_next", "up_limit_next",
                                        "vol_next", "suspend_next"])
            if len(top):
                top = top.copy()
                codes = top["ts_code"].astype(str).values
                idx2 = pd.MultiIndex.from_arrays([top["trade_date"], codes])
                nx = ctx["nxt"].reindex(idx2)
                out = pd.DataFrame({
                    "total": top["total"].values,
                    "rank_score": top["prob"].values,
                    "open_next": nx["open_next"].values,
                    "up_limit_next": nx["up_limit_next"].values,
                    "vol_next": nx["vol_next"].values,
                    "suspend_next": nx["suspend_next"].values,
                }, index=pd.Index(codes, name="ts_code"))
                out = out.sort_values("total", ascending=False)
            return out

        per_a[d] = frame(passed.nlargest(TOP_LIST, "prob") if len(passed) else passed)
        per_b[d] = frame(passed.nlargest(TOP_LIST, "total") if len(passed) else passed)
        if gi % 100 == 0:
            print("    pools %d/%d %s ..." % (gi, n_all, d.date()))
    return (dates, per_a, per_b, pd.Series(bench),
            np.array(n_pass_list), np.array(n_hist_pass_list))


print("[1/4] build_daily ctx ...")
uni = set(pd.read_parquet(PANEL, columns=["ts_code"])["ts_code"].astype(str).unique().tolist())
ctx = R8.build_daily(uni, START, END)

print("[2/4] build_pools A/B（%s ~ %s）..." % (START, END))
dates, per_a, per_b, bench, n_pass_arr, n_hist_arr = build_pools_ab(ctx)
M = len(dates)
print("    %d 个交易日 | passed 日均 %.2f 只 | 历史口径 top10 过线日均 %.2f 只"
      % (M, n_pass_arr.mean(), n_hist_arr.mean()))

print("[3/4] simulate 变体A（DE：红线后 prob top10）...")
buy_a, buy_b = [], []
tr_a, ret_a, info_a = R8.simulate(dates, per_a, K, N, ctx, buy_pairs_out=buy_a)
print("[4/4] simulate 变体B（实盘：红线后 total top10）...")
tr_b, ret_b, info_b = R8.simulate(dates, per_b, K, N, ctx, buy_pairs_out=buy_b)


def perf(trades, daily_ret, bench):
    nav = (1 + daily_ret).cumprod()
    years = len(daily_ret) / 252.0
    out = {
        "n_trades": len(trades),
        "abs_daily": float(daily_ret.mean()),
        "annual": float(nav.iloc[-1] ** (1.0 / years) - 1),
        "total_return": float(nav.iloc[-1] - 1),
        "mdd": float((nav / nav.cummax() - 1).min()),
        "sharpe": float(daily_ret.mean() / daily_ret.std() * math.sqrt(252)) if daily_ret.std() > 0 else np.nan,
        "bench_daily": float(bench.mean()),
        "excess_daily": float(daily_ret.mean() - bench.mean()),
        "win_rate": float(np.mean([t[2] > 0 for t in trades])) if trades else np.nan,
        "avg_trade_ret": float(np.mean([t[2] for t in trades])) if trades else np.nan,
    }
    return out


pa, pb = perf(tr_a, ret_a, bench), perf(tr_b, ret_b, bench)

# ---- 差异统计 ----
cand_diff_days, buy2_diff_days, buy2_a, buy2_b = [], [], {}, {}
for d in dates:
    a, b = per_a[d], per_b[d]
    sa, sb = set(a.index), set(b.index)
    if sa != sb:
        cand_diff_days.append(d)

    def first2_exec(f):
        out = []
        for code in f.index:
            if len(out) >= K:
                break
            if R8._base_exec(f.loc[code]):
                out.append(code)
        return tuple(out)

    wa, wb = first2_exec(a), first2_exec(b)
    buy2_a[d], buy2_b[d] = wa, wb
    if wa != wb:
        buy2_diff_days.append(d)

# 实际成交对比（buy pairs 按 (code, buy_date)）
set_a = set(buy_a)
set_b = set(buy_b)
both = set_a & set_b
only_a, only_b = set_a - set_b, set_b - set_a
# 深挖比例：B 买入中 rank_score（prob）在 passed 池内 prob 排名 >10 的比例——用池内排名近似
# （此处直接用「A 候选不含该 code」近似 prob>10 名，供参考）
ret_diff = (ret_a.values - ret_b.values)
big = np.argsort(-np.abs(ret_diff))[:10]

print("\n===== 汇总 =====")
summary = {
    "window": "%s~%s" % (START, END), "n_days": M, "K": K, "N": N, "redline": REDLINE,
    "avg_passed_pool": float(n_pass_arr.mean()),
    "avg_hist_top10_passed": float(n_hist_arr.mean()),
    "cand_diff_days": len(cand_diff_days),
    "would_buy2_diff_days": len(buy2_diff_days),
    "variant_A_DE_prob_top10": pa,
    "variant_B_live_total_top10": pb,
    "buy_pairs": {"both": len(both), "only_A": len(only_a), "only_B": len(only_b),
                  "n_A": len(set_a), "n_B": len(set_b)},
    "reasons_A": info_a["reasons"], "reasons_B": info_b["reasons"],
}
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

# ---- 典型分歧日（信号侧 would-buy Top2 不同 且 |日收益差| 最大）----
rows = []
for i, d in enumerate(dates):
    if buy2_a[d] != buy2_b[d]:
        rows.append({"date": d, "ret_A": float(ret_a.iloc[i]), "ret_B": float(ret_b.iloc[i]),
                     "diff_pp": float((ret_a.iloc[i] - ret_b.iloc[i]) * 100),
                     "buy_A": list(buy2_a[d]), "buy_B": list(buy2_b[d])})
df_div = pd.DataFrame(rows).sort_values("diff_pp", key=lambda s: -s.abs())
df_div.to_csv(os.path.join(HERE, "task3_divergence_days.csv"), index=False, encoding="utf-8-sig")
print("\n===== 分歧日 Top10（|A-B| 日收益差最大）=====")
print(df_div.head(10).to_string(index=False))

# 逐日候选落盘（供报告引用与复核）
pd.DataFrame({"date": dates, "passed_pool": n_pass_arr, "hist_top10_passed": n_hist_arr,
              "buy2_A": [",".join(buy2_a[d]) for d in dates],
              "buy2_B": [",".join(buy2_b[d]) for d in dates],
              "ret_A": ret_a.values, "ret_B": ret_b.values}
             ).to_csv(os.path.join(HERE, "task3_daily.csv"), index=False, encoding="utf-8-sig")

with open(os.path.join(HERE, "task3_summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
print("\n[done] 中间产物已写 %s" % HERE)
