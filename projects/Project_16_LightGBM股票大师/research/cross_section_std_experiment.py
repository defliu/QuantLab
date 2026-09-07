# coding: utf-8
"""截面标准化 A/B 实验：验证「补截面标准化(rank/zscore)是否提升 LightGBM IC/ICIR」。

背景（P16_开源对标与落地建议_20260901.md 诊断①）：
  当前特征 float32 直喂 LightGBM（train_optuna.py:154 / deploy_predict.py:205），无截面标准化；
  报告主张补 per-date rank/zscore 能提升 ICIR（0.357 偏低）。

本实验：
  变体：raw（基线，现状）/ rank01（per-date 截面 rank→[0,1]）/ zscore（per-date 截面 zscore）
  口径1（main）：固定 train/valid/test 分割（同 train_optuna），es=400，报 test IC/ICIR/acc/分位
  口径2（rolling）：季度 walk-forward（同 rolling_eval），lr=0.05/n_est=3000/es=50，报各折 IC + 汇总
  PIT 安全：标准化只使用当日截面（groupby trade_date），无前视。

用法：
  python research/cross_section_std_experiment.py --mode main --limit-rows 200000   # 冒烟
  python research/cross_section_std_experiment.py --mode main
  python research/cross_section_std_experiment.py --mode rolling --start-fold 2024-01-01
"""
import argparse
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import lightgbm as lgb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(HERE, "data", "feature_panel_v3.parquet")
META = os.path.join(HERE, "data", "features_v3.json")
PARAMS = os.path.join(HERE, "data", "optuna_report.json")
OUT_MD = os.path.join(HERE, "data", "real", "cross_section_std_experiment_{mode}_20260901.md")
OUT_JSON = os.path.join(HERE, "data", "real", "cross_section_std_experiment_{mode}_20260901.json")

VARIANTS = ["raw", "rank01", "zscore"]


def calc_ic(y_true, y_pred):
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if mask.sum() < 5:
        return np.nan
    return spearmanr(y_true[mask], y_pred[mask]).correlation


def daily_icir(panel, prob_col, ret_col):
    tmp = panel[["trade_date", ret_col, prob_col]].copy()
    tmp[ret_col] = tmp[ret_col].astype(float)
    tmp[prob_col] = tmp[prob_col].astype(float)
    ics = {}
    for d, g in tmp.groupby("trade_date"):
        ics[d] = calc_ic(g[ret_col].values, g[prob_col].values)
    s = pd.Series(ics).dropna()
    if len(s) == 0 or s.std() == 0:
        return float("nan"), float("nan"), float("nan")
    return float(s.mean()), float(s.std()), float(s.mean() / s.std())


def apply_std(df, feat_cols, variant):
    """per-date 截面标准化（PIT 安全：只用当日截面）。返回 (X float32, df_copy)。"""
    cols = feat_cols
    if variant == "raw":
        return df[cols].astype("float32").values, df
    if variant == "rank01":
        out = df.groupby("trade_date")[cols].rank(pct=True)
        return out.astype("float32").values, df
    if variant == "zscore":
        g = df.groupby("trade_date")[cols]
        mu = g.transform("mean")
        sd = g.transform("std").replace(0.0, np.nan)
        out = (df[cols] - mu) / sd
        return out.astype("float32").values, df
    raise ValueError(variant)


def load_params():
    src = json.load(open(PARAMS, encoding="utf-8"))
    best = src.get("fine_params") or src.get("optuna", {}).get("best_params", {})
    keep = {k: v for k, v in best.items() if k in
            ("max_depth", "num_leaves", "min_child_samples", "feature_fraction",
             "bagging_fraction", "bagging_freq", "lambda_l1", "lambda_l2")}
    return keep


