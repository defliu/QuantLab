# -*- coding: utf-8 -*-
"""T-20260928 验收复算（只读）：
1) 修正 v1 脚本漏做的 ind_pct_ths -> industry_pct 映射，重算 09-28 实盘 Top100 评分卡；
2) 输出 SC_F1..F6 真实分布（验证各维度是否死代码/近乎恒值）；
3) 量化 F5 对 total_new 的压分幅度（中性 5 分反事实对照）；
4) 按生产阈值 60 统计过线数，与生产 CSV 实际落盘行数核对。

口径对齐 deploy_predict_g2.py L224-L276：
  - main_net = mf_main_net * 1e4（万元 -> 元）
  - industry_pct = ind_pct_ths
  - F6 est：主库命中率 <50% -> est=None，走快照 pe_ttm/turn_ma5
不写任何生产目录，不交易，不做 git 写操作。
"""
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = r"D:\QuantLab"
sys.path.insert(0, PROJ)
sys.path.insert(0, ROOT)

LIVE = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
MODEL = os.path.join(
    ROOT, "models", "lgb_model_v3_g2_strong_real_20260825_1964t.txt"
)
META = os.path.join(
    PROJ, "data", "features_v3_g2_strong_real_20260825.json"
)
CSV_0928 = os.path.join(
    PROJ, "data", "selections", "g2", "20260928_g2_top10.csv"
)
CSV_0924 = os.path.join(
    PROJ, "data", "selections", "g2", "20260924_g2_top10.csv"
)
MAIN_DAILY = r"D:\astock\daily\stock_daily.parquet"
PRESELECT = 100
RED_LINE = 60.0


def load_est(target, pre):
    """复刻 deploy_predict_g2 的 F6 est 逻辑（含 <50% 命中率回退）。"""
    try:
        daily = pd.read_parquet(
            MAIN_DAILY,
            columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"],
        )
        if "trade_date" not in daily.columns:
            raise KeyError("trade_date 不在列")
    except Exception:
        daily = pd.read_parquet(
            MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]
        ).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    print("  MAIN_DAILY max = %s" % daily["trade_date"].max().date())
    daily = daily.set_index(["trade_date", "ts_code"])
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily.reindex(idx)
    hit = (
        float(est["pe_ttm"].notna().mean())
        if ("pe_ttm" in est.columns and len(est))
        else 0.0
    )
    print("  est 命中率 = %.1f%%" % (hit * 100))
    if hit < 0.5:
        print("  -> <50%%，est=None，F6 走快照兜底")
        return None, hit
    return est, hit


def describe(name, s):
    u = s.nunique()
    print(
        "  %-6s n=%d uniq=%2d mean=%5.2f min=%4.1f p50=%4.1f max=%4.1f"
        % (name, len(s), u, s.mean(), s.min(), s.median(), s.max())
    )
    if u <= 8:
        vc = s.value_counts().sort_index()
        print("        分布: " + ", ".join(
            "%s:%d" % (k, v) for k, v in vc.items()
        ))
    return u


