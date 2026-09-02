# coding: utf-8
"""G2 模型周更重训（g2_strong_real：43 特征 = 27 v3 基础 + 6 慢变量 asof + 10 g2 增强因子）。

背景：V2.0 收益真实性评估（2026-08-31）认定 g2_strong_real 是生产主模型（1964 树 / 43 特征），
但此前只在 08-25 通宵研究里训练过一次便冻结；周更 retrain 只 promote V1.3 模型，
导致 G2 模型无重训路径。本脚本把「g2 训练面板构建 + 训练 + 门禁 + 提升 live」固化，
并写入 data/g2_live_model.json live 指针，deploy_predict_g2 / build_g2_daily 读最新 live 模型。

训练口径与 overnight_opt_20260825.py 的 g2_strong_real 完全一致：
  - 标签：N=3 对齐 adj_open_{T+4}/adj_open_{T+1}-1（label3 = fwd_ret3 > 0），前视安全用 PIT
  - 参数：lr=0.02, n_estimators=12000, max_depth=5, num_leaves=63, min_child_samples=1000,
          feature_fraction=0.9, bagging_fraction=0.8, lambda_l1=0.0667467948733595, lambda_l2=837.6092125986357
  - 切分：train 2020-01/2023-06 | valid 2023-07/2024-06 | test 2024-07/最新可训练日（随数据滚动）
门禁（--promote）：test IC >= max(0.03, 当前 live 记录 IC) 才提升；否则保留当前 live 并告警。

产出（全部 ASCII 路径，LightGBM 不写中文路径）：
  D:/QuantLab/models/lgb_model_v3_g2_strong_real_<date>_<bi>t.txt  候选 / live 模型
  data/features_v3_g2_strong_real_<date>.json                      特征 meta
  data/g2_live_model.json                                          live 指针（--promote 才写）

用法：
  python train_g2.py                               # 训练候选，不提升
  python train_g2.py --promote                     # 训练 + 门禁通过则提升为 live
  python train_g2.py --limit 50000 --max-est 300   # 快速冒烟（限制行数/树量，验证链路）
"""
import argparse
import datetime
import json
import os
import shutil
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402

ASTOCK = DC.ASTOCK_DIR
DATA = DC.DATA_DIR
MODEL_DIR = DC.MODEL_DIR
BASE_PANEL = os.path.join(DATA, "feature_panel_v3.parquet")        # 每日刷新，27 特征（到最新收盘）
ENH_PANEL = os.path.join(DATA, "feature_panel_v3_enh.parquet")     # 慢变量 asof（6 个 enh 独有特征）
G2_NAME = "g2_strong_real"

# g2_strong_real 新增的 10 个增强因子（来自 D:/astock 权威源，与 overnight_opt 同口径）
LHB_FEATS = ["lhb_net", "lhb_count"]
NORTH_FEATS = ["north_chg"]
RC_FEATS = ["rc_rating", "rc_num"]
MF_FEATS = ["mf_main_net", "mf_elg_net", "mf_main_ratio"]
IND_FEATS = ["ind_pct_ths", "ind_net_ths"]
G2_EXTRA = LHB_FEATS + NORTH_FEATS + RC_FEATS + MF_FEATS + IND_FEATS

# 训练参数（08-25 通宵寻优最优）
PARAMS = dict(
    objective="binary", metric="auc", learning_rate=0.02, n_estimators=12000,
    max_depth=5, num_leaves=63, min_child_samples=1000, feature_fraction=0.9,
    bagging_fraction=0.8, lambda_l1=0.0667467948733595, lambda_l2=837.6092125986357,
    random_state=42, n_jobs=-1, verbose=-1,
)


def log(msg):
    print(msg, flush=True)


