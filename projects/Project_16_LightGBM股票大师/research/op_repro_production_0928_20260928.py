# -*- coding: utf-8 -*-
"""T-20260928 验收复现（只读）：逐行复现 deploy_predict_g2.py 生产链路。

v2 已定位自身 bug：漏做 ind_pct_ths -> industry_pct 映射 + 漏调 _fill_f3_events，
导致 F5 误判恒 5.0 / F3 误判恒 4.0，与生产 CSV 对不上。
本脚本直接 import 生产的 _fill_f3_events，复刻完整链路，目标是
「独立复算的红线 60 命中集合 == 生产落盘 CSV 的 ts_code」。

不写任何生产目录，不交易，不做 git 写操作。
"""
import json
import os
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)

import data_config as DC
import scorecard_real as SR
from deploy_predict_g2 import _fill_f3_events

LIVE = os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet")
CSV_0928 = os.path.join(
    DC.DATA_DIR, "selections", "g2", "20260928_g2_top10.csv"
)
MAIN_DAILY = r"D:\astock\daily\stock_daily.parquet"
PRESELECT = 100
RED_LINE = 60.0


def main():
    G2_MODEL, G2_META = DC.g2_live()
    print("[0] live 模型 = %s" % os.path.basename(G2_MODEL))
    feats = json.load(open(G2_META, encoding="utf-8"))["feature_cols"]
    print("    特征数 = %d" % len(feats))

    day = pd.read_parquet(LIVE)
    day["trade_date"] = pd.to_datetime(day["trade_date"])
    target = day["trade_date"].max()
    print("[1] 快照日=%s 股票数=%d" % (target.date(), len(day)))

    day["_p"] = lgb.Booster(model_file=G2_MODEL).predict(
        day[feats], num_threads=4
    )
    pre = day.nlargest(PRESELECT, "_p").copy()
    print("[2] Top100 池完成")

    # ---- F6 est（复刻 L200-L223）----
    try:
        daily = pd.read_parquet(
            MAIN_DAILY,
            columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"],
        )
        if "trade_date" not in daily.columns:
            raise KeyError("no trade_date")
    except Exception:
        daily = pd.read_parquet(
            MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]
        ).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily.reindex(idx)
    hit = (
        float(est["pe_ttm"].notna().mean())
        if ("pe_ttm" in est.columns and len(est))
        else 0.0
    )
    print("[3] MAIN_DAILY max=%s est命中率=%.0f%%"
          % (daily.index.get_level_values("trade_date").max().date(),
             hit * 100))
    if hit < 0.5:
        print("    -> est=None，F6 走快照兜底（与生产一致）")
        est = None

    # ---- F2 / F5 映射（复刻 L226-L233）----
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan
    print("[4] industry_pct 均值=%.2f%%（板块普跌度）"
          % pre["industry_pct"].mean())

    # ---- F3 面板外事件 fail-safe（复刻 L267）----
    filled = _fill_f3_events(pre, verbose=True)

    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre["SC_%s" % f] = sc[f].values

    print("[5] 维度分布（Top100 池）")
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        s = sc[f]
        u = s.nunique()
        flag = "!! 恒值" if u <= 1 else ("!! 近死维度" if u <= 2 else "OK")
        print("    %s uniq=%2d mean=%5.2f  %s  %s"
              % (f, u, s.mean(), flag,
                 dict(s.value_counts().sort_index())))

    print("[6] FAIL-LOUD 复刻：SC_F2/SC_F5 恒值 -> 中止落盘")
    if pre["SC_F2"].nunique() <= 1 or pre["SC_F5"].nunique() <= 1:
        print("    !! 会触发 SystemExit")
    else:
        print("    OK 不触发")

    print("[7] 红线统计")
    for ln in (60.0, 58.0, 55.0, 50.0):
        print("    >=%.0f : %d 只" % (ln, int((pre["total_new"] >= ln).sum())))
    print("    max=%.1f 中位=%.1f" % (pre["total_new"].max(),
                                    pre["total_new"].median()))

    got = pre.loc[pre["total_new"] >= RED_LINE].nlargest(10, "total_new")
    got = got.assign(prob=got["_p"])
    prod = pd.read_csv(CSV_0928)
    ok = set(prod["ts_code"].astype(str))
    mine = set(got["ts_code"].astype(str))

    print("[8] 逐行对比")
    print("    生产落盘 CSV:")
    print(prod.to_string(index=False))
    print("    独立复算命中:")
    print(got[["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3",
               "SC_F4", "SC_F5", "SC_F6"]].to_string(index=False))
    print("[9] ts_code 完全一致: %s" % (mine == ok))
    if mine == ok:
        m = got.set_index("ts_code")
        for c in ["total_new", "SC_F1", "SC_F2", "SC_F3",
                  "SC_F4", "SC_F5", "SC_F6"]:
            p = float(prod.set_index("ts_code").loc[m.index[0], c])
            r = float(m.loc[m.index[0], c])
            print("    %-9s 生产=%.1f 复算=%.1f %s"
                  % (c, p, r, "OK" if abs(p - r) < 1e-6 else "!! 不符"))

    print("[10] F3 fail-safe 填充行数=%d / %d" % (filled, len(pre)))

    # 评分卡对 F5 线性：total = 0.10*F5*10，故中性化增量 = 1.0*(5 - F5)
    print("[11] F5 中性化反事实（同一 live 模型/同一 Top100，F5 强制=5.0）")
    delta = SR.SC_WEIGHTS["F5"] * (5.0 - pre["SC_F5"]) * 10.0
    neu = pre["total_new"] + delta
    print("    平均压分 = %.2f 分（= 0.10*(5-F5均值)*10）"
          % (-delta.mean()))
    for ln in (60.0, 58.0, 55.0, 50.0):
        print("    >=%.0f : 实际 %d 只 -> 中性化后 %d 只"
              % (ln, int((pre["total_new"] >= ln).sum()),
                 int((neu >= ln).sum())))
    print("    max=%.1f -> 中性化后 %.1f"
          % (pre["total_new"].max(), neu.max()))
    top_neu = pre.assign(_neu=neu).nlargest(10, "_neu")
    print("    中性化后 Top10:")
    print(top_neu[["ts_code", "total_new", "SC_F5", "_neu"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