def main():
    import json

    import lightgbm as lgb

    from scorecard_real import compute_real_scorecard

    with open(META, "r", encoding="utf-8") as f:
        _meta = json.load(f)
    feats = _meta.get("feature_cols") or _meta.get("features")

    live = pd.read_parquet(LIVE)
    live["trade_date"] = pd.to_datetime(live["trade_date"])
    ldate = live["trade_date"].max()
    print("[1] 快照日期=%s 股票数=%d 特征数=%d"
          % (ldate.date(), len(live), len(feats)))

    print("[2] 关键列非空率")
    for c in ["mf_main_net", "ind_pct_ths", "volume_ratio",
              "pe_ttm", "turn_ma5"]:
        if c in live.columns:
            print("    %-14s %.1f%%" % (c, live[c].notna().mean() * 100))

    # ---- v1 脚本 bug 复现证明：不做映射时 industry_pct 是否存在 ----
    print("[3] v1 bug 定位")
    print("    快照是否自带 industry_pct 列: %s"
          % ("industry_pct" in live.columns))
    print("    -> v1 复算漏做映射 => industry_pct 全 NaN => F5 恒 5.0"
          "（该 5.00 是脚本 bug，不是生产事实）")

    booster = lgb.Booster(model_file=MODEL)
    live["_p"] = booster.predict(live[feats], num_threads=4)
    top = live.sort_values("_p", ascending=False).head(PRESELECT).copy()
    print("[4] Top100 池完成（prob p50=%.4f p90=%.4f）"
          % (top["_p"].median(), top["_p"].quantile(0.9)))

    # ---- 修正后的生产口径映射 ----
    top["main_net"] = top["mf_main_net"] * 1e4
    top["industry_pct"] = top["ind_pct_ths"]
    print("[5] industry_pct（=ind_pct_ths）非空率 %.1f%%，均值 %.2f%%"
          % (top["industry_pct"].notna().mean() * 100,
             top["industry_pct"].mean()))

    est, _ = load_est(ldate, top)
    sc = compute_real_scorecard(top, est)
    s = sc["total_new_real"]

    print("[6] 修正后 Top100 评分卡分布")
    nus = {}
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        nus[f] = describe(f, sc[f])
    describe("total", s)

    print("[7] 维度健康判定（复刻 FAIL-LOUD 口径）")
    for f, v in nus.items():
        if v <= 1:
            print("    !! %s 恒值（死代码）" % f)
        elif v <= 2:
            print("    !! %s 仅 %d 档（近乎死维度）" % (f, v))
        else:
            print("    OK %s %d 档" % (f, v))

    print("[8] 红线通过数")
    for ln in (60.0, 58.0, 55.0, 50.0):
        print("    >=%.0f : %d 只" % (ln, int((s >= ln).sum())))
    print("    max=%.1f 中位=%.1f" % (s.max(), s.median()))

    # ---- 反事实：F5 置中性 5 分，看能多出多少候选 ----
    print("[9] F5 反事实（压分幅度量化）")
    import review_full as RF
    from scorecard_real import SC_WEIGHTS

    w5 = SC_WEIGHTS["F5"] * 10.0
    ind = top["industry_pct"].values
    f5_now = np.array([RF.score_f5(v) for v in ind], dtype=float)
    f5_fix = np.full(len(top), 5.0)
    s_cf = s.values - w5 * f5_now + w5 * f5_fix
    print("    F5 权重折算系数 = %.2f 分/档" % w5)
    print("    当前 F5: mean=%.2f  中性化后均值=5.00" % f5_now.mean())
    print("    F5 贡献压分 = 平均 %.2f 分"
          % (w5 * (f5_now.mean() - 5.0)))
    for ln in (60.0, 58.0, 55.0):
        print("    >=%.0f : 实际 %d 只 -> F5中性化 %d 只（+%d）"
              % (ln, int((s >= ln).sum()), int((s_cf >= ln).sum()),
                 int((s_cf >= ln).sum()) - int((s >= ln).sum())))
    print("    max: 实际 %.1f -> F5中性化 %.1f"
          % (s.max(), s_cf.max()))

    # ---- 与生产 CSV 核对 ----
    print("[10] 与生产 CSV 核对")
    for path in (CSV_0924, CSV_0928):
        if not os.path.exists(path):
            print("    MISSING %s" % path)
            continue
        c = pd.read_csv(path)
        print("    %s 行数=%d" % (os.path.basename(path), len(c)))
        print("      SC_F5=%s" % sorted(c["SC_F5"].unique().tolist()))
        print("      SC_F6=%s" % sorted(c["SC_F6"].unique().tolist()))
        print("      total min=%.1f max=%.1f"
              % (c["total_new"].min(), c["total_new"].max()))

    c28 = pd.read_csv(CSV_0928)
    ok = set(c28["ts_code"].astype(str))
    got = set(top.loc[s >= RED_LINE, "ts_code"].astype(str))
    print("[11] 红线60 独立复算命中: %s" % sorted(got))
    print("     生产落盘      : %s" % sorted(ok))
    print("     完全一致: %s" % (got == ok))


if __name__ == "__main__":
    main()