def build_panel(limit_rows=0, max_date=None):
    """构建 g2 训练面板：27 v3 基础（fresh） + 6 enh 慢变量（asof） + 10 g2 增强因子 + N3 标签。"""
    log("[1/4] 读取 v3 基础面板（每日刷新）...")
    p = pd.read_parquet(BASE_PANEL)
    p["trade_date"] = pd.to_datetime(p["trade_date"])
    meta_v3 = json.load(open(os.path.join(DATA, "features_v3.json"), encoding="utf-8"))
    base_feat = meta_v3["feature_cols"]
    log(f"    v3 基础 {len(p):,} 行, 特征 {len(base_feat)}（最新 {p['trade_date'].max().date()}）")

    # ---- 6 个 enh 独有慢变量：feature_panel_v3_enh asof（每股最后可用值）----
    log("[2/4] 并入 enh 慢变量（asof）...")
    enh = pd.read_parquet(ENH_PANEL, columns=["trade_date", "ts_code"] +
                          [c for c in ["dv_year_sum", "ex_days_since", "ex_yoy", "fc_pchange",
                                       "industry_mom20", "turnover_rank"] if c in pd.read_parquet(ENH_PANEL).columns])
    enh["trade_date"] = pd.to_datetime(enh["trade_date"])
    enh_feats = [c for c in enh.columns if c not in ("trade_date", "ts_code")]
    enh_last = enh.sort_values("trade_date").groupby("ts_code").tail(1).set_index("ts_code")
    p = p.merge(enh_last[enh_feats], left_on="ts_code", right_index=True, how="left")
    log(f"    enh 慢变量 {len(enh_feats)} 个（asof {enh['trade_date'].max().date()}）")

    # ---- 10 个 g2 增强因子：龙虎榜/北向/研报/真实F2/同花顺板块 ----
    log("[3/4] 构建 g2 增强因子（lhb/north/rc/mf/ths）...")
    # 龙虎榜
    tl = pd.read_parquet(os.path.join(ASTOCK, "lhb", "top_list.parquet")).reset_index()
    tl["trade_date"] = pd.to_datetime(tl["trade_date"])
    g = tl.groupby(["ts_code", "trade_date"]).agg(lhb_net=("net_amount", "sum"), lhb_count=("net_amount", "size")).reset_index()
    p = p.merge(g, on=["ts_code", "trade_date"], how="left")
    p["lhb_net"] = p["lhb_net"].fillna(0.0)
    p["lhb_count"] = p["lhb_count"].fillna(0.0)
    # 北向
    hk = pd.read_parquet(os.path.join(ASTOCK, "northbound", "hk_hold_full.parquet"))
    hk["trade_date"] = pd.to_datetime(hk["trade_date"])
    hk = hk[["ts_code", "trade_date", "ratio"]].rename(columns={"ratio": "north_ratio"})
    hk = hk.sort_values(["ts_code", "trade_date"])
    hk["north_chg"] = hk.groupby("ts_code")["north_ratio"].diff()
    p = p.merge(hk[["ts_code", "trade_date", "north_chg"]], on=["ts_code", "trade_date"], how="left")
    # 研报
    rc = pd.read_parquet(os.path.join(ASTOCK, "research", "report_rc_daily.parquet")).reset_index()
    rc["trade_date"] = pd.to_datetime(rc["report_date"])
    rc["rc_rating_up"] = rc["rating"].map({"买入": 2, "增持": 1, "持有": 0, "中性": -1, "减持": -2, "卖出": -3})
    rcg = rc.groupby(["ts_code", "trade_date"]).agg(rc_num=("rating", "size"), rc_rating=("rc_rating_up", "mean")).reset_index()
    p = p.merge(rcg, on=["ts_code", "trade_date"], how="left")
    p["rc_num"] = p["rc_num"].fillna(0)
    # 真实 F2（moneyflow 五档）
    mf = pd.read_parquet(os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet")).reset_index()
    mf["trade_date"] = pd.to_datetime(mf["trade_date"])
    mf["mf_main_net"] = mf["buy_lg_amount"] + mf["buy_elg_amount"] - mf["sell_lg_amount"] - mf["sell_elg_amount"]
    mf["mf_elg_net"] = mf["buy_elg_amount"] - mf["sell_elg_amount"]
    tot = mf[["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]].sum(axis=1)
    mf["mf_main_ratio"] = np.where(tot > 0, mf["mf_main_net"] / tot, np.nan)
    p = p.merge(mf[["ts_code", "trade_date", "mf_main_net", "mf_elg_net", "mf_main_ratio"]],
                on=["ts_code", "trade_date"], how="left")
    # 同花顺板块（F5 修复）
    comp = pd.read_parquet(os.path.join(
        ASTOCK, "board", "source", "parquet行业概念板块全量更新到20260814",
        "parquet", "行业概念板块", "行业板块成分汇总_同花顺.parquet"))
    comp["股票代码"] = comp["股票代码"].astype(str)
    comp["指数代码"] = comp["指数代码"].astype(str)
    stk2ind = comp.set_index("股票代码")["指数代码"].to_dict()
    p["ths_ind"] = p["ts_code"].map(stk2ind)
    td = pd.read_parquet(os.path.join(ASTOCK, "board", "ths_daily.parquet")).reset_index()
    td["trade_date"] = pd.to_datetime(td["trade_date"])
    td["ts_code"] = td["ts_code"].astype(str)
    td = td[td["ts_code"].str.startswith("881")]
    ind_pct = td[["trade_date", "ts_code", "pct_change"]].rename(
        columns={"ts_code": "ths_ind", "pct_change": "ind_pct_ths"})
    p = p.merge(ind_pct, on=["trade_date", "ths_ind"], how="left")
    iths = pd.read_parquet(os.path.join(ASTOCK, "board_fundflow", "moneyflow_ind_ths.parquet")).reset_index()
    iths["trade_date"] = pd.to_datetime(iths["trade_date"])
    iths["ts_code"] = iths["ts_code"].astype(str)
    ind_net = iths[["trade_date", "ts_code", "net_amount"]].rename(
        columns={"ts_code": "ths_ind", "net_amount": "ind_net_ths"})
    p = p.merge(ind_net, on=["trade_date", "ths_ind"], how="left")

    # ---- N=3 对齐标签（adj_open_{T+4}/adj_open_{T+1}-1）----
    log("[4/4] 构建 N=3 对齐标签 ...")
    daily = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet"),
                            columns=["open", "adj_factor"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["adj_open"] = daily["open"] * daily["adj_factor"]
    ao = daily.set_index(["ts_code", "trade_date"])["adj_open"].sort_index()
    op1 = ao.groupby(level=0).shift(-1)
    op4 = ao.groupby(level=0).shift(-4)
    fwd3 = op4 / op1 - 1.0
    lbl3 = (fwd3 > 0.0).astype("int8")
    fwd_map = pd.DataFrame({
        "trade_date": ao.index.get_level_values(1),
        "ts_code": ao.index.get_level_values(0),
        "fwd_ret3": fwd3.values, "label3": lbl3.values})
    fwd_map["trade_date"] = pd.to_datetime(fwd_map["trade_date"])
    p = p.merge(fwd_map, on=["ts_code", "trade_date"], how="left")
    log(f"    N3 标签覆盖率 {p['label3'].notna().mean():.4f}")

    if max_date:
        p = p[p["trade_date"] <= pd.Timestamp(max_date)]
    if limit_rows:
        p = p.iloc[: limit_rows]
    feat = list(dict.fromkeys(base_feat + enh_feats + [c for c in G2_EXTRA if c in p.columns]))
    return p, feat


def train_g2_strong_real(panel, feat, max_est=12000):
    import lightgbm as lgb
    from scipy.stats import spearmanr

    d = panel.dropna(subset=["label3", "fwd_ret3"]).copy()
    X = d[feat].astype("float32").values
    y = d["label3"].astype(int).values
    fwd = d["fwd_ret3"].astype(float).values
    m_tr = (d["trade_date"] >= "2020-01-01") & (d["trade_date"] <= "2023-06-30")
    m_va = (d["trade_date"] >= "2023-07-01") & (d["trade_date"] <= "2024-06-30")
    m_te = (d["trade_date"] >= "2024-07-01")
    log(f"    train {m_tr.sum():,}  valid {m_va.sum():,}  test {m_te.sum():,}")

    params = dict(PARAMS, n_estimators=max_est)
    model = lgb.LGBMClassifier(**params)
    model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_va], y[m_va])],
              callbacks=[lgb.early_stopping(400, verbose=False)])
    bi = model.best_iteration_
    prob = model.predict_proba(X[m_te])[:, 1]
    msk = np.isfinite(fwd[m_te]) & np.isfinite(prob)
    ic = spearmanr(fwd[m_te][msk], prob[msk]).correlation
    log(f"    best_iteration={bi}  test IC={ic:.5f}")
    # 分位收益（test，验证方向）
    q = pd.qcut(pd.Series(prob), 5, labels=False, duplicates="drop")
    qret = {}
    for gid in np.unique(q):
        qret[int(gid) + 1] = float(np.nanmean(fwd[m_te][q.values == gid]))
    log(f"    分位收益: { {k: round(v, 5) for k, v in qret.items()} }")
    return model, bi, ic, qret


