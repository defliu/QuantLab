# coding: utf-8
"""阶段3：部署双轨选股 —— 模型推理 + F1-F6 评分卡红线 → Top5。

流程：
  1) 读取训练特征面板 data/feature_panel_v2.parquet（已含 38 特征到最新交易日）
  2) 取目标交易日（默认面板最新日）全部股票的特征
  3) 加载 LightGBM 模型 D:/QuantLab/models/lgb_model_v2.txt 推理"次日上涨概率"
  4) 用面板特征计算 F1-F6 评分卡（离线代理版，与 Project_15 评分卡口径对齐）
  5) 双轨综合：模型概率×0.6 + 评分卡总分/100×0.4，评分卡≥70 为红线 → Top5
  6) 输出控制台表格 + Markdown（data/selections/）

注意（代理说明）：
  - F1/F4/F6 由量价/估值特征离线计算，与 Project_15 口径基本一致；
  - F2(主力资金)/F3(新闻催化)/F5(板块β) 用面板量价/事件特征近似代理，
    与 TDX 实时口径存在差异，正式定股前建议用 TDX/wenda 复核这三项。

用法：
  python deploy_predict.py                      # 用面板最新交易日
  python deploy_predict.py --date 2026-08-14    # 指定日期（需在面板内）
  python deploy_predict.py --model v1           # 用 v1 量价模型
"""
import argparse
import json
import os
import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC

HERE = DC.PROJECT_DIR
DATA_DIR = DC.DATA_DIR
SELECT_DIR = os.path.join(DATA_DIR, "selections")
SNAP_PATH = os.path.join(DC.LIVE_DIR, "latest_features.parquet")

# 评分卡权重（与 Project_15 factor_framework.md 一致）
SC_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def _score_f1(r):
    """F1 位置健康度：pos_250=当前价/250日高点（越高越过热）。"""
    p = r["pos_250"]
    if np.isnan(p):
        return 5.0
    if p >= 0.99:
        return 3.0
    if p >= 0.95:
        return 5.0
    if p >= 0.88:
        return 7.0
    if p >= 0.72:
        return 9.0
    return 8.0


def _score_f2(r):
    """F2 资金认可度（离线代理：量比+换手，无主力净流入）。"""
    vr = r["volume_ratio"]
    if np.isnan(vr):
        return 5.0
    if vr >= 2.5:
        return 10.0
    if vr >= 1.5:
        return 8.0
    if vr >= 1.0:
        return 6.0
    return 4.0


def _score_f3(r):
    """F3 催化强度（离线代理：业绩预告/快报事件 + 分红）。对精简面板(v3)缺失列容错。"""
    fc_days = r.get("fc_days_since", 9999)
    ex_days = r.get("ex_days_since", 9999)
    fc_pchg = r.get("fc_pchange", 0.0)
    dv_sum = r.get("dv_year_sum", 0.0)
    s = 4.0
    if fc_days <= 90 or ex_days <= 90:
        s += 3.0
    if fc_pchg > 20:
        s += 2.0
    if dv_sum > 1.0:
        s += 1.0
    return min(10.0, s)


def _score_f4(r):
    """F4 技术形态：RSI(6) + MACD 柱。"""
    rsi, macd = r["rsi6"], r["macd_hist"]
    if np.isnan(rsi) or np.isnan(macd):
        return 5.0
    if rsi > 65 and macd > 0:
        return 10.0
    if rsi > 50 and macd > 0:
        return 7.0
    if 40 <= rsi <= 60:
        return 5.0
    if rsi < 40 or macd < 0:
        return 3.0
    return 1.0


def _score_f5(r):
    """F5 板块β联动（离线代理：相对市场动量 rel_mom_20）。"""
    rm = r["rel_mom_20"]
    if np.isnan(rm):
        return 5.0
    if rm > 0.05:
        return 8.0
    if rm > 0.02:
        return 6.0
    if rm > 0.0:
        return 5.0
    return 4.0


