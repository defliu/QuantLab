# -*- coding: utf-8 -*-
"""XVAL T-20260928 命题1+命题2：独立复现 20260928_g2_top10.csv + Top100 F5 分布分析。

命题1：严格按 deploy_predict_g2.py main() 源码流程复现（当前 live 指针模型）：
  快照过滤 -> 模型推理 -> Top100 预选池 -> F6 est(DAILY_BASIC 命中率判定) ->
  main_net/industry_pct 现场映射 -> F2 主源判定(moneyflow 新鲜度) + 新浪兜底 ->
  F3 面板外事件 fail-safe -> 真实评分卡 -> 红线60 -> Top10 -> 与生产 CSV 逐格比对。
命题2：Top100 池 ind_pct_ths 分布 / F5 档位 / F5 中性化(=5)对总分与红线命中的影响。

只读：不修改任何生产代码与数据；输出仅落 research/。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict_g2 as DG      # 只 import 复用其 _fill_f3_events（生产同款函数）
import scorecard_real as SR         # 生产评分卡（最大保真）
import g2_realtime as RT

import xval_de_scorecard as SCV

TARGET = pd.Timestamp("2026-09-28")
POOL = 100
THRESHOLD = 60.0
TOP = 10
PROD_CSV = os.path.join(DC.DATA_DIR, "selections", "g2", "20260928_g2_top10.csv")
OUT_TOP100 = os.path.join(HERE, "xval_de_p1_top100_20260928.csv")


def main():
    print("=" * 70)
    print("命题1：复现 20260928_g2_top10.csv")
    print("=" * 70)
    G2_MODEL, G2_META = DC.g2_live()   # 走 live 指针，不硬编码
    print("[live 指针] model:", G2_MODEL)
    print("[live 指针] meta :", G2_META)
    feat_cols = json.load(open(G2_META, encoding="utf-8"))["feature_cols"]

    day = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
    day["trade_date"] = pd.to_datetime(day["trade_date"])
    day = day[day["trade_date"] == TARGET].copy()
    print(f"[1] 快照 {TARGET.date()} 股票数 {len(day):,} 特征 {len(feat_cols)}")

    booster = lgb.Booster(model_file=G2_MODEL)
    day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
    pre = day.nlargest(POOL, "prob").copy()
    print(f"[2] Top{POOL} prob 范围: {pre['prob'].min():.4f} ~ {pre['prob'].max():.4f}")

    # ---- F6 est（deploy_predict_g2.py:188-223 同款逻辑）----
    daily = None
    if os.path.exists(DC.DAILY_BASIC):
        try:
            daily = pd.read_parquet(DC.DAILY_BASIC,
                                    columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
            if "trade_date" not in daily.columns:
                raise KeyError("trade_date 不在数据列，回退旧布局")
        except Exception:
            try:
                daily = pd.read_parquet(DC.DAILY_BASIC,
                                        columns=["pe_ttm", "turnover_rate"]).reset_index()
            except Exception:
                daily = None
    if daily is None:
        try:
            daily = pd.read_parquet(DC.MAIN_DAILY,
                                    columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
            if "trade_date" not in daily.columns:
                raise KeyError("trade_date 不在数据列，回退旧布局")
        except Exception:
            daily = pd.read_parquet(DC.MAIN_DAILY,
                                    columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily.reindex(idx)
    est_hit = float(est["pe_ttm"].notna().mean()) if len(est) else 0.0
    print(f"[3] F6 est 命中率(Top100) = {est_hit:.0%}", end="")
    if est_hit < 0.5:
        print(" -> est=None，F6 走快照 pe_ttm/turn_ma5 兜底")
        est = None
    else:
        print(" -> est 生效（daily_basic 0928 当日口径）")

    # ---- main_net / industry_pct 现场映射（deploy_predict_g2.py:226-233）----
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan

    # ---- F2 主源判定（deploy_predict_g2.py:237-264）----
    MF_PATH = os.path.join(DC.ASTOCK_DIR, "moneyflow", "moneyflow.parquet")
    mf_fresh = False
    _mf = pd.read_parquet(MF_PATH, columns=["net_mf_amount"])
    _latest = pd.Timestamp(_mf.index.get_level_values("trade_date").max())
    mf_fresh = (TARGET - _latest).days <= 2
    print(f"[4] F2 主源 moneyflow 最新日 {_latest.date()} -> mf_fresh={mf_fresh}"
          f"（{'Tushare主源' if mf_fresh else '新浪兜底'}）")
    pool_codes = pre["ts_code"].astype(str).tolist()
    need_rt = []
    if mf_fresh:
        # 生产 fetch 全池但仅 NaN/0 主净额的票会被覆盖（deploy_predict_g2.py:256 条件），
        # 等价化简：只 fetch 会被覆盖的票（其余 rt 值生产逻辑不使用）
        _snap_ser = pre["main_net"]
        need_rt = [c for c in pool_codes
                   if pd.isna(_snap_ser[pre["ts_code"].astype(str) == c].iloc[0])
                   or _snap_ser[pre["ts_code"].astype(str) == c].iloc[0] == 0]
    else:
        need_rt = pool_codes
    print(f"    需新浪兜底的票数: {len(need_rt)} / {len(pool_codes)}"
          f"（生产 fetch 全池但仅覆盖 NaN/0 票，等价）")
    rt_hit = 0
    if need_rt:
        print(f"    [sina] fetch {len(need_rt)} 只（当日 2026-09-28 口径）...")
        rt = RT.fetch_main_net_sina(need_rt, target_date="2026-09-28")
        for c in need_rt:
            if c not in rt:
                continue
            m = pre["ts_code"].astype(str) == c
            if not m.any():
                continue
            _snap = pre.loc[m, "main_net"].iloc[0]
            if (not mf_fresh) or pd.isna(_snap) or _snap == 0:
                pre.loc[m, "main_net"] = rt[c].get("main_net_yuan", np.nan)
                rt_hit += 1
        print(f"    [sina] 覆盖 {rt_hit}/{len(need_rt)} 只")

    # ---- F3 面板外事件 fail-safe（生产同款函数）----
    filled = DG._fill_f3_events(pre, verbose=True)
    print(f"[5] F3 fail-safe 重算 {filled} 行")

    # ---- 真实评分卡（生产函数）----
    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre[f"SC_{f}"] = sc[f].values
    assert pre["SC_F2"].nunique() > 1 and pre["SC_F5"].nunique() > 1 \
        and pre["main_net"].notna().sum() > 0, "FAIL-LOUD 会触发（与生产不符，需排查）"
    print("[6] 评分卡完成（FAIL-LOUD 自检通过：SC_F2/SC_F5 非恒值）")

    # ---- 位置过滤：生产默认关闭（PF_MODE_G2 未设）----
    assert os.environ.get("PF_MODE_G2", "").strip() == "", "环境有 PF_MODE_G2，需排查"

    # ---- 红线 60 -> Top10 ----
    passed = pre[pre["total_new"] >= THRESHOLD].copy()
    print(f"[7] 红线 {THRESHOLD:.0f} 通过 {len(passed)} / {POOL} 只")
    picks = passed.nlargest(TOP, "total_new")
    cols = ["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]
    out = picks[cols].copy()
    out["prob"] = out["prob"].round(4)
    out["total_new"] = out["total_new"].round(1)
    out["trade_date"] = "20260928"
    print("\n我的复现结果：")
    print(out.to_string(index=False))

    prod = pd.read_csv(PROD_CSV, encoding="utf-8-sig", dtype={"ts_code": str})
    print("\n生产 CSV：")
    print(prod.to_string(index=False))

    # ---- 逐格比对 ----
    print("\n[逐格比对]")
    mism = []
    if len(out) != len(prod):
        mism.append(f"行数: 我的={len(out)} 生产={len(prod)}")
    n = min(len(out), len(prod))
    for i in range(n):
        mine_r = out.iloc[i]
        prod_r = prod.iloc[i]
        for c in ["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3",
                  "SC_F4", "SC_F5", "SC_F6", "trade_date"]:
            mv, pv = mine_r[c], prod_r[c]
            if isinstance(mv, float) or isinstance(pv, float):
                same = (pd.isna(mv) and pd.isna(pv)) or (float(mv) == float(pv))
            else:
                same = str(mv) == str(pv)
            if not same:
                mism.append(f"row{i} {c}: 我的={mv!r} 生产={pv!r}")
    if mism:
        print("!! 不一致项：")
        for m in mism:
            print("   ", m)
    else:
        print("全部 %d 行 × 10 字段逐格一致 ✓" % n)

    # ---- 我的向量化评分卡交叉验证（为命题2与后续命题做保真校验）----
    f1v = SCV.score_f1_vec(pre["pos_250"].values)
    f2v = SCV.score_f2_vec(pre["main_net"].values, pre["volume_ratio"].values)
    f3v = SCV.score_f3_vec(pre["fc_days_since"].values, pre["ex_days_since"].values,
                           pre["fc_pchange"].values, pre["dv_year_sum"].values)
    f4v = SCV.score_f4_vec(pre["rsi6"].values, pre["macd_hist"].values)
    f5v = SCV.score_f5_vec(pre["industry_pct"].values)
    if est is not None and "pe_ttm" in est.columns:
        pe_v = est["pe_ttm"].values
        tu_v = est["turnover_rate"].values
    else:
        pe_v = pre["pe_ttm"].values
        tu_v = pre["turn_ma5"].values
    f6v = SCV.score_f6_vec(pe_v, tu_v)
    tot_v = SCV.total_new_vec(f1v, f2v, f3v, f4v, f5v, f6v)
    diffs = {
        "F1": np.abs(f1v - pre["SC_F1"].values).max(),
        "F2": np.abs(f2v - pre["SC_F2"].values).max(),
        "F3": np.abs(f3v - pre["SC_F3"].values).max(),
        "F4": np.abs(f4v - pre["SC_F4"].values).max(),
        "F5": np.abs(f5v - pre["SC_F5"].values).max(),
        "F6": np.abs(f6v - pre["SC_F6"].values).max(),
        "total": np.abs(tot_v - pre["total_new"].values).max(),
    }
    print("\n[向量化评分卡 vs 生产函数] max|diff|:", {k: float(v) for k, v in diffs.items()})

    pre.to_csv(OUT_TOP100, index=False, encoding="utf-8-sig")
    print(f"\nTop100 全量（含 prob/SC/特征）已存: {OUT_TOP100}")

    # ================= 命题 2 =================
    print("\n" + "=" * 70)
    print("命题2：Top100 预选池 F5 分布与单维影响")
    print("=" * 70)
    ind = pre["ind_pct_ths"].astype(float)
    print("\n[ind_pct_ths 分布] (%%, 同花顺881板块当日涨幅, build_g2_daily 自算)")
    print(ind.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95]).to_string())
    print("负值占比: %.1f%%  (>0 仅 %d 只)" % ((ind < 0).mean() * 100, int((ind > 0).sum())))

    f5 = pre["SC_F5"]
    print("\n[F5 档位计数]")
    vc = f5.value_counts().sort_index()
    for k, v in vc.items():
        print(f"  F5={k:.0f}: {v} 只 ({v / len(pre):.0%})")
    print(f"F5 均值 = {f5.mean():.3f}  最低档(2.0)占比 = {(f5 == 2).mean():.0%}")

    # F5 中性化（=5 分）后的总分与红线命中变化
    w = 1.0  # F5 权重 0.10 × 10 = 每分 1.0 分总分
    pre["total_f5neutral"] = pre["total_new"] - (pre["SC_F5"] - 5.0) * w
    for thr in (60.0, 58.0, 55.0):
        n_orig = int((pre["total_new"] >= thr).sum())
        n_neu = int((pre["total_f5neutral"] >= thr).sum())
        cross_up = int(((pre["total_new"] < thr) & (pre["total_f5neutral"] >= thr)).sum())
        cross_dn = int(((pre["total_new"] >= thr) & (pre["total_f5neutral"] < thr)).sum())
        print(f"\n红线 {thr:.0f}: 原始通过 {n_orig} 只 -> F5中性后 {n_neu} 只"
              f"（上穿 {cross_up} / 跌出 {cross_dn}）")
    d_tot = (pre["total_f5neutral"] - pre["total_new"])
    print(f"\nF5 中性化对总分的平均改动: {d_tot.mean():+.2f} 分（范围 {d_tot.min():+.1f} ~ {d_tot.max():+.1f}）")
    print(f"Top100 总分均值: 原始 {pre['total_new'].mean():.2f} / F5中性 {pre['total_f5neutral'].mean():.2f}")
    # 中性化后被拉高/压低的票数
    print(f"被压低(F5<5, 行业跌): {(pre['SC_F5'] < 5).sum()} 只 | 被拉高(F5>5): {(pre['SC_F5'] > 5).sum()} 只"
          f" | 不变(F5=5): {(pre['SC_F5'] == 5).sum()} 只")
    # 唯一通过票
    if len(passed):
        r = passed.iloc[0]
        print(f"\n通过票 {r['ts_code']}: SC_F5={r['SC_F5']:.0f} (ind={r['ind_pct_ths']:.2f}%), "
              f"total={r['total_new']:.2f}, F5中性后 total={r['total_new'] - (r['SC_F5']-5.0):.2f}")


if __name__ == "__main__":
    main()
