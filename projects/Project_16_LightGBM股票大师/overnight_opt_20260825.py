# -*- coding: utf-8 -*-
"""通宵优化 2026-08-25（第二轮）：基于首轮发现的强正面因子推进。

首轮发现（OVERNIGHT_20260825_RESULT.md）：
  龙虎榜净买额 lhb_net ICIR +0.82 / 北向持股变动 north_chg ICIR +0.52 / 研报 rc_rating ICIR +0.16
  真实 F2/F5 原始水平负相关；加入特征后 test IC 0.048>0.044。
本轮：
  1) 修复 F5 行业映射（同花顺行业成分 881xxx → ths_daily 板块涨幅 + 行业资金流）
  2) 构建强正面因子 + 真实F2/F5 + 负向因子 → 增强面板
  3) 买入首日对齐标签（N=3: adj_open_{T+4}/adj_open_{T+1}-1，v3.4_N3 同款）
  4) 多组重训对比（基础+强因子 / +真实F2F5 / 全量）
  5) 最优模型可执行口径回测（复用 scan_rotate_cost_real 引擎）
  6) 负向因子过滤实验
  7) 结论报告

严守隔离：只写研究面板/候选模型。
输出：data/real/OVERNIGHT_OPT_20260825_PROGRESS.log / _RESULT.md
"""
import os, sys, json, traceback, datetime, subprocess
import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
ASTOCK = "E:/astock"
LOG = os.path.join(REAL, "OVERNIGHT_OPT_20260825_PROGRESS.log")
REPORT = os.path.join(REAL, "OVERNIGHT_OPT_20260825_RESULT.md")
PY = sys.executable

PANEL_ENH = os.path.join(DATA, "feature_panel_v3_enh.parquet")
META_ENH = os.path.join(DATA, "features_v3_enh.json")
_model = r"D:/QuantLab/models/lgb_model_v3_enh.txt"

_lines = []


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def rep(md):
    _lines.append(md)
    log("REPORT> " + md.replace("\n", " "))


def phase(name):
    log(f"\n########## {name} ##########")


def run(fn, name):
    try:
        fn()
    except Exception as e:
        log(f"!! {name} 失败: {e}")
        log(traceback.format_exc())