def run_main(panel, feat_cols, params, tr0, tr1, va0, va1, te0, te1, limit_rows, lr, n_est, es):
    y = panel["label"].astype(int).values
    fwd = panel["fwd_ret"].astype(float).values
    dates = panel["trade_date"].values
    m_tr = (panel["trade_date"] >= tr0) & (panel["trade_date"] <= tr1)
    m_va = (panel["trade_date"] >= va0) & (panel["trade_date"] <= va1)
    m_te = (panel["trade_date"] >= te0) & (panel["trade_date"] <= te1)
    res = {}
    for v in VARIANTS:
        X, _ = apply_std(panel, feat_cols, v)
        model = lgb.LGBMClassifier(objective="binary", metric="auc", learning_rate=lr,
                                   n_estimators=n_est, random_state=42, n_jobs=-1, verbose=-1, **params)
        model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_va], y[m_va])],
                  callbacks=[lgb.early_stopping(es, verbose=False)])
        prob = model.predict_proba(X[m_te])[:, 1]
        acc = float(np.mean((prob > 0.5) == (y[m_te] == 1)))
        ic_all = calc_ic(fwd[m_te], prob)
        ic_mean, ic_std, icir = daily_icir(panel[m_te][["trade_date", "fwd_ret"]].assign(prob=prob), "prob", "fwd_ret")
        q = pd.qcut(prob, 5, labels=False, duplicates="drop")
        quant = {}
        for gid in np.unique(q):
            quant[int(gid) + 1] = float(np.nanmean(fwd[m_te][q == gid]))
        res[v] = {"acc": round(acc, 4), "ic_all": round(ic_all, 5),
                  "ic_daily_mean": round(ic_mean, 5), "icir": round(icir, 4),
                  "quantile_fwd_ret": {k: round(vv, 5) for k, vv in quant.items()}}
        print(f"    [{v}] test IC {ic_all:.5f} | 日均IC {ic_mean:.5f} | ICIR {icir:.4f} | acc {acc:.4f}", flush=True)
    return res


