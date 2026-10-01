# -*- coding: utf-8 -*-
"""XVAL JOINT Q1c：锁定 OP 少计的那一只票。

思路：OP 的 B 段打印了其 Top100 池的一批统计量（prob 范围 / est 命中率 / 非空率 /
total max/median/min / industry 均值中位 min/max/p10/p90/负占比 / F5 分布与均值）。
若 OP 的池 = 我的池 - 某票X + 第101名（或 - 某票X 共99只），这些统计量会给出可判别签名。
对 X ∈ {000786.SZ, 920651.BJ, 001332.SZ} 逐一生成签名，与 OP 报告打印值全量对照。
只读。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict_g2 as DG      # 只读复用 _fill_f3_events
import review_full as RF
import xval_de_scorecard as SCV

M0915 = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META0915 = os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260915.json")
CSV = os.path.join(HERE, "xval_de_p1_top100_20260928.csv")

# OP 报告 B/A 段打印值（对照目标）
OP = {
    "prob_min": 0.6406, "prob_max": 0.7042, "est_hit": 0.67,
    "main_net_nn": 1.00, "industry_nn": 1.00, "volume_nn": 0.98,
    "total_max": 61.5, "total_med": 47.5, "total_min": 36.0,
    "ind_mean": -5.26, "ind_med": -5.95, "ind_min": -8.80, "ind_max": 2.10,
    "ind_p10": -8.6, "ind_p90": -2.09, "ind_neg": 0.980,
    "f5_mean": 2.170, "f5_dist": {2.0: 94, 4.0: 4, 5.0: 1, 8.0: 1},
}


def score_rows(rows, daily_db):
    """对快照行（已含 prob）走生产评分路径（F3 fill + est F6），返回 SC 与 total。"""
    pre = rows.copy()
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan
    DG._fill_f3_events(pre, verbose=False)
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily_db.reindex(idx)
    sc = SCV  # 向量化评分（上轮已与生产逐值校验 max diff 0.0）
    f1 = sc.score_f1_vec(pre["pos_250"].values)
    f2 = sc.score_f2_vec(pre["main_net"].values, pre["volume_ratio"].values)
    f3 = sc.score_f3_vec(pre["fc_days_since"].values, pre["ex_days_since"].values,
                          pre["fc_pchange"].values, pre["dv_year_sum"].values)
    f4 = sc.score_f4_vec(pre["rsi6"].values, pre["macd_hist"].values)
    f5 = sc.score_f5_vec(pre["industry_pct"].values)
    f6 = sc.score_f6_vec(est["pe_ttm"].values, est["turnover_rate"].values)
    tot = sc.total_new_vec(f1, f2, f3, f4, f5, f6)
    out = pre[["ts_code", "prob"]].copy()
    for k, v in zip(["F1", "F2", "F3", "F4", "F5", "F6"], [f1, f2, f3, f4, f5, f6]):
        out[f"SC_{k}"] = v
    out["total_new"] = tot
    out["est_pe_hit"] = est["pe_ttm"].notna().values
    return out


def sig(pool):
    """生成池签名，与 OP 打印值同口径。"""
    ind = pool["industry_pct"].astype(float)
    return {
        "prob_min": round(pool["prob"].min(), 4), "prob_max": round(pool["prob"].max(), 4),
        "est_hit": round(pool["est_pe_hit"].mean(), 2),
        "main_net_nn": round(pool["main_net"].notna().mean(), 2),
        "industry_nn": round(pool["industry_pct"].notna().mean(), 2),
        "volume_nn": round(pool["volume_ratio"].notna().mean(), 2),
        "total_max": round(pool["total_new"].max(), 1),
        "total_med": round(pool["total_new"].median(), 1),
        "total_min": round(pool["total_new"].min(), 1),
        "ind_mean": round(ind.mean(), 2), "ind_med": round(ind.median(), 2),
        "ind_min": round(ind.min(), 2), "ind_max": round(ind.max(), 2),
        "ind_p10": round(ind.quantile(0.10), 1), "ind_p90": round(ind.quantile(0.90), 2),
        "ind_neg": round((ind < 0).mean(), 3),
        "f5_mean": round(pool["SC_F5"].mean(), 3),
        "f5_dist": {float(k): int(v) for k, v in pool["SC_F5"].value_counts().items()},
    }


def cmp_sig(name, s):
    print(f"\n[{name}] 与 OP 打印值逐项对照：")
    n_ok = n_bad = 0
    for k, opv in OP.items():
        mv = s.get(k)
        if isinstance(opv, dict):
            ok = mv == opv
        else:
            ok = abs(float(mv) - float(opv)) < 0.005 if isinstance(opv, float) else mv == opv
        n_ok += ok
        n_bad += (not ok)
        print(f"  {'OK ' if ok else '!!X'} {k:<12} mine={mv}  op={opv}")
    print(f"  => {n_ok} 项一致 / {n_bad} 项不一致")


def main():
    mypool = pd.read_csv(CSV, dtype={"ts_code": str})
    mypool["est_pe_hit"] = None

    # 三只候选票的完整 SC + 名次
    print("[三只 58.5 票完整分解]")
    trio = ["000786.SZ", "920651.BJ", "001332.SZ"]
    srt = mypool.sort_values("prob", ascending=False).reset_index(drop=True)
    srt["rank"] = srt.index + 1
    for c in trio:
        r = srt[srt["ts_code"] == c].iloc[0]
        print(f"  {c}: prob_rank={int(r['rank'])}  F1={r['SC_F1']:.0f} F2={r['SC_F2']:.0f} "
              f"F3={r['SC_F3']:.0f} F4={r['SC_F4']:.0f} F5={r['SC_F5']:.0f} F6={r['SC_F6']:.0f} "
              f"total={r['total_new']:.1f}  main_net={r['main_net']!r} vol_ratio={r['volume_ratio']!r}")

    # 第 101~103 名快照行，走同款评分（用于池替换场景）
    snap = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
    snap["trade_date"] = pd.to_datetime(snap["trade_date"])
    day = snap[snap["trade_date"] == pd.Timestamp("2026-09-28")].copy()
    feat_cols = json.load(open(META0915, encoding="utf-8"))["feature_cols"]
    day["prob"] = lgb.Booster(model_file=M0915).predict(day[feat_cols].astype("float32").values)
    out_pool = day.sort_values("prob", ascending=False).iloc[100:103].copy()  # rank 101~103
    print("\n[第 101~103 名]（我的池外首批票）")
    daily = pd.read_parquet(DC.DAILY_BASIC, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["trade_date"] == pd.Timestamp("2026-09-28")].set_index(["trade_date", "ts_code"])
    scored = score_rows(out_pool, daily)
    print(scored.to_string(index=False))

    # est 命中率：OP 的 67% 是池级 pe 命中率；给我的池补上 est_pe_hit
    db2 = daily.reset_index()
    pe_map = dict(zip(db2["ts_code"], db2["pe_ttm"].notna()))
    mypool["est_pe_hit"] = mypool["ts_code"].map(pe_map).fillna(False)

    # 场景 0：我的原池（基准，应与 OP 全部一致才说明池同构）
    cmp_sig("场景0 我的原池(100只)", sig(mypool))

    # 场景 1a/1b/1c：池 - X + rank101
    r101 = scored.iloc[0:1]
    r101_full = out_pool.iloc[0:1].copy()
    r101_full["total_new"] = r101["total_new"].values
    r101_full["SC_F5"] = r101["SC_F5"].values
    r101_full["est_pe_hit"] = r101["est_pe_hit"].values
    for x in trio:
        pool2 = pd.concat([mypool[~mypool["ts_code"].isin([x])], r101_full],
                           ignore_index=True)
        cmp_sig(f"场景1 池 - {x} + rank101({r101_full['ts_code'].iloc[0]})", sig(pool2))

    # 场景 2a/2b/2c：池 - X（99 只）
    for x in trio:
        pool2 = mypool[~mypool["ts_code"].isin([x])]
        cmp_sig(f"场景2 池 - {x}（99只）", sig(pool2))


if __name__ == "__main__":
    main()
