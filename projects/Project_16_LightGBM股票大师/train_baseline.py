# coding: utf-8
"""阶段1：LightGBM 基线 + IC 评估（walk-forward 时序切分）。

读取阶段0产出的特征面板 data/feature_panel.parquet：
  - 按时间切分 train / valid / test（禁止随机K折，避免时序泄露）
  - LightGBM 基线参数 + early_stopping（资料推荐"先固定基础配置"）
  - 核心评估：IC（Spearman）、ICIR、准确率、分位收益、特征重要性
  - 决策红线：若 test IC 不为正/不显著，说明特征或标签还有问题，先别进阶段2

用法：
  python train_baseline.py
  python train_baseline.py --split-train 2020-01-01/2023-06-30 --split-valid 2023-07-01/2024-06-30 --split-test 2024-07-01/2026-08-14
"""
import argparse
import json
import os
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "data", "feature_panel.parquet")
META = os.path.join(HERE, "data", "features.json")
OUT_REPORT = os.path.join(HERE, "data", "baseline_report.json")


def calc_ic(y_true, y_pred):
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if mask.sum() < 5:
        return np.nan
    return spearmanr(y_true[mask], y_pred[mask]).correlation


def daily_ic_series(panel, prob_col, ret_col):
    """按日计算全市场截面 IC 序列（用于 ICIR）。"""
    tmp = panel[["trade_date", ret_col, prob_col]].copy()
    tmp[ret_col] = tmp[ret_col].astype(float)
    tmp[prob_col] = tmp[prob_col].astype(float)
    out = {}
    for d, g in tmp.groupby("trade_date"):
        out[d] = calc_ic(g[ret_col].values, g[prob_col].values)
    return pd.Series(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split-train", default="2020-01-01/2023-06-30")
    ap.add_argument("--split-valid", default="2023-07-01/2024-06-30")
    ap.add_argument("--split-test", default="2024-07-01/2026-08-14")
    args = ap.parse_args()

    train_rng, valid_rng, test_rng = args.split_train, args.split_valid, args.split_test
    train_rng = train_rng.split("/")
    valid_rng = valid_rng.split("/")
    test_rng = test_rng.split("/")

    print("[1/4] 读取特征面板 ...")
    df = pd.read_parquet(PANEL)
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    print(f"    行数: {len(df):,}  特征数: {len(feat_cols)}  日期: {df['trade_date'].min()} -> {df['trade_date'].max()}")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    label = df["label"].astype(int).values
    fwd_ret = df["fwd_ret"].astype(float).values
    X = df[feat_cols].astype("float32").values

    mask_tr = (df["trade_date"] >= train_rng[0]) & (df["trade_date"] <= train_rng[1])
    mask_va = (df["trade_date"] >= valid_rng[0]) & (df["trade_date"] <= valid_rng[1])
    mask_te = (df["trade_date"] >= test_rng[0]) & (df["trade_date"] <= test_rng[1])
    print(f"    train: {mask_tr.sum():,}  valid: {mask_va.sum():,}  test: {mask_te.sum():,}")

    print("[2/4] 训练 LightGBM 基线 ...")
    # 基线参数（资料"由粗到细"第一步：先固定基础配置）
    params = dict(
        objective="binary",
        metric="auc",
        learning_rate=0.05,
        n_estimators=2000,
        num_leaves=31,
        max_depth=6,
        min_child_samples=1000,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        bagging_freq=1,
        lambda_l1=1.0,
        lambda_l2=10.0,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    model = lgb.LGBMClassifier(**params)
    model.fit(
        X[mask_tr], label[mask_tr],
        eval_set=[(X[mask_va], label[mask_va])],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    best_iter = model.best_iteration_
    print(f"    best_iteration = {best_iter}")

    print("[3/4] 评估 ...")
    prob_te = model.predict_proba(X[mask_te])[:, 1]
    acc = float(np.mean((prob_te > 0.5) == (label[mask_te] == 1)))

    # IC（全样本）
    ic_all = calc_ic(fwd_ret[mask_te], prob_te)
    # 按日 IC 序列 -> ICIR
    test_panel = df[mask_te][["trade_date"]].copy()
    test_panel["prob"] = prob_te
    test_panel["ret"] = fwd_ret[mask_te]
    ic_daily = daily_ic_series(test_panel, "prob", "ret").dropna()
    ic_mean = float(ic_daily.mean())
    ic_std = float(ic_daily.std())
    icir = float(ic_mean / ic_std) if ic_std > 0 else np.nan

    # 分位收益（prob 分 5 组，看单调性）
    fwd_test_arr = fwd_ret[mask_te]
    q = pd.qcut(prob_te, 5, labels=False, duplicates="drop")
    quant_ret = {}
    for gid in np.unique(q):
        m = q == gid
        quant_ret[int(gid) + 1] = float(np.nanmean(fwd_test_arr[m]))

    # 特征重要性
    imp = sorted(zip(feat_cols, model.feature_importances_), key=lambda x: -x[1])[:20]

    print("    test AUC 隐含准确率(>0.5):", round(acc, 4))
    print("    test IC (全样本):", round(ic_all, 4))
    print("    test 日均 IC:", round(ic_mean, 4), " ICIR:", round(icir, 4))
    print("    test 分位平均收益:", {k: round(v, 5) for k, v in quant_ret.items()})
    print("    Top10 特征:", imp[:10])

    print("[4/4] 保存报告 ...")
    report = {
        "splits": {
            "train": train_rng, "valid": valid_rng, "test": test_rng,
        },
        "n_rows": {"train": int(mask_tr.sum()), "valid": int(mask_va.sum()), "test": int(mask_te.sum())},
        "best_iteration": int(best_iter),
        "params": params,
        "metrics": {
            "accuracy_0.5": acc,
            "ic_all": ic_all,
            "ic_daily_mean": ic_mean,
            "icir": icir,
            "quantile_fwd_ret": quant_ret,
        },
        "top20_features": imp,
        "verdict": (
            "PASS: 测试集 IC 显著为正，可进入阶段2(滚动训练+OPTUNA)"
            if ic_all > 0.01
            else "FAIL: 测试集 IC 未达正值，先检查特征/标签/前视偏差，再进入阶段2"
        ),
    }
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print("    报告保存到:", OUT_REPORT)


if __name__ == "__main__":
    main()