def load_live_info():
    """读取当前 live G2 的 test IC（无记录则用初始参考 0.048）。"""
    if os.path.exists(DC.G2_LIVE_POINTER):
        try:
            d = json.load(open(DC.G2_LIVE_POINTER, encoding="utf-8"))
            return d.get("test_ic", 0.048)
        except Exception:
            pass
    return 0.048


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true", help="门禁通过则提升为 live")
    ap.add_argument("--limit", type=int, default=0, help="限制面板行数（冒烟测试）")
    ap.add_argument("--max-est", type=int, default=12000, help="最大树数（冒烟测试用小值）")
    ap.add_argument("--max-date", default=None, help="截断日期 YYYY-MM-DD（测试用）")
    args = ap.parse_args()

    os.makedirs(MODEL_DIR, exist_ok=True)
    panel, feat = build_panel(limit_rows=args.limit, max_date=args.max_date)
    log(f"特征数 {len(feat)} = 27 v3 基础 + 6 enh + 10 g2 增强")

    model, bi, ic, qret = train_g2_strong_real(panel, feat, max_est=args.max_est)

    # ---- 保存候选 ----
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    fname = f"lgb_model_v3_{G2_NAME}_{stamp}_{bi}t.txt"
    out_ascii = os.path.join(MODEL_DIR, fname)
    model.booster_.save_model(out_ascii, num_iteration=bi)
    fjson = os.path.join(DATA, f"features_v3_{G2_NAME}_{stamp}.json")
    json.dump({"feature_cols": feat}, open(fjson, "w", encoding="utf-8"), ensure_ascii=False)
    # 研究目录留一份副本（保留历史）
    cand_dir = os.path.join(PROJ, "versions", "models")
    os.makedirs(cand_dir, exist_ok=True)
    shutil.copy2(out_ascii, os.path.join(cand_dir, fname))
    log(f"候选模型 -> {out_ascii}")

    # ---- 门禁与提升 ----
    live_ic = load_live_info()
    gate_ic = max(0.03, live_ic)
    passed = ic >= gate_ic
    log(f"门禁: test IC {ic:.5f} >= max(0.03, 当前live {live_ic:.5f})={gate_ic:.5f}  => {'PASS' if passed else 'FAIL'}")
    if not args.promote:
        log("[未提升] 仅训练候选（未传 --promote）")
        return 0

    if not passed:
        log(f"!! [门禁未过] 候选 IC {ic:.5f} < {gate_ic:.5f}，不提升；当前 live 保持不变（{DC.G2_LIVE_POINTER}）")
        return 1

    pointer = {
        "model_path": out_ascii, "meta_path": fjson,
        "trained_date": stamp, "trees": bi, "test_ic": round(ic, 5),
        "quantile_fwd_ret": {str(k): round(v, 5) for k, v in qret.items()},
        "promoted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "g2_strong_real 周更重训 live 指针；由 train_g2.py --promote 写入",
    }
    with open(DC.G2_LIVE_POINTER, "w", encoding="utf-8") as f:
        json.dump(pointer, f, ensure_ascii=False, indent=2)
    log(f"[提升成功] G2 live 已更新 -> {out_ascii}（IC={ic:.5f}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
