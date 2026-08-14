# coding: utf-8
"""分析 ML 因子合成器的特征重要性（理解 ML 学到了什么）。

复用 ml_factor_synthesizer 的数据管线，只训练一次模型并输出特征重要性。
"""
import sys
sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_12_RPS主升浪/research")

import numpy as np
import pandas as pd
import lightgbm as lgb

# 复用 ml_factor_synthesizer 的函数
from ml_factor_synthesizer import (
    load_panel, load_finance, load_benchmark, compute_factors, build_labels,
    FACTOR_COLS,
)


def main():
    print("=== ML 因子合成器：特征重要性分析 ===\n")
    panel, ind_map = load_panel(n_codes=1500)
    codes_all = set(panel.index.get_level_values("ts_code"))
    fin_by_code = load_finance(codes_all)
    bm = load_benchmark()
    factor_snapshots, close_wide, trade_dates = compute_factors(
        panel, ind_map, fin_by_code)
    labels = build_labels(factor_snapshots, close_wide, bm)

    dates = sorted(factor_snapshots.keys())

    # 收集所有历史样本（用中间一段，避免全量）
    X_all, y_all = [], []
    for hd in dates[10:50]:  # 用 40 个调仓日训练
        if hd not in labels:
            continue
        lab = labels[hd].dropna()
        if len(lab) < 50:
            continue
        fh = factor_snapshots[hd].loc[lab.index]
        X_all.append(fh.values)
        y_all.append(lab.values)
    X = np.vstack(X_all)
    y = np.concatenate(y_all)
    print("训练样本: %d 行" % len(X))

    ds = lgb.Dataset(X, label=y)
    params = {"objective": "binary", "metric": "auc",
              "num_leaves": 31, "learning_rate": 0.05,
              "feature_fraction": 0.8, "verbose": -1}
    model = lgb.train(params, ds, num_boost_round=50)

    print("\n=== 特征重要性 ===")
    imp = pd.Series(model.feature_importance("gain"),
                    index=FACTOR_COLS).sort_values(ascending=False)
    for feat, val in imp.items():
        print("  %-10s: %8.0f  (%5.1f%%)" % (feat, val, val / imp.sum() * 100))

    # 预测 AUC（训练集内，仅供参考）
    from sklearn.metrics import roc_auc_score
    try:
        pred = model.predict(X)
        auc = roc_auc_score(y, pred)
        print("\n训练集 AUC: %.3f (分类器区分度)" % auc)
    except ImportError:
        print("\n(无 sklearn，跳过 AUC)")

    print("\n=== 解读 ===")
    print("特征重要性高的 = ML 认为最该用来组合选股的因子")


if __name__ == "__main__":
    main()
