# coding: utf-8
"""季度滚动训练评估（walk-forward）：验证模型 IC 的时间稳定性。

对每季度：用之前所有数据训练（v3 最优参数）→ 预测本季度 → 计算 IC。
输出每季度 IC + 平均 IC / ICIR，验证"滚动训练"是否必要、模型是否稳定。

用法：
  python rolling_eval.py --panel data/feature_panel_v3.parquet --meta data/features_v3.json
  python rolling_eval.py --start-fold 2021-01-01 --lr 0.05
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "rolling_eval_report.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=os.path.join(HERE, "data", "feature_panel_v3.parquet"))
    ap.add_argument("--meta", default=os.path.join(HERE, "data", "features_v3.json"))
    ap.add_argument("--params", default=os.path.join(HERE, "data", "optuna_report.json"),
                    help="v3 最优参数来源")
    ap.add_argument("--start-fold", default="2021-01-01")
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--n-estimators", type=int, default=3000)
    args = ap.parse_args()

    print("[1/3] 加载面板与参数 ...")
    panel = pd.read_parquet(args.panel)
    meta = json.load(open(args.meta, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    param_src = json.load(open(args.params, encoding="utf-8"))
    best = param_src.get("fine_params") or param_src.get("optuna", {}).get("best_params", {})
    keep = {k: v for k, v in best.items() if k in
            ("max_depth", "num_leaves", "min_child_samples", "feature_fraction",
             "bagging_fraction", "bagging_freq", "lambda_l1", "lambda_l2")}
    params = dict(objective="binary", metric="auc", learning_rate=args.lr,
                  n_estimators=args.n_estimators, random_state=42, n_jobs=-1, verbose=-1, **keep)
    print("    参数:", {k: v for k, v in params.items() if k != "verbose"})

    y = panel["label"].astype(int).values
    fwd = panel["fwd_ret"].astype(float).values
    X = panel[feat_cols].astype("float32").values
    dates = panel["trade_date"]

    print("[2/3] 季度滚动训练 ...")
    fold_starts = pd.date_range(args.start_fold, "2026-03-31", freq="QS")
    folds = []
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
        model = lgb.LGBMClassifier(**params)
        model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_va], y[m_va])],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
        prob = model.predict_proba(X[m_te])[:, 1]
        mask = np.isfinite(fwd[m_te])
        ic = spearmanr(fwd[m_te][mask], prob[mask]).correlation
        folds.append({"fold": str(fs.date()), "train_rows": int(m_tr.sum()),
                      "test_rows": int(m_te.sum()), "ic": round(float(ic), 5)})
        print(f"    {fs.date()} | train {m_tr.sum():,} | IC {ic:.5f}")

    s = pd.Series([f["ic"] for f in folds])
    mean_ic = float(s.mean())
    std_ic = float(s.std())
    icir = mean_ic / std_ic if std_ic > 0 else None
    positive = float((s > 0).mean())

    print("[3/3] 保存报告 ...")
    report = {
        "method": "季度滚动训练（walk-forward，用历史全部数据训练→预测当季）",
        "params": {k: v for k, v in params.items() if k != "verbose"},
        "folds": folds,
        "summary": {
            "n_folds": len(folds),
            "ic_mean": mean_ic,
            "ic_std": std_ic,
            "icir": icir,
            "positive_ratio": positive,
            "verdict": "稳定" if icir and icir > 0.15 else ("一般" if icir and icir > 0.05 else "不稳定"),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"    折数 {len(folds)} | 平均IC {mean_ic:.5f} | ICIR {icir} | 正IC占比 {positive:.2f}")
    print("    报告:", OUT)


if __name__ == "__main__":
    main()
