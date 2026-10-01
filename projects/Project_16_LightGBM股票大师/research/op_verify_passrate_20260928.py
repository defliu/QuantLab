# -*- coding: utf-8 -*-
"""主代理独立复算：回测期 G2 候选通过率基线（只读，不写生产数据）。

口径严格对齐生产 scorecard_real.compute_real_scorecard + audit_stats_walkforward：
  模型 -> 每交易日 Top100 预选池 -> 真实评分卡 F1-F6（est=daily 主库）-> total_new_real
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
PANEL = os.path.join(PROJ, "data", "feature_panel_v3_enh2_n3_bt.parquet")
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260825.json")

START = pd.Timestamp("2024-07-01")
END = pd.Timestamp("2026-08-14")
PRESELECT = 100
LINES = (60.0, 58.0, 55.0, 52.0, 50.0)

sys.path.insert(0, PROJ)
os.chdir(PROJ)


def load_est():
    import data_config as DC
    try:
        daily = pd.read_parquet(
            DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]
        ).reset_index()
    except Exception as exc:
        print("[warn] daily 主库读取失败，est=None（F6 回退面板口径）: %s" % exc)
        return None
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    print("[info] est 源=%s 行数=%d" % (getattr(DC, "MAIN_DAILY", "?"), len(daily)))
    return daily


def main():
    from scorecard_real import compute_real_scorecard

    with open(META, "r", encoding="utf-8") as f:
        meta = json.load(f)
    feats = list(meta.get("features") or meta.get("feature_cols"))
    print("[info] 模型特征数=%d" % len(feats))

    need = ["trade_date", "ts_code", "main_net", "industry_pct",
            "ind_pct_ths", "volume_ratio", "pe_ttm", "turn_ma5"] + feats
    need = list(dict.fromkeys(need))
    panel = pd.read_parquet(PANEL, columns=need)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel[(panel["trade_date"] >= START) & (panel["trade_date"] <= END)]
    print("[info] 面板行数=%d 交易日=%d"
          % (len(panel), panel["trade_date"].nunique()))

    est_all = load_est()
    booster = lgb.Booster(model_file=MODEL)

    rows = []
    for d, g in panel.groupby("trade_date", sort=True):
        if len(g) < PRESELECT:
            continue
        g = g.copy()
        g["_p"] = booster.predict(g[feats], num_threads=4)
        pre = g.nlargest(PRESELECT, "_p")

        est = None
        if est_all is not None:
            idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
            est = est_all.reindex(idx)

        sc = compute_real_scorecard(pre, est)
        s = sc["total_new_real"]
        rec = {
            "trade_date": d,
            "max_total": float(np.nanmax(s)),
            "p50": float(np.nanmedian(s)),
            "p90": float(np.nanpercentile(s, 90)),
            "ind_med": float(np.nanmedian(pre["ind_pct_ths"])),
            "F5_mean": float(np.nanmean(sc["F5"])),
        }
        for ln in LINES:
            rec["pass%d" % int(ln)] = int((s >= ln).sum())
        rows.append(rec)

    df = pd.DataFrame(rows)
    print("\n=== 候选通过率基线（%d 个交易日）===" % len(df))
    for ln in LINES:
        c = "pass%d" % int(ln)
        print(
            "红线 %2.0f: 日均 %5.2f  中位 %4.1f  单日最大 %4d  "
            "有候选 %3d/%d (%.0f%%)  满10只 %3d"
            % (ln, df[c].mean(), df[c].median(), df[c].max(),
               int((df[c] > 0).sum()), len(df),
               100.0 * (df[c] > 0).sum() / len(df), int((df[c] >= 10).sum()))
        )

    print("\n=== total_new 分布 ===")
    print("单日最高分 p10/p25/p50/p75/p90 = %s"
          % np.round(np.percentile(df["max_total"], [10, 25, 50, 75, 90]), 1))
    print("Top100 池中位分 p50=%.1f  p90=%.1f"
          % (df["p50"].median(), df["p90"].median()))

    print("\n=== 强弱市分层（按预选池 ind_pct_ths 中位数四分位）===")
    qs = df["ind_med"].quantile([0.25, 0.5, 0.75]).values
    lab = pd.cut(df["ind_med"], [-99, qs[0], qs[1], qs[2], 99],
                 labels=["最弱25%", "偏弱", "偏强", "最强25%"])
    for k in ["最弱25%", "偏弱", "偏强", "最强25%"]:
        sub = df[lab == k]
        if not len(sub):
            continue
        print("%-8s n=%3d 板块中位%+.2f%% F5均值%.2f 日均过60=%5.2f "
              "最高分中位%5.1f 有候选%d"
              % (k, len(sub), sub["ind_med"].mean() * 100, sub["F5_mean"].mean(),
                 sub["pass60"].mean(), sub["max_total"].median(),
                 int((sub["pass60"] > 0).sum())))

    print("\n=== 近 15 个交易日明细 ===")
    print(df.tail(15)[["trade_date", "max_total", "pass60", "pass55",
                       "pass50", "F5_mean", "ind_med"]].to_string(index=False))


if __name__ == "__main__":
    main()
