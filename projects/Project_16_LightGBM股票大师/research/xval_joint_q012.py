# -*- coding: utf-8 -*-
"""XVAL JOINT（T-20260928-XVAL2）—— Q0 基线自检 + Q1 F5 中性化 4vs5 裁决 + Q2 模型身份核实。

只读任务：不修改任何生产代码/配置/数据；输出仅打印到 stdout。
复用上轮中间产物 xval_de_p1_top100_20260928.csv / xval_de_bt_cache.parquet，避免重算。
"""
import json
import os
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import review_full as RF            # 生产 F6（只读复用）
import xval_de_scorecard as SCV     # 上轮向量化评分卡（已与生产逐值校验）

M0825 = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
M0915 = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META0825 = os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260825.json")
META0915 = os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260915.json")
CSV = os.path.join(HERE, "xval_de_p1_top100_20260928.csv")
CACHE = os.path.join(HERE, "xval_de_bt_cache.parquet")
CACHE_MKT = os.path.join(HERE, "xval_de_bt_cache_mkt.parquet")


def total_recompute(f1, f2, f3, f4, f5, f6):
    """生产 scorecard_real.py:68-69 同款求和路径（F5 替换为目标值后整体重算）。"""
    W1, W2, W3, W4, W5, W6 = 0.25, 0.20, 0.20, 0.15, 0.10, 0.10
    t = W1 * f1 + W2 * f2
    t = t + W3 * f3
    t = t + W4 * f4
    t = t + W5 * f5
    return t * 10.0 + W6 * f6 * 10.0


def total_exact(f1, f2, f3, f4, f5, f6):
    """精确有理数总分（权重 0.25/0.2/0.2/0.15/0.1/0.1 ×10 = 2.5/2/2/1.5/1/1）。"""
    return (Fraction(25) * Fraction(f1) + Fraction(20) * Fraction(f2)
            + Fraction(20) * Fraction(f3) + Fraction(15) * Fraction(f4)
            + Fraction(10) * Fraction(f5) + Fraction(10) * Fraction(f6)) / 10


