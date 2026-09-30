# -*- coding: utf-8 -*-
"""XVAL5 工作包 A（T-20260929-XVAL3）：OP 逐票塌分机制复现与定案（只读审计）。

目的：用独立代码复现 2026-09-28 Top100 池「红线各档通过数」的两列
    DE/生产口径 1/4/12/21/33（thr 60/58/55/52/50）
    OP 复刻口径   1/3/11/20/32
  并逐票定位差异票、差异维度、差异来源，给出代码级根因。

两条路径（其余步骤完全一致：同快照/同 0915 模型/同 Top100/同 F6 est/同 F3 fail-safe/同评分卡）：
  Path-OP（bug 路径）  : 复刻 research/opx_verify_20260928.py run_live() L176-186 —— 仅做
                         main_net = mf_main_net*1e4 / industry_pct = ind_pct_ths 静态映射，
                         【无】生产 deploy_predict_g2.py L234-264 的新浪兜底步。
  Path-FIX（生产口径） : 复刻 deploy_predict_g2.py L226-264 —— 静态映射 + moneyflow 新鲜度
                         判定 + 新浪兜底（快照 main_net 为 0/NaN 时用新浪 main_net_yuan 覆盖）。

新浪兜底值获取：优先实时拉取 g2_realtime.fetch_main_net_sina(target_date='2026-09-28')；
  拉取失败时回退 research/xval5_a_sina_20260928.json（此前落盘值），并在输出中如实标注。
  无论哪种来源，都会与 DE 中间产物 xval_de_p1_top100_20260928.csv 的 main_net 交叉核对。

只读约束：不修改任何生产代码/配置/部署文件；不写 data/selections、D:/QMT_POOL；
  输出仅落 research/（含 xval5_a_sina_20260928.json 供回归测试离线复用）。
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)

import data_config as DC
import scorecard_real as SR
import g2_realtime as RT
from deploy_predict_g2 import _fill_f3_events  # 生产同款函数（只 import，不跑生产 main）

TARGET = pd.Timestamp("2026-09-28")
POOL = 100
THRESHOLD = 60.0
TOP = 10
THRS = (60.0, 58.0, 55.0, 52.0, 50.0)
SINA_JSON = os.path.join(HERE, "xval5_a_sina_20260928.json")
DE_CSV = os.path.join(HERE, "xval_de_p1_top100_20260928.csv")
PROD_CSV = os.path.join(DC.DATA_DIR, "selections", "g2", "20260928_g2_top10.csv")
# 生产 2026-09-28 实际加载的模型 = live 指针 0915（XVAL JOINT Q2 已终裁）。
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META = os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260915.json")


def load_top100():
    """快照 -> 0915 模型推理 -> 概率 Top100（两路径共用，完全一致）。

    数据源回落（2026-09-29 op 验收修复）：g2_latest_features.parquet 是滚动快照，
    每日被当日特征覆盖后 TARGET=2026-09-28 行消失、回归测试隔日即失效；
    主源缺 TARGET 行时回落到 data_live/snapshot_g2_20260928.parquet（09-28 归档，
    5839 行与当日滚动文件同规模，经生产 CSV 逐格断言交叉核对）。
    """
    feat_cols = json.load(open(META, encoding="utf-8"))["feature_cols"]
    src = os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet")
    day = pd.read_parquet(src)
    day["trade_date"] = pd.to_datetime(day["trade_date"])
    day = day[day["trade_date"] == TARGET].copy()
    if len(day) <= 5000:
        src = os.path.join(DC.LIVE_DIR, "snapshot_g2_20260928.parquet")
        day = pd.read_parquet(src)
        day["trade_date"] = pd.to_datetime(day["trade_date"])
        day = day[day["trade_date"] == TARGET].copy()
        print("[load_top100] 主源缺 TARGET 行，回落归档快照 %s" % os.path.basename(src))
    assert len(day) > 5000, "快照异常：%d 行" % len(day)
    booster = lgb.Booster(model_file=MODEL)
    day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
    pre = day.nlargest(POOL, "prob").copy()
    return pre


def load_est(pre):
    """F6 est：复刻 deploy_predict_g2 L188-223（daily_basic 优先 + 命中率 50% 门）。"""
    daily = None
    if os.path.exists(DC.DAILY_BASIC):
        try:
            daily = pd.read_parquet(DC.DAILY_BASIC,
                                    columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
            if "trade_date" not in daily.columns:
                raise KeyError("trade_date 不在数据列")
        except Exception:
            try:
                daily = pd.read_parquet(DC.DAILY_BASIC,
                                        columns=["pe_ttm", "turnover_rate"]).reset_index()
            except Exception:
                daily = None
    if daily is None:
        daily = pd.read_parquet(DC.MAIN_DAILY,
                                columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily.reindex(idx)
    hit = float(est["pe_ttm"].notna().mean()) if len(est) else 0.0
    if hit < 0.5:
        est = None
    return est, hit


def static_map(pre):
    """静态映射（生产 L226-233 = opx_verify L177-183，两路径共同部分）。"""
    pre = pre.copy()
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan
    return pre


def sina_fallback_map(pre, sina_map=None, fetch_live=True):
    """FIX 路径专属：新浪兜底覆盖（复刻生产 L237-264）。

    生产对全池 fetch，但覆盖条件 `if c in rt and (not mf_fresh or pd.isna(_snap) or _snap==0)`
    在 mf_fresh=True 时等价于只覆盖快照 0/NaN 票 —— 此处只 fetch 会被覆盖的票（等价化简，
    与 xval_de_p12.py L111-123 同款，已在 XVAL DE 报告中论证）。
    """
    pre = pre.copy()
    MF_PATH = os.path.join(DC.ASTOCK_DIR, "moneyflow", "moneyflow.parquet")
    mf_fresh = False
    _mf = pd.read_parquet(MF_PATH, columns=["net_mf_amount"])
    _latest = pd.Timestamp(_mf.index.get_level_values("trade_date").max())
    mf_fresh = (TARGET - _latest).days <= 2
    codes = pre["ts_code"].astype(str).tolist()
    snap = pre.set_index(pre["ts_code"].astype(str))["main_net"]
    need_rt = [c for c in codes
               if (not mf_fresh) or pd.isna(snap.get(c)) or snap.get(c) == 0]
    src = "none"
    rt = {}
    if fetch_live:
        try:
            rt = RT.fetch_main_net_sina(need_rt, target_date=TARGET.strftime("%Y-%m-%d"))
            if rt:
                src = "live_sina"
        except Exception as e:
            print("[sina] 实时拉取异常：%r" % e)
    if not rt and sina_map is not None:
        rt = dict(sina_map)
        src = "cached_json"
    hit = 0
    for c in need_rt:
        if c not in rt:
            continue
        m = pre["ts_code"].astype(str) == c
        if not m.any():
            continue
        _snap = pre.loc[m, "main_net"].iloc[0]
        if (not mf_fresh) or pd.isna(_snap) or _snap == 0:
            val = rt[c].get("main_net_yuan", np.nan) if isinstance(rt[c], dict) else rt[c]
            pre.loc[m, "main_net"] = val
            hit += 1
    return pre, mf_fresh, need_rt, src, hit


def score(pre, est):
    """F3 fail-safe + 真实评分卡（两路径同款生产函数）。"""
    pre = pre.copy()
    _fill_f3_events(pre, verbose=False)
    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre["SC_%s" % f] = sc[f].values
    return pre


def tier_counts(pre):
    return {thr: int((pre["total_new"] >= thr).sum()) for thr in THRS}


def run(fetch_live=True, sina_map=None, verbose=True):
    pre0 = load_top100()
    est, hit = load_est(pre0)
    if verbose:
        print("[1] 快照 %s / Top%d / F6 est 命中率 %.0f%%（est=%s）"
              % (TARGET.date(), POOL, hit * 100, "生效" if est is not None else "None"))
    pre_op = static_map(pre0)                                   # Path-OP：仅静态映射
    pre_fix, mf_fresh, need_rt, src, hit_rt = sina_fallback_map(
        static_map(pre0), sina_map=sina_map, fetch_live=fetch_live)  # Path-FIX：+新浪兜底
    if verbose:
        print("[2] mf_fresh=%s（moneyflow 最新日口径同生产判定）；需兜底票 %d 只，"
              "来源=%s，覆盖 %d 只" % (mf_fresh, len(need_rt), src, hit_rt))
    res_op = score(pre_op, est)
    res_fix = score(pre_fix, est)
    return res_op, res_fix, {"mf_fresh": mf_fresh, "need_rt": need_rt,
                             "sina_src": src, "sina_hit": hit_rt}


def main():
    print("=" * 78)
    print("XVAL5-A：OP 逐票塌分机制复现（目标日 2026-09-28，模型 0915）")
    print("=" * 78)
    sina_map = None
    if os.path.exists(SINA_JSON):
        sina_map = json.load(open(SINA_JSON, encoding="utf-8")).get("codes")
    res_op, res_fix, meta = run(fetch_live=True, sina_map=sina_map)

    # ---- 新浪兜底值落盘（供回归测试离线复用）----
    if meta["sina_src"] == "live_sina":
        zc = res_fix[res_fix["ts_code"].isin(meta["need_rt"])]
        codes = {r["ts_code"]: {"main_net_yuan": float(r["main_net"])}
                 for _, r in zc.iterrows()}
        with open(SINA_JSON, "w", encoding="utf-8") as f:
            json.dump({"target_date": str(TARGET.date()), "codes": codes}, f,
                      ensure_ascii=False, indent=1)
        print("[3] 新浪兜底值已落盘 %s（%d 只）" % (os.path.basename(SINA_JSON), len(codes)))

    # ---- 表1：红线各档通过数（复现任务书 §3.1 表）----
    print("\n[表1] 红线各档通过数（2026-09-28 Top100）")
    print("| 红线 | FIX(生产口径) | OP(复刻口径) | 差 |")
    print("|---:|---:|---:|---:|")
    for thr in THRS:
        a, b = tier_counts(res_fix)[thr], tier_counts(res_op)[thr]
        print("| %.0f | %d | %d | %+d |" % (thr, a, b, b - a))

    # ---- 与 DE/OP 报告原值核对 ----
    expect_fix = {60.0: 1, 58.0: 4, 55.0: 12, 52.0: 21, 50.0: 33}
    expect_op = {60.0: 1, 58.0: 3, 55.0: 11, 52.0: 20, 50.0: 32}
    ok_fix = tier_counts(res_fix) == expect_fix
    ok_op = tier_counts(res_op) == expect_op
    print("\n[核对] FIX 列 == DE 原始列 %s；OP 列 == OP 原始列 %s"
          % ("PASS" if ok_fix else "FAIL!!", "PASS" if ok_op else "FAIL!!"))

    # ---- 逐票差异：哪几只、哪一维 ----
    cols = ["total_new"] + ["SC_F%d" % i for i in range(1, 7)]
    m = res_fix["ts_code"].astype(str).values
    j = res_op["ts_code"].astype(str).values
    assert (m == j).all(), "两路径票序不一致"
    diff_rows = []
    for c in cols:
        d = np.abs(res_fix[c].astype(float).values - res_op[c].astype(float).values)
        for i in np.where(d > 1e-9)[0]:
            diff_rows.append((res_fix["ts_code"].iloc[i], c,
                              float(res_fix[c].iloc[i]), float(res_op[c].iloc[i])))
    print("\n[表2] 两路径逐票逐维差异（共 %d 项）" % len(diff_rows))
    for t, c, a, b in diff_rows:
        print("  %s  %s: FIX=%.1f -> OP=%.1f" % (t, c, a, b))

    # ---- 三只 58.5 同分票 + 920274 逐维明细 ----
    keys = ["000786.SZ", "920651.BJ", "001332.SZ", "920274.BJ"]
    show = ["ts_code", "prob", "mf_main_net", "main_net", "volume_ratio",
            "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]
    print("\n[表3] 关键票逐维明细")
    for name, df in (("FIX(生产)", res_fix), ("OP(复刻)", res_op)):
        print("-- %s --" % name)
        sub = df[df["ts_code"].isin(keys)][show]
        print(sub.to_string(index=False))

    # ---- F5 中性化 60 档（DE=5 vs OP=4 复核）----
    for name, df in (("FIX", res_fix), ("OP", res_op)):
        neu = df["total_new"] + SR.SC_WEIGHTS["F5"] * (5.0 - df["SC_F5"]) * 10.0
        print("[中性化] %s 路径红线 60：原始 %d -> F5中性化后 %d"
              % (name, int((df["total_new"] >= 60).sum()), int((neu >= 60).sum())))

    # ---- FIX 路径 vs 生产 CSV 逐格比对（生产未受影响验证）----
    prod = pd.read_csv(PROD_CSV, encoding="utf-8-sig", dtype={"ts_code": str})
    picks = res_fix[res_fix["total_new"] >= THRESHOLD].nlargest(TOP, "total_new")
    cols9 = ["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3",
             "SC_F4", "SC_F5", "SC_F6"]
    got = picks[cols9].copy()
    got["prob"] = got["prob"].round(4)
    got["total_new"] = got["total_new"].round(1)
    print("\n[生产核对] 生产 CSV 列数=%d 行数=%d" % (len(prod.columns), len(prod)))
    print(prod[cols9].to_string(index=False))
    print("FIX 重算：")
    print(got.to_string(index=False))
    same = len(prod) == len(got) and all(
        ((prod[c].astype(str).values == got[c].astype(str).values).all()
         if c == "ts_code"
         else np.allclose(prod[c].astype(float).values,
                          got[c].astype(float).values, rtol=0, atol=1e-9))
        for c in cols9)
    print("逐格一致 = %s" % ("PASS" if same else "FAIL!!"))

    # ---- 与 DE 中间产物交叉核对 main_net（兜底值）----
    de = pd.read_csv(DE_CSV, dtype={"ts_code": str})
    mm = res_fix.set_index("ts_code")["main_net"].astype(float)
    de_mm = de.set_index("ts_code")["main_net"].astype(float)
    common = mm.index.intersection(de_mm.index)
    dmax = float((mm.loc[common] - de_mm.loc[common]).abs().max())
    print("\n[交叉核对] FIX main_net vs DE 中间产物 max|diff| = %.6f（%d 只）"
          % (dmax, len(common)))


if __name__ == "__main__":
    main()