def _score_f6(r):
    """F6 估值/流动性合理性：PE(TTM) + 换手。"""
    pe, turn = r["pe_ttm"], r["turn_ma5"]
    if np.isnan(pe):
        return 4.0
    if 10 <= pe <= 30 and 1 <= turn <= 8:
        return 10.0
    if 5 <= pe <= 50 and 0.5 <= turn <= 12:
        return 7.0
    if pe < 0 or turn > 25:
        return 1.0
    return 4.0


def compute_scorecard(df):
    """对面板行计算 F1-F6 得分与总分。df 需包含所需特征列。"""
    sc = pd.DataFrame(index=df.index)
    sc["F1"] = df.apply(_score_f1, axis=1)
    sc["F2"] = df.apply(_score_f2, axis=1)
    sc["F3"] = df.apply(_score_f3, axis=1)
    sc["F4"] = df.apply(_score_f4, axis=1)
    sc["F5"] = df.apply(_score_f5, axis=1)
    sc["F6"] = df.apply(_score_f6, axis=1)
    total = sum(SC_WEIGHTS[f] * sc[f] for f in SC_WEIGHTS) * 10.0  # 映射到百分制(0-100)
    sc["total"] = total
    return sc


def _prefer_snapshot(df, feat_cols):
    """daily 增量快照比主面板新时改用快照（merge_live_features.py 产出，限定主面板股票域）。"""
    if not os.path.exists(SNAP_PATH):
        return df
    try:
        snap = pd.read_parquet(SNAP_PATH)
        snap["trade_date"] = pd.to_datetime(snap["trade_date"])
    except Exception as e:
        print(f"    !! 增量快照读取失败，回退主面板: {e}")
        return df
    panel_max = df["trade_date"].max()
    snap_max = snap["trade_date"].max()
    if snap_max <= panel_max:
        return df
    if not set(feat_cols).issubset(snap.columns):
        print("    !! 增量快照缺特征列，回退主面板")
        return df
    uni = set(df["ts_code"])
    before = len(snap)
    snap = snap[snap["ts_code"].isin(uni)].copy()
    print(f"    使用增量快照: {os.path.basename(SNAP_PATH)}（{snap_max.date()} > 主面板 {panel_max.date()}，股票域过滤 {before}→{len(snap)}）")
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日(YYYY-MM-DD)，默认面板最新日")
    ap.add_argument("--model", default="v3", choices=["v1", "v2", "v3"])
    ap.add_argument("--top-k", type=int, default=5, help="最终输出候选数")
    ap.add_argument("--pool-size", type=int, default=20, help="模型分预选池大小")
    ap.add_argument("--score-threshold", type=float, default=58.0, help="评分卡红线(池内,百分制)")
    ap.add_argument("--model-w", type=float, default=0.6, help="模型分权重")
    args = ap.parse_args()
    os.makedirs(SELECT_DIR, exist_ok=True)

    suffix = "" if args.model == "v1" else ("_v2" if args.model == "v2" else "_v3")
    panel_path = os.path.join(DATA_DIR, f"feature_panel{suffix}.parquet")
    meta_path = os.path.join(DATA_DIR, f"features{suffix}.json")
    model_path = DC.model_file(suffix)

    print("[1/5] 加载面板与模型 ...")
    df = pd.read_parquet(panel_path)
    meta = json.load(open(meta_path, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if args.date is None:
        df = _prefer_snapshot(df, feat_cols)
        date = df["trade_date"].max()
    else:
        date = pd.Timestamp(args.date)
    day = df[df["trade_date"] == date].copy()
    if len(day) == 0:
        print(f"    !! 面板中无 {date.date()} 的交易日数据，可用日期范围 {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
        print("    !! 若需预测更新日期，请先运行 build_features_v2.py 更新面板")
        return
    print(f"    交易日: {date.date()}  当日股票数: {len(day):,}  特征: {len(feat_cols)}")

    booster = lgb.Booster(model_file=model_path)
    print(f"    模型: {model_path}")

    print("[2/5] 模型推理（次日上涨概率） ...")
    X = day[feat_cols].astype("float32").values
    prob = booster.predict(X)  # 模型已按最优迭代数保存
    day["model_prob"] = prob

    print("[3/5] 离线代理评分卡 F1-F6 ...")
    sc = compute_scorecard(day)
    day["score_total"] = sc["total"]
    for f in SC_WEIGHTS:
        day[f"SC_{f}"] = sc[f]

    print("[4/5] 双轨综合与 Top%d 筛选 ..." % args.top_k)
    # 第一步：按模型分预选池（模型分越高越优先），在池内才施加评分卡红线
    pre_pool = int(max(args.pool_size * 5, 100))
    day = day.nlargest(pre_pool, "model_prob").copy()
    # 第二步：评分卡红线（池内过滤）
    day = day[day["score_total"] >= args.score_threshold].copy()
    if len(day) == 0:
        print("    !! 预选池内无股票通过评分卡红线，尝试降低 --score-threshold")
        return
    # 综合分 = 模型概率*权重 + 评分卡总分/100*(1-权重)
    day["combo"] = args.model_w * day["model_prob"] + (1 - args.model_w) * (day["score_total"] / 100.0)
    day = day.sort_values("combo", ascending=False)
    # 预选池：模型分 Top(pool_size) 与 综合分 Top(pool_size) 取并集后按综合分取 top_k
    pool_model = set(day.nlargest(args.pool_size, "model_prob").index)
    pool_combo = set(day.nlargest(args.pool_size, "combo").index)
    pool = day.loc[list(pool_model | pool_combo)].sort_values("combo", ascending=False).head(args.top_k)
    day = pool

    cols = ["ts_code", "model_prob", "score_total", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6", "combo"]
    out = day[cols].copy()
    out["model_prob"] = out["model_prob"].round(4)
    out["score_total"] = out["score_total"].round(1)
    out["combo"] = out["combo"].round(4)
    print(out.to_string(index=False))

    print("[5/5] 保存结果 ...")
    date_str = date.strftime("%Y%m%d")
    out["trade_date"] = date_str
    csv_path = os.path.join(SELECT_DIR, f"{date_str}_model_top{args.top_k}.csv")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    md_path = os.path.join(SELECT_DIR, f"{date_str}_selection_dual.md")
    lines = [
        f"# 双轨选股 Top{args.top_k} · {date.date()}",
        "",
        f"> 模型：{os.path.basename(model_path)}  |  评分卡阈值 ≥ {args.score_threshold:.0f}  |  综合 = 模型×{args.model_w}+评分卡×{1 - args.model_w:.1f}",
        "",
        "| 排名 | 代码 | 模型概率 | 评分卡总分 | F1位置 | F2资金 | F3催化 | F4技术 | F5板块 | F6估值 | 综合分 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (_, r) in enumerate(out.iterrows(), 1):
        lines.append(
            f"| {i} | {r['ts_code']} | {r['model_prob']:.3f} | {r['score_total']:.0f} "
            f"| {r['SC_F1']:.0f} | {r['SC_F2']:.0f} | {r['SC_F3']:.0f} | {r['SC_F4']:.0f} | {r['SC_F5']:.0f} | {r['SC_F6']:.0f} | {r['combo']:.3f} |"
        )
    lines += [
        "",
        "> ⚠️ F2(主力资金)/F3(新闻催化)/F5(板块β) 为离线代理分，正式定股前请用 TDX/wenda 实时数据复核。",
        "> 本结果仅为模型+评分卡双轨信号参考，不构成投资建议。",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    CSV:", csv_path)
    print("    MD :", md_path)


if __name__ == "__main__":
    main()