def main():
    print("=" * 78)
    print("Q0 基线自检")
    print("=" * 78)
    cache = pd.read_parquet(CACHE)
    cache["trade_date"] = pd.to_datetime(cache["trade_date"])
    print(f"[cache] rows={len(cache):,}  days={cache['trade_date'].nunique()}  "
          f"range={cache['trade_date'].min().date()} ~ {cache['trade_date'].max().date()}")
    mkt = pd.read_parquet(CACHE_MKT).reset_index()
    mkt["trade_date"] = pd.to_datetime(mkt["trade_date"])
    print(f"[cache_mkt] rows={len(mkt)}  days={mkt['trade_date'].nunique()}  "
          f"末值={mkt['mkt'].iloc[-1]!r}（期望 NaN：面板最后一日无前向收益）")

    df = pd.read_csv(CSV, dtype={"ts_code": str})
    print(f"[p1_csv] rows={len(df)}  cols={list(df.columns)}")
    print(f"[p1_csv] 重复 ts_code 数 = {int(df['ts_code'].duplicated().sum())}")

    db = pd.read_parquet(DC.DAILY_BASIC, columns=["pe_ttm"]).reset_index()
    print(f"[daily_basic] rows={len(db):,}  max_date={pd.to_datetime(db['trade_date']).max().date()}")

    snap = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
    snap["trade_date"] = pd.to_datetime(snap["trade_date"])
    print(f"[snapshot] rows={len(snap):,}  max_date={snap['trade_date'].max().date()}  "
          f"唯一日={snap['trade_date'].nunique()}")
    print(f"[live指针] {DC.g2_live()}")

    # ================= Q2：命题1 实际加载的模型 =================
    print()
    print("=" * 78)
    print("Q2 命题1 实际加载模型核实")
    print("=" * 78)
    m0825, m0915 = lgb.Booster(model_file=M0825), lgb.Booster(model_file=M0915)
    fc0825 = json.load(open(META0825, encoding="utf-8"))["feature_cols"]
    fc0915 = json.load(open(META0915, encoding="utf-8"))["feature_cols"]
    print(f"[0825] trees={m0825.num_trees()}  feats={len(fc0825)}")
    print(f"[0915] trees={m0915.num_trees()}  feats={len(fc0915)}")

    day = snap[snap["trade_date"] == pd.Timestamp("2026-09-28")].copy()
    p0825 = m0825.predict(day[fc0825].astype("float32").values)
    p0915 = m0915.predict(day[fc0915].astype("float32").values)
    day = day.assign(_p0825=p0825, _p0915=p0915)
    code = "000607.SZ"
    r = day[day["ts_code"] == code]
    print(f"\n[决定性检验] {code} prob：")
    print(f"  0825 模型 = {float(r['_p0825'].iloc[0]):.6f}")
    print(f"  0915 模型 = {float(r['_p0915'].iloc[0]):.6f}")
    print(f"  生产 CSV  = 0.6541（0.6541xx 截断到 4 位）")

    # prob 并列检查（Q1 排查项：nlargest 在并列 prob 时的稳定性）
    srt = day.sort_values("_p0915", ascending=False)["_p0915"].reset_index(drop=True)
    print(f"\n[并列检查] 0915 prob 第 96~104 名：")
    print("  " + " ".join(f"{srt.iloc[i]:.6f}" for i in range(95, 105)))
    print(f"  rank100 == rank101 ? {srt.iloc[99] == srt.iloc[100]}")

    # ================= Q1：F5 中性化 4 vs 5 =================
    print()
    print("=" * 78)
    print("Q1 F5 中性化反事实：红线60 通过数 1 -> ? （DE报5 / OP报4）")
    print("=" * 78)
    pre = df
    # 两条浮点路径
    pre["neu_delta"] = pre["total_new"] - (pre["SC_F5"] - 5.0)          # 上轮 DE 路径（差值法）
    pre["neu_recalc"] = [total_recompute(r.SC_F1, r.SC_F2, r.SC_F3, r.SC_F4, 5.0, r.SC_F6)
                         for r in pre.itertuples()]                      # 整体重算法（F5=5 代入）
    pre["neu_exact"] = [total_exact(r.SC_F1, r.SC_F2, r.SC_F3, r.SC_F4, 5, r.SC_F6)
                        for r in pre.itertuples()]                       # 精确有理数

    for thr in (60.0, 58.0):
        n_orig = int((pre["total_new"] >= thr).sum())
        n_delta = int((pre["neu_delta"] >= thr).sum())
        n_recalc = int((pre["neu_recalc"] >= thr).sum())
        n_exact = sum(1 for v in pre["neu_exact"] if v >= Fraction(int(thr * 10), 10))
        print(f"\n红线 {thr:.0f}: 原始 {n_orig} | 差值法 {n_delta} | 重算法 {n_recalc} | 精确值 {n_exact}")

    # 全部 total_new >= 57 的票（覆盖所有可能上穿 60 的候选 + 原>=58 全体）
    show = pre[pre["total_new"] >= 56.5].sort_values("total_new", ascending=False)
    print(f"\n[明细] 原始 total_new >= 56.5 的全部 {len(show)} 只：")
    print(f"{'ts_code':<11}{'prob':>8}{'total_new':>22}{'SC_F5':>7}{'neu_delta':>22}"
          f"{'neu_recalc':>22}{'neu_exact':>10}{'>=60delta':>10}{'>=60recalc':>11}{'>=60exact':>10}")
    for r in show.itertuples():
        e60 = "Y" if r.neu_exact >= Fraction(600, 10) else "N"
        print(f"{r.ts_code:<11}{r.prob:>8.4f}{r.total_new!r:>22}{r.SC_F5:>7.0f}"
              f"{r.neu_delta!r:>22}{r.neu_recalc!r:>22}{float(r.neu_exact):>10.1f}"
              f"{'Y' if r.neu_delta >= 60 else 'N':>10}"
              f"{'Y' if r.neu_recalc >= 60 else 'N':>11}{e60:>10}")

    # 边界票精查：neu 落在 (59.9, 60.1) 的票
    bnd = pre[(pre["neu_delta"] > 59.9) & (pre["neu_delta"] < 60.1)]
    print(f"\n[边界票] neu_delta ∈ (59.9, 60.1) 的票数 = {len(bnd)}")
    for r in bnd.itertuples():
        print(f"  {r.ts_code}: total_new={r.total_new!r}  SC_F5={r.SC_F5:.0f}  "
              f"neu_delta={r.neu_delta!r}  neu_recalc={r.neu_recalc!r}  neu_exact={r.neu_exact}"
              f" (=60? {r.neu_exact == Fraction(60)})")

    # 排查项逐条
    print("\n[排查项]")
    print(f"  1. NaN 置 5：SC_F5 为 NaN 的票数 = {int(pre['SC_F5'].isna().sum())}；"
          f"industry_pct 为 NaN 的票数 = {int(pre['industry_pct'].isna().sum())}；"
          f"F5=5 的那只票 industry_pct = "
          f"{pre.loc[pre['SC_F5'] == 5, 'industry_pct'].tolist()!r}")
    print(f"  2. >= vs >：生产 deploy_predict_g2.py:278 与 scan_rotate_cost_real.py:377 均为 >=（源码已核）")
    f5n = pre["SC_F5"].value_counts().sort_index()
    print(f"  3. SC_F5 档位分布：{dict((f'{k:.0f}', int(v)) for k, v in f5n.items())}")

    # F3 fill 影响：用快照（fill 前）事件列重算 F3，比较 crossing 票是否被 fill 改分
    snap_idx = snap.set_index("ts_code")
    near = pre[pre["total_new"] >= 56.5]
    print(f"\n[F3 fill 排查] 对上述 {len(near)} 只近线票，比较快照(fill前) vs CSV(fill后) 事件列：")
    for r in near.itertuples():
        if r.ts_code not in snap_idx.index:
            print(f"  {r.ts_code}: 不在快照中（异常）")
            continue
        s = snap_idx.loc[r.ts_code]
        pre_f3 = SCV.score_f3_vec(
            np.array([s.get("fc_days_since", np.nan)], dtype=float),
            np.array([s.get("ex_days_since", np.nan)], dtype=float),
            np.array([s.get("fc_pchange", np.nan)], dtype=float),
            np.array([s.get("dv_year_sum", np.nan)], dtype=float))[0]
        cols = ["fc_days_since", "ex_days_since", "fc_pchange", "dv_year_sum"]
        changed = any(
            not (pd.isna(s.get(c, np.nan)) and pd.isna(getattr(r, c)))
            and float(s.get(c, np.nan) if not pd.isna(s.get(c, np.nan)) else np.nan) \
            != (float(getattr(r, c)) if not pd.isna(getattr(r, c)) else np.nan)
            for c in cols)
        print(f"  {r.ts_code}: SC_F3={r.SC_F3:.0f}  fill前F3={pre_f3:.0f}  "
              f"{'fill 改变了 F3' if pre_f3 != r.SC_F3 else 'F3 未被 fill 改变'}"
              f"{'（事件列有变化）' if changed else ''}")

    # F6 est 口径排查：est 失效回退快照 pe_ttm/turn_ma5 时，近线票 F6/总分/过线是否变化
    print(f"\n[F6 est 排查] 近线票在 est 回退口径下的总分变化：")
    for r in near.itertuples():
        s = snap_idx.loc[r.ts_code]
        f6_fb = RF.score_f6(s.get("pe_ttm", np.nan), s.get("turn_ma5", np.nan))
        tot_fb = total_recompute(r.SC_F1, r.SC_F2, r.SC_F3, r.SC_F4, 5.0, f6_fb)
        flag = "!! 过线状态翻转" if (tot_fb >= 60) != (r.neu_delta >= 60) else ""
        print(f"  {r.ts_code}: est口径F6={r.SC_F6:.0f} 回退口径F6={f6_fb:.0f}  "
              f"回退口径中性总分={tot_fb:.4f} {flag}")


if __name__ == "__main__":
    main()
