# coding: utf-8
"""阶段2：OPTUNA 参数寻优 + 精调 + 模型保存（walk-forward 时序切分 + IC 目标）。

读取阶段0特征面板 data/feature_panel.parquet：
  1) OPTUNA 搜索核心参数，优化目标 = 验证集 IC（Spearman，排序能力），maximize
     - 搜索空间来自知乎调参资料：max_depth[3-6]、num_leaves≤2^max_depth-1、
       lambda_l1/l2 loguniform（l2 可大）、feature_fraction/bagging_fraction
     - 固定基础：learning_rate=0.05、min_child_samples 大值、early_stopping、剪枝
  2) 精调：用最优参数，learning_rate 降到 0.02，n_estimators 加大，重新训练
  3) 测试集评估：IC / ICIR / 分位收益 / 准确率 / 特征重要性
  4) 保存模型 model.txt（LightGBM 原生格式，供部署阶段加载推理）+ 报告
  5) 可选 --rolling：季度滚动重训（walk-forward），输出 IC 时间序列

用法：
  python train_optuna.py --n-trials 20                # 全量寻优
  python train_optuna.py --n-trials 3 --limit-rows 200000   # 快速验证
  python train_optuna.py --rolling --rolling-folds 6        # 附加滚动重训评估
"""
import argparse
import json
import os
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import lightgbm as lgb
import optuna
from datetime import datetime

import data_config as DC

HERE = DC.PROJECT_DIR
# 注意：LightGBM 底层在 Windows 上写文件不支持中文路径，模型统一存到英文路径（data_config.MODEL_DIR）
OUT_MODEL = DC.model_file()
OUT_REPORT = os.path.join(HERE, "data", "optuna_report.json")


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
    return float(s.mean()), float(s.std()), float(s.mean() / s.std()) if s.std() > 0 else np.nan


def train_once(params, X_tr, y_tr, X_va, y_va, trial=None, es_rounds=100):
    callbacks = [lgb.early_stopping(es_rounds, verbose=False)]
    if trial is not None:
        from optuna.integration import LightGBMPruningCallback
        callbacks.append(LightGBMPruningCallback(trial, "auc"))
    model = lgb.LGBMClassifier(**params)
    model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], callbacks=callbacks)
    return model


def build_params(trial=None):
    """返回 LightGBM 参数；trial 为 None 时返回基线默认。"""
    if trial is None:
        return dict(
            objective="binary", metric="auc",
            learning_rate=0.05, n_estimators=2000,
            max_depth=6, num_leaves=31, min_child_samples=1000,
            feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
            lambda_l1=1.0, lambda_l2=10.0,
            random_state=42, n_jobs=-1, verbose=-1,
        )
    max_depth = trial.suggest_int("max_depth", 3, 6)
    max_leaves = 2 ** max_depth - 1
    num_leaves = trial.suggest_categorical("num_leaves", [7, 15, 31, 63])
    num_leaves = min(num_leaves, max_leaves)
    return dict(
        objective="binary", metric="auc",
        learning_rate=0.05, n_estimators=3000,
        max_depth=max_depth, num_leaves=num_leaves,
        min_child_samples=trial.suggest_categorical("min_child_samples", [500, 1000, 2000]),
        feature_fraction=trial.suggest_categorical("feature_fraction", [0.5, 0.7, 0.9]),
        bagging_fraction=trial.suggest_categorical("bagging_fraction", [0.7, 0.8, 0.9]),
        bagging_freq=1,
        lambda_l1=trial.suggest_float("lambda_l1", 0.01, 100.0, log=True),
        lambda_l2=trial.suggest_float("lambda_l2", 0.1, 2000.0, log=True),
        random_state=42, n_jobs=-1, verbose=-1,
    )