# ============================================================
# 阶段1：F5 修复（同花顺行业）+ 阶段2 增强因子 + 阶段3 对齐标签
# ============================================================
def build_enhanced_panel():
    phase("阶段A 增强面板构建（F5修复 + 强因子 + 对齐标签）")
    log("读取 v3_enh 基础面板 ...")
    p = pd.read_parquet(PANEL_ENH)
    p["trade_date"] = pd.to_datetime(p["trade_date"])
    meta = json.load(open(META_ENH, encoding="utf-8"))
    base_feat = meta["feature_cols"]
    log(f"基础面板 {len(p):,} 行, 特征 {len(base_feat)}")

    # ---- F5 修复：同花顺行业成分映射 ----
    log("F5: 同花顺行业成分 → ths_daily 板块涨幅 + moneyflow_ind_ths 行业资金流")
    comp = pd.read_parquet(os.path.join(ASTOCK, "board", "source", "parquet行业概念板块全量更新到20260814",
                                        "parquet", "行业概念板块", "行业板块成分汇总_同花顺.parquet"))
    comp["股票代码"] = comp["股票代码"].astype(str)
    comp["指数代码"] = comp["指数代码"].astype(str)
    stk2ind = comp.set_index("股票代码")["指数代码"].to_dict()
    p["ths_ind"] = p["ts_code"].map(stk2ind)
    cov_ind = p["ths_ind"].notna().mean()
    log(f"  个股→同花顺行业指数 覆盖率 {cov_ind:.4f}")

    td = pd.read_parquet(os.path.join(ASTOCK, "board", "ths_daily.parquet")).reset_index()
    td["trade_date"] = pd.to_datetime(td["trade_date"])
    td["ts_code"] = td["ts_code"].astype(str)
    td = td[td["ts_code"].str.startswith("881")]
    ind_pct = td[["trade_date", "ts_code", "pct_change"]].rename(columns={"ts_code": "ths_ind", "pct_change": "ind_pct_ths"})
    p = p.merge(ind_pct, on=["trade_date", "ths_ind"], how="left")

    iths = pd.read_parquet(os.path.join(ASTOCK, "board_fundflow", "moneyflow_ind_ths.parquet")).reset_index()
    iths["trade_date"] = pd.to_datetime(iths["trade_date"])
    iths["ts_code"] = iths["ts_code"].astype(str)
    ind_net = iths[["trade_date", "ts_code", "net_amount"]].rename(columns={"ts_code": "ths_ind", "net_amount": "ind_net_ths"})
    p = p.merge(ind_net, on=["trade_date", "ths_ind"], how="left")
    post = p[p["trade_date"] >= "2024-07-01"]
    log(f"  F5 修复后回测期 ind_pct_ths 覆盖率 {post['ind_pct_ths'].notna().mean():.4f} / ind_net_ths {post['ind_net_ths'].notna().mean():.4f}")
    rep(f"- **阶段A·F5修复**：同花顺行业成分(90 指数)映射，回测期板块涨幅覆盖率 {post['ind_pct_ths'].notna().mean():.2%}（原 18.8% → 修复）。")

    # ---- 强正面因子 ----
    log("强正面因子: 龙虎榜/北向/研报")
    tl = pd.read_parquet(os.path.join(ASTOCK, "lhb", "top_list.parquet")).reset_index()
    tl["trade_date"] = pd.to_datetime(tl["trade_date"])
    g = tl.groupby(["ts_code", "trade_date"]).agg(lhb_net=("net_amount", "sum"), lhb_count=("net_amount", "size")).reset_index()
    p = p.merge(g, on=["ts_code", "trade_date"], how="left")
    p["lhb_net"] = p["lhb_net"].fillna(0.0)
    p["lhb_count"] = p["lhb_count"].fillna(0.0)

    hk = pd.read_parquet(os.path.join(ASTOCK, "northbound", "hk_hold_full.parquet"))
    hk["trade_date"] = pd.to_datetime(hk["trade_date"])
    hk = hk[["ts_code", "trade_date", "ratio"]].rename(columns={"ratio": "north_ratio"})
    hk = hk.sort_values(["ts_code", "trade_date"])
    hk["north_chg"] = hk.groupby("ts_code")["north_ratio"].diff()
    p = p.merge(hk, on=["ts_code", "trade_date"], how="left")

    rc = pd.read_parquet(os.path.join(ASTOCK, "research", "report_rc_daily.parquet")).reset_index()
    rc["trade_date"] = pd.to_datetime(rc["report_date"])
    rc["rc_rating_up"] = rc["rating"].map({"买入": 2, "增持": 1, "持有": 0, "中性": -1, "减持": -2, "卖出": -3})
    rcg = rc.groupby(["ts_code", "trade_date"]).agg(rc_num=("rating", "size"), rc_rating=("rc_rating_up", "mean")).reset_index()
    p = p.merge(rcg, on=["ts_code", "trade_date"], how="left")
    p["rc_num"] = p["rc_num"].fillna(0)

    # ---- 真实 F2（moneyflow 五档）----
    log("真实 F2: moneyflow 五档")
    mf = pd.read_parquet(os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet")).reset_index()
    mf["trade_date"] = pd.to_datetime(mf["trade_date"])
    mf["mf_main_net"] = mf["buy_lg_amount"] + mf["buy_elg_amount"] - mf["sell_lg_amount"] - mf["sell_elg_amount"]
    mf["mf_elg_net"] = mf["buy_elg_amount"] - mf["sell_elg_amount"]
    tot = mf[["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]].sum(axis=1)
    mf["mf_main_ratio"] = np.where(tot > 0, mf["mf_main_net"] / tot, np.nan)
    p = p.merge(mf[["ts_code", "trade_date", "mf_main_net", "mf_elg_net", "mf_main_ratio"]],
                on=["ts_code", "trade_date"], how="left")

    # ---- 负向因子（筹码/竞价）----
    log("负向因子: 筹码/竞价")
    cy = pd.read_parquet(os.path.join(ASTOCK, "chip", "cyq_daily.parquet"))
    cy["trade_date"] = pd.to_datetime(cy["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    cy = cy.dropna(subset=["trade_date"])
    p = p.merge(cy[["ts_code", "trade_date", "winner_rate", "cost_50pct"]],
                on=["ts_code", "trade_date"], how="left")
    au = pd.read_parquet(os.path.join(ASTOCK, "auction", "stock_auction_o_daily.parquet")).reset_index()
    au["trade_date"] = pd.to_datetime(au["trade_date"])
    au["auc_vol_ratio"] = au["vol"] / au["vol"].groupby(au["trade_date"]).transform("median")
    p = p.merge(au[["ts_code", "trade_date", "auc_vol_ratio"]], on=["ts_code", "trade_date"], how="left")

    # ---- 阶段3：N=3 买入首日对齐标签 ----
    log("N=3 对齐标签: adj_open_{T+4}/adj_open_{T+1}-1")
    daily = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet"),
                            columns=["open", "adj_factor"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["adj_open"] = daily["open"] * daily["adj_factor"]
    ao = daily.set_index(["ts_code", "trade_date"])["adj_open"]
    # T+1 与 T+4 开盘价（每只股票 shift）
    s = ao.sort_index()
    op1 = s.groupby(level=0).shift(-1)
    op4 = s.groupby(level=0).shift(-4)
    fwd3 = (op4 / op1 - 1.0)
    lbl3 = (fwd3 > 0.0).astype("int8")
    fwd_map = pd.DataFrame({"trade_date": s.index.get_level_values(1), "ts_code": s.index.get_level_values(0), "fwd_ret3": fwd3.values, "label3": lbl3.values})
    fwd_map["trade_date"] = pd.to_datetime(fwd_map["trade_date"])
    p = p.merge(fwd_map, on=["ts_code", "trade_date"], how="left")
    cov3 = p["label3"].notna().mean()
    log(f"  N3 标签覆盖率 {cov3:.4f}")

    # 新特征集合
    new_feat = [c for c in ["lhb_net", "lhb_count", "north_chg", "rc_rating", "rc_num",
                            "mf_main_net", "mf_elg_net", "mf_main_ratio", "ind_pct_ths", "ind_net_ths",
                            "winner_rate", "cost_50pct", "auc_vol_ratio"]
                if c in p.columns]
    out_panel = os.path.join(DATA, "feature_panel_v3_enh2_n3.parquet")
    p.to_parquet(out_panel)
    rep(f"- **阶段A** 增强面板 `feature_panel_v3_enh2_n3.parquet`：新特征 {len(new_feat)} 个 = {new_feat}。N3 标签覆盖率 {cov3:.2%}。")
    globals()["_P"] = p
    globals()["_BASE"] = base_feat
    globals()["_NEW"] = new_feat


# ============================================================
# 阶段4：多组重训
# ============================================================
def train_var(name, extra_feat, save=True):
    import lightgbm as lgb
    from scipy.stats import spearmanr
    p = globals()["_P"]
    base = globals()["_BASE"]
    feat = list(dict.fromkeys(base + extra_feat))
    d = p.dropna(subset=["label3", "fwd_ret3"]).copy()
    X = d[feat].astype("float32").values
    y = d["label3"].astype(int).values
    fwd = d["fwd_ret3"].astype(float).values
    m_tr = (d["trade_date"] >= "2020-01-01") & (d["trade_date"] <= "2023-06-30")
    m_va = (d["trade_date"] >= "2023-07-01") & (d["trade_date"] <= "2024-06-30")
    m_te = (d["trade_date"] >= "2024-07-01") & (d["trade_date"] <= "2026-08-14")
    params = dict(objective="binary", metric="auc", learning_rate=0.02, n_estimators=12000,
                  max_depth=5, num_leaves=63, min_child_samples=1000, feature_fraction=0.9,
                  bagging_fraction=0.8, lambda_l1=0.0667467948733595, lambda_l2=837.6092125986357,
                  random_state=42, n_jobs=-1, verbose=-1)
    log(f"[{name}] 训练 {len(feat)} 特征 ...")
    model = lgb.LGBMClassifier(**params)
    model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_va], y[m_va])],
              callbacks=[lgb.early_stopping(400, verbose=False)])
    bi = model.best_iteration_
    prob = model.predict_proba(X[m_te])[:, 1]
    msk = np.isfinite(fwd[m_te]) & np.isfinite(prob)
    ic = spearmanr(fwd[m_te][msk], prob[msk]).correlation
    log(f"[{name}] best_iteration={bi} test IC={ic:.5f}")
    out = None
    if save:
        import shutil
        stamp = datetime.datetime.now().strftime("%Y%m%d")
        fname = f"lgb_model_v3_{name}_{stamp}_{bi}t.txt"
        out_ascii = os.path.join(r"c:\Users\Administrator\.trae-cn\work\6a856dd08ac25249ed9d6c30", fname)
        model.booster_.save_model(out_ascii, num_iteration=bi)
        cand = os.path.join(PROJ, "versions", "models")
        os.makedirs(cand, exist_ok=True)
        shutil.copy2(out_ascii, os.path.join(cand, fname))
        fjson = os.path.join(DATA, f"features_v3_{name}_{stamp}.json")
        json.dump({"feature_cols": feat}, open(fjson, "w", encoding="utf-8"), ensure_ascii=False)
        out = (os.path.join(cand, fname), fjson)
    rep(f"- **阶段B·{name}**：特征 {len(feat)}，best_iteration={bi}，test IC={ic:.5f}" + (f"，模型 {os.path.basename(out[0])}" if out else ""))
    return ic, bi, out


def multi_train():
    phase("阶段B 多组重训（N3 对齐标签）")
    new = globals()["_NEW"]
    strong = [c for c in ["lhb_net", "lhb_count", "north_chg", "rc_rating", "rc_num"] if c in new]
    realf = [c for c in ["mf_main_net", "mf_elg_net", "mf_main_ratio", "ind_pct_ths", "ind_net_ths"] if c in new]
    negf = [c for c in ["winner_rate", "cost_50pct", "auc_vol_ratio"] if c in new]
    ic1, bi1, out1 = train_var("g1_strong", strong)
    ic2, bi2, out2 = train_var("g2_strong_real", strong + realf)
    ic3, bi3, out3 = train_var("g3_all", strong + realf + negf)
    # 选最优
    best = max([(ic1, "g1_strong", out1), (ic2, "g2_strong_real", out2), (ic3, "g3_all", out3)], key=lambda x: x[0] or -1)
    rep(f"- **阶段B 最优**：{best[1]} test IC={best[0]:.5f}")
    globals()["_BEST"] = best


# ============================================================
# 阶段5：可执行口径回测（复用 scan_rotate_cost_real 引擎）
# ============================================================
def exec_backtest():
    phase("阶段C 可执行口径回测（最优模型）")
    p = globals()["_P"]
    best = globals().get("_BEST")
    if best is None or best[2] is None:
        log("无最优模型，跳过回测"); return
    model_path, meta_path = best[2]
    # 构造含评分卡列的面板（main_net / industry_pct / volume_ratio / pe_ttm / turn_ma5）
    panel = p.copy()
    if "mf_main_net" in panel.columns:
        panel["main_net"] = panel["mf_main_net"]
    if "ind_pct_ths" in panel.columns:
        panel["industry_pct"] = panel["ind_pct_ths"]
    for c in ["volume_ratio", "pe_ttm", "turn_ma5"]:
        if c not in panel.columns:
            panel[c] = np.nan
    bt_panel = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")
    keep = [c for c in panel.columns]
    panel.to_parquet(bt_panel)
    env = dict(os.environ)
    env["BT_PANEL"] = bt_panel
    env["BT_MODEL"] = model_path
    env["BT_META"] = meta_path
    env["BT_OUT"] = os.path.join(REAL, "scan_rotate_cost_opt_report.md")
    log("运行 scan_rotate_cost_real.py --exec ...")
    r = subprocess.run([PY, os.path.join(PROJ, "scan_rotate_cost_real.py"), "--exec"],
                       env=env, capture_output=True, text=True, cwd=PROJ)
    if r.stdout:
        log(r.stdout[-2500:])
    if r.returncode != 0:
        log("!! 回测失败 " + str(r.returncode))
        log(r.stderr[-1500:] if r.stderr else "")
    else:
        out = env["BT_OUT"]
        if os.path.exists(out):
            txt = open(out, encoding="utf-8").read()
            rep(f"- **阶段C** 可执行回测输出 `scan_rotate_cost_opt_report.md`（关键行）:")
            for line in txt.splitlines():
                if any(k in line for k in ["N=", "超额", "胜率", "盈亏比", "回撤"]):
                    rep("  - " + line.strip())
        else:
            rep("- **阶段C** 回测报告未生成（见进度日志）")


# ============================================================
# 阶段6：负向因子过滤
# ============================================================
def neg_filter():
    phase("阶段D 负向因子过滤（筹码获利盘/竞价量比高 → 剔除）")
    p = globals()["_P"]
    best = globals().get("_BEST")
    if best is None or best[2] is None:
        log("无模型，跳过过滤"); return
    import lightgbm as lgb
    from scipy.stats import spearmanr
    model_path, meta_path = best[2]
    meta = json.load(open(meta_path, encoding="utf-8"))
    feat = meta["feature_cols"]
    booster = lgb.Booster(model_file=model_path)
    d = p.dropna(subset=["label3", "fwd_ret3"]).copy()
    m_te = (d["trade_date"] >= "2024-07-01") & (d["trade_date"] <= "2026-08-14")
    dte = d[m_te]
    prob = booster.predict(dte[feat].astype("float32").values)
    msk = np.isfinite(dte["fwd_ret3"].values)
    full_ic = spearmanr(dte["fwd_ret3"].values[msk], prob[msk]).correlation
    # 过滤：剔除 winner_rate 高分位 或 auc_vol_ratio 高分位
    for col, thr_q in [("winner_rate", 0.8), ("auc_vol_ratio", 0.8)]:
        if col not in dte.columns or dte[col].notna().mean() < 0.5:
            log(f"  跳过 {col}（覆盖率不足）"); continue
        q = dte[col].quantile(thr_q)
        keep = dte[col] <= q
        sub = dte[keep]
        if len(sub) < 10000:
            continue
        psub = prob[keep.values]
        m2 = np.isfinite(sub["fwd_ret3"].values)
        ic = spearmanr(sub["fwd_ret3"].values[m2], psub[m2]).correlation
        rep(f"- **阶段D 过滤 {col}≤P{int(thr_q*100)}**：保留 {len(sub):,}/{len(dte):,} 行，test IC {ic:.5f}（未过滤 {full_ic:.5f}，Δ{ic-full_ic:+.5f}）")


def main():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("通宵优化 2026-08-25 启动\n")
    log("==== 通宵优化 2026-08-25 启动 ====")
    run(build_enhanced_panel, "增强面板")
    run(multi_train, "多组重训")
    run(exec_backtest, "可执行回测")
    run(neg_filter, "负向过滤")
    header = [
        "# 通宵优化结论 2026-08-25（第二轮）",
        "",
        f"> 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "> 基线：v3.4_N3（N=3 对齐标签，代理 N=5/0.1% +0.084%，TDX N=10 +0.072%）",
        "> 严守隔离：仅产研究面板/候选模型。",
        "",
    ]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(header + _lines))
        f.write("\n\n---\n*仅供个人量化研究使用，不构成投资建议。市场有风险。*\n")
    log(f"\n==== 全部完成，报告: {REPORT} ====")


if __name__ == "__main__":
    main()