def run_rolling(panel, feat_cols, params, start_fold, limit_rows):
    y = panel["label"].astype(int).values
    fwd = panel["fwd_ret"].astype(float).values
    dates = panel["trade_date"]
    fold_starts = pd.date_range(start_fold, "2026-03-31", freq="QS")
    per_variant = {v: [] for v in VARIANTS}
    X_cache = {v: None for v in VARIANTS}
    for fs in fold_starts:
        ve = fs + pd.DateOffset(months=3)
        te = ve + pd.DateOffset(months=3)
        if te > dates.max():
            break
        m_tr = dates < fs
        m_va = (dates >= fs) & (dates < ve)
        m_te = (dates >= ve) & (dates < te)
        if m_tr.sum() < 20000 or m_te.sum() < 200:
            continue
        print(f"    折 {fs.date()} | train {m_tr.sum():,} | test {m_te.sum():,}", flush=True)
        for v in VARIANTS:
            if X_cache[v] is None:
                X_cache[v], _ = apply_std(panel, feat_cols, v)
            X = X_cache[v]
            model = lgb.LGBMClassifier(objective="binary", metric="auc", learning_rate=0.05,
                                       n_estimators=3000, random_state=42, n_jobs=-1, verbose=-1, **params)
            model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_va], y[m_va])],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
            prob = model.predict_proba(X[m_te])[:, 1]
            ic = calc_ic(fwd[m_te], prob)
            per_variant[v].append(round(float(ic), 5))
            print(f"      [{v}] IC {ic:.5f}", flush=True)
    res = {}
    for v in VARIANTS:
        s = pd.Series(per_variant[v])
        mean = float(s.mean()) if len(s) else float("nan")
        std = float(s.std()) if len(s) > 1 else float("nan")
        icir = mean / std if std == std and std > 0 else float("nan")
        res[v] = {"n_folds": len(s), "ic_mean": round(mean, 5), "ic_std": round(std, 5),
                  "icir": round(icir, 4), "positive_ratio": round(float((s > 0).mean()), 3),
                  "folds": per_variant[v]}
        print(f"    [{v}] {len(s)} 折 | 平均IC {mean:.5f} | ICIR {icir:.4f} | 正IC占比 {(s > 0).mean():.2f}", flush=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["main", "rolling"], default="main")
    ap.add_argument("--limit-rows", type=int, default=0)
    ap.add_argument("--start-fold", default="2024-01-01")
    ap.add_argument("--lr", type=float, default=0.02)
    ap.add_argument("--n-estimators", type=int, default=12000)
    ap.add_argument("--es", type=int, default=400)
    args = ap.parse_args()

    print("[1/3] 加载面板 ...", flush=True)
    panel = pd.read_parquet(PANEL)
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    if args.limit_rows:
        panel = panel.iloc[: args.limit_rows].copy()
    params = load_params()
    print(f"    行 {len(panel):,} | 特征 {len(feat_cols)} | 变体 {VARIANTS}", flush=True)

    lines = [
        "# 截面标准化 A/B 实验（raw / rank01 / zscore）",
        "",
        f"> 生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 面板：feature_panel_v3.parquet（{len(panel):,} 行，{panel['trade_date'].nunique()} 日）",
        f"> 变体：raw=基线（现状直喂）；rank01=per-date 截面 rank→[0,1]；zscore=per-date 截面 zscore",
        f"> 参数：optuna_report.json fine_params（lr 0.02 / es 400 / main 口径；lr 0.05 / es 50 / rolling 口径）",
        "> PIT：标准化只使用当日截面（groupby trade_date），无前视。",
        "",
    ]

    if args.mode == "main":
        print("[2/3] main 口径（train 2020-01~2023-06 / valid 2023-07~2024-06 / test 2024-07~2026-08）...", flush=True)
        res = run_main(panel, feat_cols, params,
                       "2020-01-01", "2023-06-30", "2023-07-01", "2024-06-30", "2024-07-01", "2026-08-14",
                       args.limit_rows, args.lr, args.n_estimators, args.es)
        lines += ["## 口径1：固定分割 test（2024-07-01 ~ 2026-08-14）", "",
                  "| 变体 | IC | 日均IC | ICIR | 准确率 | Q1~Q5 分位收益 |", "|---|---|---|---|---|---|"]
        for v in VARIANTS:
            r = res[v]
            q = r["quantile_fwd_ret"]
            qs = "/".join(f"{q.get(i, float('nan')):+.4f}" for i in range(1, 6))
            lines.append(f"| {v} | {r['ic_all']} | {r['ic_daily_mean']} | {r['icir']} | {r['acc']} | {qs} |")
        lines += ["", "**结论判读**：若 rank01/zscore 的 test IC / ICIR 显著高于 raw（>0.005 / >0.05），"
                  "则支持「补截面标准化」；若持平或更差，说明树模型对标准化不敏感，需谨慎。", ""]
    else:
        print(f"[2/3] rolling 口径（季度 walk-forward，start {args.start_fold}）...", flush=True)
        res = run_rolling(panel, feat_cols, params, args.start_fold, args.limit_rows)
        lines += [f"## 口径2：季度 walk-forward（start {args.start_fold}）", "",
                  "| 变体 | 折数 | 平均IC | IC std | ICIR | 正IC占比 |", "|---|---|---|---|---|---|"]
        for v in VARIANTS:
            r = res[v]
            lines.append(f"| {v} | {r['n_folds']} | {r['ic_mean']} | {r['ic_std']} | {r['icir']} | {r['positive_ratio']} |")
        lines += ["", "**结论判读**：walk-forward 平均 IC/ICIR 最高的变体为当前数据下的更优选择。", ""]

    lines += ["", "*仅供研究，不构成投资建议。*", ""]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    out_md = OUT_MD.format(mode=args.mode)
    out_json = OUT_JSON.format(mode=args.mode)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"mode": args.mode, "variants": res}, f, ensure_ascii=False, indent=2)
    print("[3/3] 报告:", out_md, flush=True)


if __name__ == "__main__":
    main()