def evaluate(model, feat_cols, X_te, y_te, fwd_te, panel_te):
    prob = model.predict_proba(X_te)[:, 1]
    acc = float(np.mean((prob > 0.5) == (y_te == 1)))
    ic_all = calc_ic(fwd_te, prob)
    ic_mean, ic_std, icir = daily_icir(panel_te, "prob", "ret")
    q = pd.qcut(prob, 5, labels=False, duplicates="drop")
    quant = {}
    for gid in np.unique(q):
        quant[int(gid) + 1] = float(np.nanmean(fwd_te[q == gid]))
    imp = sorted(zip(feat_cols, model.feature_importances_), key=lambda x: -x[1])[:20]
    return {
        "prob": prob, "acc": acc, "ic_all": ic_all,
        "ic_daily_mean": ic_mean, "icir": icir, "quantile_fwd_ret": quant,
        "top20_features": imp,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-trials", type=int, default=20, help="OPTUNA 迭代次数")
    ap.add_argument("--limit-rows", type=int, default=0, help="限制训练行数(调试用)")
    ap.add_argument("--panel", default="v1", choices=["v1", "v2", "v3"], help="使用 v1/v2/v3 面板")
    ap.add_argument("--panel-file", default=None, help="自定义面板 parquet 绝对路径(测试用，覆盖 --panel)")
    ap.add_argument("--meta-file", default=None, help="自定义特征 json 绝对路径(测试用)")
    ap.add_argument("--model-tag", default="", help="模型文件名后缀（候选文件，如 _enh / _retrain_20260825；缺省自动 _cand_YYYYMMDD；生产模型经 promote_model.py 提升）")
    ap.add_argument("--split-train", default="2020-01-01/2023-06-30")
    ap.add_argument("--split-valid", default="2023-07-01/2024-06-30")
    ap.add_argument("--split-test", default="2024-07-01/2026-08-14")
    ap.add_argument("--rolling", action="store_true", help="附加季度滚动重训评估")
    ap.add_argument("--rolling-folds", type=int, default=6, help="滚动折数")
    ap.add_argument("--fine-lr", type=float, default=0.02, help="精调学习率")
    args = ap.parse_args()

    suffix = "" if args.panel == "v1" else ("_v2" if args.panel == "v2" else "_v3")
    if args.panel_file:
        panel_path = args.panel_file
        meta_path = args.meta_file or os.path.join(HERE, "data", "features_v3_enh.json")
    else:
        panel_path = os.path.join(HERE, "data", f"feature_panel{suffix}.parquet")
        meta_path = os.path.join(HERE, "data", f"features{suffix}.json")

    # 【研发-生产隔离】一律输出候选文件，绝不写裸生产模型名（lgb_model_v3.txt）。
    # 生产模型只允许经 promote_model.py 显式提升。model_tag 为空时自动追加 _cand_YYYYMMDD。
    _tag = args.model_tag or "_cand_" + datetime.now().strftime("%Y%m%d")
    if args.panel_file:
        out_model = os.path.join(DC.MODEL_DIR, f"lgb_model_v3{_tag}.txt")
    else:
        out_model = os.path.join(DC.MODEL_DIR, f"lgb_model{suffix}{_tag}.txt")

    print("[1/5] 读取特征面板 ...")
    df = pd.read_parquet(panel_path)
    meta = json.load(open(meta_path, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if args.limit_rows:
        df = df.iloc[: args.limit_rows]
    print(f"    行数: {len(df):,}  特征数: {len(feat_cols)}")

    y = df["label"].astype(int).values
    fwd = df["fwd_ret"].astype(float).values
    X = df[feat_cols].astype("float32").values

    tr0, tr1 = args.split_train.split("/")
    va0, va1 = args.split_valid.split("/")
    te0, te1 = args.split_test.split("/")
    m_tr = (df["trade_date"] >= tr0) & (df["trade_date"] <= tr1)
    m_va = (df["trade_date"] >= va0) & (df["trade_date"] <= va1)
    m_te = (df["trade_date"] >= te0) & (df["trade_date"] <= te1)
    print(f"    train: {m_tr.sum():,}  valid: {m_va.sum():,}  test: {m_te.sum():,}")

    print("[2/5] OPTUNA 参数寻优（目标=验证集 IC） ...")
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        params = build_params(trial)
        model = train_once(params, X[m_tr], y[m_tr], X[m_va], y[m_va], trial=trial)
        prob_va = model.predict_proba(X[m_va])[:, 1]
        return calc_ic(fwd[m_va], prob_va)

    study = optuna.create_study(direction="maximize", study_name="lgbm_stock_ic")
    study.optimize(objective, n_trials=args.n_trials, show_progress_bar=False)
    best_params = study.best_params
    print("    最优参数:", best_params)
    print("    最优验证IC:", round(study.best_value, 5))

    print("[3/5] 精调（降学习率 + 增大树量） ...")
    fine_params = dict(
        objective="binary", metric="auc",
        learning_rate=args.fine_lr, n_estimators=12000,
        **{k: v for k, v in best_params.items() if k in
           ("max_depth", "num_leaves", "min_child_samples", "feature_fraction",
            "bagging_fraction", "bagging_freq", "lambda_l1", "lambda_l2")},
        random_state=42, n_jobs=-1, verbose=-1,
    )
    model = train_once(fine_params, X[m_tr], y[m_tr], X[m_va], y[m_va], es_rounds=400)
    print("    best_iteration =", model.best_iteration_)

    print("[4/5] 测试集评估 ...")
    te_panel = df[m_te][["trade_date"]].copy()
    te_panel["prob"] = model.predict_proba(X[m_te])[:, 1]
    te_panel["ret"] = fwd[m_te]
    res = evaluate(model, feat_cols, X[m_te], y[m_te], fwd[m_te], te_panel)
    print("    准确率(>0.5):", round(res["acc"], 4))
    print("    test IC:", round(res["ic_all"], 5), " 日均IC:", round(res["ic_daily_mean"], 5), " ICIR:", round(res["icir"], 4))
    print("    分位收益:", {k: round(v, 5) for k, v in res["quantile_fwd_ret"].items()})
    print("    Top10 特征:", res["top20_features"][:10])

    print("[5/5] 保存模型与报告 ...")
    os.makedirs(os.path.dirname(out_model), exist_ok=True)
    model.booster_.save_model(out_model, num_iteration=model.best_iteration_)
    report = {
        "optuna": {
            "n_trials": args.n_trials, "best_params": best_params, "best_valid_ic": study.best_value,
        },
        "fine_params": {k: v for k, v in fine_params.items() if k != "verbose"},
        "splits": {"train": [tr0, tr1], "valid": [va0, va1], "test": [te0, te1]},
        "metrics": {
            "accuracy_0.5": res["acc"], "ic_all": res["ic_all"],
            "ic_daily_mean": res["ic_daily_mean"], "icir": res["icir"],
            "quantile_fwd_ret": res["quantile_fwd_ret"],
        },
        "top20_features": res["top20_features"],
        "model_file": out_model,
        "verdict": (
            "PASS: test IC 显著为正，模型已保存，可进入阶段3(双轨选股接入)"
            if res["ic_all"] > 0.02
            else "CHECK: test IC 偏弱，建议检查特征或增大寻优范围"
        ),
    }

    if args.rolling:
        print("    [可选] 季度滚动重训评估 ...")
        fold_ics = {}
        fold_starts = pd.date_range("2021-01-01", "2026-03-31", freq="QS")
        for i, fs in enumerate(fold_starts):
            ve = fs + pd.DateOffset(months=3)
            te = ve + pd.DateOffset(months=3)
            if te > df["trade_date"].max():
                break
            m_r = df["trade_date"] < fs
            m_v = (df["trade_date"] >= fs) & (df["trade_date"] < ve)
            m_t = (df["trade_date"] >= ve) & (df["trade_date"] < te)
            if m_r.sum() < 10000 or m_t.sum() < 100:
                continue
            rp = dict(fine_params, learning_rate=0.05, n_estimators=3000)
            rm = train_once(rp, X[m_r], y[m_r], X[m_v], y[m_v])
            p = rm.predict_proba(X[m_t])[:, 1]
            fold_ics[str(fs.date())] = calc_ic(fwd[m_t], p)
        fics = pd.Series(fold_ics).dropna()
        report["rolling"] = {
            "fold_ic": {k: round(v, 5) for k, v in fics.items()},
            "mean_ic": float(fics.mean()),
            "std_ic": float(fics.std()),
            "icir": float(fics.mean() / fics.std()) if fics.std() > 0 else None,
        }
        print("    滚动IC:", report["rolling"])

    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print("    模型保存到:", out_model)
    print("    [隔离] 已保存为候选模型；若需上线请运行 promote_model.py --model-file <路径> --suffix v3")
    print("    报告保存到:", OUT_REPORT)


if __name__ == "__main__":
    main()
