# -*- coding: utf-8 -*-
"""通宵任务 2026-08-25：真实数据复核 + 新因子研究。

背景：已采购并整合真实数据到 E:/astock（moneyflow五档/板块资金流/龙虎榜/竞价/研报/筹码/北向）。
本脚本复核「之前数据不全时做的 F2/F5 真实化实验」，并用新数据做衍生因子 IC 研究。

严守研发-生产隔离：只写候选文件/研究面板，绝不碰 V1.1 正式模型/面板/脚本。

输出：
  data/real/OVERNIGHT_20260825_PROGRESS.log  分阶段进度
  data/real/OVERNIGHT_20260825_RESULT.md     最终结论报告
"""
import os, sys, json, traceback, datetime
import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
ASTOCK = "E:/astock"
LOG = os.path.join(REAL, "OVERNIGHT_20260825_PROGRESS.log")
REPORT = os.path.join(REAL, "OVERNIGHT_20260825_RESULT.md")
PY = sys.executable

# 研究用面板/模型（非 V1.1 生产资产）
PANEL_SC = os.path.join(DATA, "feature_panel_v3_sc.parquet")
META_ENH = os.path.join(DATA, "features_v3_enh.json")
MODEL_ENH = r"D:/QuantLab/models/lgb_model_v3_enh.txt"

_report_lines = []
_cur_phase = ""


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def report(md):
    _report_lines.append(md)
    log("REPORT> " + md.replace("\n", " "))


def phase(name):
    global _cur_phase
    _cur_phase = name
    log(f"\n########## 阶段: {name} ##########")


def calc_daily_ic(panel, feat_cols, ret_col="fwd_ret"):
    """逐日横截面 IC（秩相关），返回 (mean_ic, icir, pos_ratio)。"""
    rows = []
    for d, g in panel.groupby("trade_date"):
        if len(g) < 20:
            continue
        r = g[feat_cols].rank()
        rr = g[ret_col].rank()
        ic = r.corrwith(rr)
        rows.append({"date": d, **ic.to_dict()})
    df = pd.DataFrame(rows)
    out = {}
    for f in feat_cols:
        s = df[f].dropna()
        if len(s) < 20:
            out[f] = (None, None, None, int(len(s)))
            continue
        mean = float(s.mean())
        std = float(s.std())
        icir = mean / std if std and std > 0 else None
        pos = float((s > 0).mean())
        out[f] = (round(mean, 5), round(icir, 4) if icir else None, round(pos, 3), int(len(s)))
    return out


def run_step(fn, name):
    try:
        fn()
    except Exception as e:
        log(f"!! {name} 失败: {e}")
        log(traceback.format_exc())


# ======================================================================
# 阶段 1：真实 F2/F5 构建与 IC 复核
# ======================================================================
def build_real_f2f5_panel():
    phase("1.真实 F2/F5 面板构建")
    log("读取 v3_sc 面板（含 v3_enh 特征 + 新浪 main_net + 代理 industry_pct）")
    p = pd.read_parquet(PANEL_SC)
    p["trade_date"] = pd.to_datetime(p["trade_date"])
    log(f"面板: {len(p):,} 行, 列数 {p.shape[1]}, 区间 {p['trade_date'].min().date()} ~ {p['trade_date'].max().date()}")

    # --- F2 真实：moneyflow 五档 ---
    mf = pd.read_parquet(os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet")).reset_index()
    mf["trade_date"] = pd.to_datetime(mf["trade_date"])
    mf["mf_main_net"] = mf["buy_lg_amount"] + mf["buy_elg_amount"] - mf["sell_lg_amount"] - mf["sell_elg_amount"]
    mf["mf_elg_net"] = mf["buy_elg_amount"] - mf["sell_elg_amount"]
    mf["mf_net"] = mf["net_mf_amount"]
    tot = mf[["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]].sum(axis=1)
    mf["mf_main_ratio"] = np.where(tot > 0, mf["mf_main_net"] / tot, np.nan)
    mf2 = mf[["ts_code", "trade_date", "mf_main_net", "mf_elg_net", "mf_net", "mf_main_ratio"]]
    log(f"moneyflow 五档: {len(mf2):,} 行, {mf2['trade_date'].min().date()} ~ {mf2['trade_date'].max().date()}")
    before = len(p)
    p = p.merge(mf2, on=["ts_code", "trade_date"], how="left")
    cov = p["mf_main_net"].notna().mean()
    log(f"merge 后行数 {len(p):,} (前 {before:,}), mf_main_net 覆盖率 {cov:.4f}")

    # --- F5 真实：moneyflow_ind_dc 行业资金流 + 板块涨幅 ---
    sb = pd.read_parquet(os.path.join(ASTOCK, "basic", "stock_basic.parquet"), columns=["ts_code", "industry"])
    ind_map = sb.dropna(subset=["industry"]).set_index("ts_code")["industry"].to_dict()
    ind = pd.read_parquet(os.path.join(ASTOCK, "board_fundflow", "moneyflow_ind_dc.parquet")).reset_index()
    ind["trade_date"] = pd.to_datetime(ind["trade_date"])
    ind = ind[ind["content_type"] == "行业"].copy()
    indf = ind[["trade_date", "name", "net_amount", "pct_change"]].rename(
        columns={"name": "industry", "net_amount": "ind_net_mf", "pct_change": "ind_pct_real"})
    log(f"行业资金流: {len(indf):,} 行, {indf['trade_date'].min().date()} ~ {indf['trade_date'].max().date()}, 行业数 {indf['industry'].nunique()}")
    p["industry"] = p["ts_code"].map(ind_map)
    p = p.merge(indf, on=["trade_date", "industry"], how="left")
    cov_ind = p["ind_net_mf"].notna().mean()
    log(f"F5 行业覆盖率 {cov_ind:.4f} (2023-09 起，回测期 2024-07 后应高)")
    p_post = p[p["trade_date"] >= "2024-07-01"]
    log(f"回测期(>=2024-07)行数 {len(p_post):,}, ind_net_mf 覆盖率 {p_post['ind_net_mf'].notna().mean():.4f}, mf_main_net 覆盖率 {p_post['mf_main_net'].notna().mean():.4f}")

    out = os.path.join(DATA, "feature_panel_v3_sc_real.parquet")
    p.to_parquet(out)
    report(f"- **阶段1** 构建真实面板 `feature_panel_v3_sc_real.parquet`：moneyflow五档(2007起) main_net/超大单净额/主力净占比；东财行业资金流+板块涨幅(2023-09起)。回测期 F2 覆盖率 {p_post['mf_main_net'].notna().mean():.2%} / F5 {p_post['ind_net_mf'].notna().mean():.2%}。")
    # 保存供后续阶段使用
    globals()["_panel_real"] = p


def ic_real_vs_proxy():
    phase("2.真实 vs 代理 F2/F5 IC 对比")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    cols = ["main_net", "mf_main_net", "mf_elg_net", "mf_net", "mf_main_ratio",
            "industry_pct", "ind_pct_real", "ind_net_mf"]
    cols = [c for c in cols if c in p.columns]
    ic = calc_daily_ic(p, cols)
    report(f"\n### 阶段2 真实 vs 代理 F2/F5 IC（目标 fwd_ret）")
    for f in cols:
        mean, icir, pos, n = ic[f]
        report(f"- `{f}`: 日均IC={mean} ICIR={icir} 正IC占比={pos} 交易日={n}")
    # 归一化对比（量纲不同，用秩 IC 已可比）
    for a, b in [("main_net", "mf_main_net"), ("industry_pct", "ind_pct_real")]:
        if a in ic and b in ic and ic[a][0] is not None and ic[b][0] is not None:
            delta = ic[b][0] - ic[a][0]
            report(f"- **对比 {a} → {b}: IC 变化 {delta:+.5f}")


def retrain_candidate_with_real():
    phase("3.重训 v3 候选（加入真实 F2/F5 特征）")
    import lightgbm as lgb
    from scipy.stats import spearmanr
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    meta = json.load(open(META_ENH, encoding="utf-8"))
    base_feat = meta["feature_cols"]
    new_feat = [c for c in ["mf_main_net", "mf_elg_net", "mf_main_ratio", "ind_net_mf", "ind_pct_real"]
                if c in p.columns]
    feat_cols = base_feat + new_feat
    # 特征名去重保序
    feat_cols = list(dict.fromkeys(feat_cols))
    log(f"特征: {len(base_feat)} 基础 + {len(new_feat)} 真实 = {len(feat_cols)}")
    p = p.dropna(subset=["label", "fwd_ret"])
    X = p[feat_cols].astype("float32").values
    y = p["label"].astype(int).values
    fwd = p["fwd_ret"].astype(float).values
    m_tr = (p["trade_date"] >= "2020-01-01") & (p["trade_date"] <= "2023-06-30")
    m_va = (p["trade_date"] >= "2023-07-01") & (p["trade_date"] <= "2024-06-30")
    m_te = (p["trade_date"] >= "2024-07-01") & (p["trade_date"] <= "2026-08-14")
    params = dict(objective="binary", metric="auc", learning_rate=0.02, n_estimators=12000,
                  max_depth=5, num_leaves=63, min_child_samples=1000, feature_fraction=0.9,
                  bagging_fraction=0.8, lambda_l1=0.0667467948733595, lambda_l2=837.6092125986357,
                  random_state=42, n_jobs=-1, verbose=-1)
    log(f"训练: train {m_tr.sum():,} / valid {m_va.sum():,} / test {m_te.sum():,}")
    model = lgb.LGBMClassifier(**params)
    model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_va], y[m_va])],
              callbacks=[lgb.early_stopping(400, verbose=False)])
    bi = model.best_iteration_
    prob = model.predict_proba(X[m_te])[:, 1]
    msk = np.isfinite(fwd[m_te]) & np.isfinite(prob)
    ic_test = spearmanr(fwd[m_te][msk], prob[msk]).correlation
    log(f"best_iteration={bi}  test IC={ic_test:.5f}")
    # 保存候选（ASCII 路径 + 复制到 versions/models）
    from datetime import datetime
    import shutil
    stamp = datetime.now().strftime("%Y%m%d")
    fname = f"lgb_model_v3_realF25_{stamp}_{bi}t.txt"
    out_ascii = os.path.join(r"c:\Users\Administrator\.trae-cn\work\6a856dd08ac25249ed9d6c30", fname)
    model.booster_.save_model(out_ascii, num_iteration=bi)
    cand_dir = os.path.join(PROJ, "versions", "models")
    os.makedirs(cand_dir, exist_ok=True)
    shutil.copy2(out_ascii, os.path.join(cand_dir, fname))
    # 保存特征定义
    feat_json = os.path.join(DATA, f"features_v3_realF25_{stamp}.json")
    json.dump({"feature_cols": feat_cols}, open(feat_json, "w", encoding="utf-8"), ensure_ascii=False)
    report(f"- **阶段3** 重训候选 `{fname}`：特征 {len(feat_cols)}（+真实F2/F5），best_iteration={bi}，test IC={ic_test:.5f}（对比 v3_enh 基线 test IC≈0.044）。")


# ======================================================================
# 阶段 4：新因子 IC 研究（衍生方向）
# ======================================================================
def _ic_factor_df(name, df, feat_cols, ret_col="fwd_ret"):
    """df 需含 trade_date/ts_code/特征/fwd_ret。返回 IC dict。"""
    df = df.dropna(subset=[ret_col])
    return calc_daily_ic(df, [c for c in feat_cols if c in df.columns])


def research_chip():
    phase("4.1 筹码分布因子 IC")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    cy = pd.read_parquet(os.path.join(ASTOCK, "chip", "cyq_daily.parquet"))
    cy["trade_date"] = pd.to_datetime(cy["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    cy = cy.dropna(subset=["trade_date"])
    cy["chip_winner"] = cy["winner_rate"]
    cy["chip_cost50"] = cy["cost_50pct"]
    cy["chip_spread"] = (cy["cost_95pct"] - cy["cost_5pct"]) / cy["cost_50pct"].replace(0, np.nan)
    cy = cy[["ts_code", "trade_date", "chip_winner", "chip_cost50", "chip_spread"]]
    d = p.merge(cy, on=["ts_code", "trade_date"], how="left")
    ic = _ic_factor_df("chip", d, ["chip_winner", "chip_cost50", "chip_spread"])
    report(f"\n### 阶段4.1 筹码分布 IC")
    for f, v in ic.items():
        report(f"- `{f}`: 日均IC={v[0]} ICIR={v[1]} 正IC占比={v[2]} 交易日={v[3]}")


def research_lhb():
    phase("4.2 龙虎榜因子 IC")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    tl = pd.read_parquet(os.path.join(ASTOCK, "lhb", "top_list.parquet")).reset_index()
    tl["trade_date"] = pd.to_datetime(tl["trade_date"])
    g = tl.groupby(["ts_code", "trade_date"]).agg(
        lhb_net=("net_amount", "sum"), lhb_count=("net_amount", "size")).reset_index()
    # 上榜后次日起 N 日（用 fwd_ret 已经代表次日；此处直接合并上榜当日作信号）
    d = p.merge(g, on=["ts_code", "trade_date"], how="left")
    d["lhb_net"] = d["lhb_net"].fillna(0)
    d["lhb_count"] = d["lhb_count"].fillna(0)
    ic = _ic_factor_df("lhb", d, ["lhb_net", "lhb_count"])
    report(f"\n### 阶段4.2 龙虎榜 IC")
    for f, v in ic.items():
        report(f"- `{f}`: 日均IC={v[0]} ICIR={v[1]} 正IC占比={v[2]} 交易日={v[3]}")


def research_auction():
    phase("4.3 集合竞价因子 IC")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    au = pd.read_parquet(os.path.join(ASTOCK, "auction", "stock_auction_o_daily.parquet")).reset_index()
    au["trade_date"] = pd.to_datetime(au["trade_date"])
    au["auc_ret"] = au["close"] / au["open"].replace(0, np.nan) - 1  # 竞价成交 vs 开盘价
    au["auc_vol_ratio"] = au["vol"] / au["vol"].groupby(au["trade_date"]).transform("median")
    au = au[["ts_code", "trade_date", "auc_ret", "auc_vol_ratio"]]
    d = p.merge(au, on=["ts_code", "trade_date"], how="left")
    ic = _ic_factor_df("auction", d, ["auc_ret", "auc_vol_ratio"])
    report(f"\n### 阶段4.3 集合竞价 IC")
    for f, v in ic.items():
        report(f"- `{f}`: 日均IC={v[0]} ICIR={v[1]} 正IC占比={v[2]} 交易日={v[3]}")


def research_report():
    phase("4.4 研报一致预期因子 IC")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    rc = pd.read_parquet(os.path.join(ASTOCK, "research", "report_rc_daily.parquet")).reset_index()
    rc["trade_date"] = pd.to_datetime(rc["report_date"]) if "report_date" in rc.columns else pd.to_datetime(rc.index.get_level_values("report_date"))
    rc["rc_rating_up"] = rc["rating"].map({"买入": 2, "增持": 1, "持有": 0, "中性": -1, "减持": -2, "卖出": -3})
    g = rc.groupby(["ts_code", "trade_date"]).agg(rc_num=("rating", "size"),
                                                   rc_rating=("rc_rating_up", "mean")).reset_index()
    d = p.merge(g, on=["ts_code", "trade_date"], how="left")
    d["rc_num"] = d["rc_num"].fillna(0)
    ic = _ic_factor_df("report", d, ["rc_num", "rc_rating"])
    report(f"\n### 阶段4.4 研报一致预期 IC")
    for f, v in ic.items():
        report(f"- `{f}`: 日均IC={v[0]} ICIR={v[1]} 正IC占比={v[2]} 交易日={v[3]}")


def research_northbound():
    phase("4.5 北向持股因子 IC")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    hk = pd.read_parquet(os.path.join(ASTOCK, "northbound", "hk_hold_full.parquet"))
    hk["trade_date"] = pd.to_datetime(hk["trade_date"])
    hk = hk[["ts_code", "trade_date", "ratio"]].rename(columns={"ratio": "north_ratio"})
    hk["north_chg"] = hk.sort_values(["ts_code", "trade_date"]).groupby("ts_code")["north_ratio"].diff()
    d = p.merge(hk, on=["ts_code", "trade_date"], how="left")
    ic = _ic_factor_df("northbound", d, ["north_ratio", "north_chg"])
    report(f"\n### 阶段4.5 北向持股 IC")
    for f, v in ic.items():
        report(f"- `{f}`: 日均IC={v[0]} ICIR={v[1]} 正IC占比={v[2]} 交易日={v[3]}")


def research_board_rotation():
    phase("4.6 板块资金流轮动因子 IC")
    p = globals().get("_panel_real")
    if p is None:
        p = pd.read_parquet(os.path.join(DATA, "feature_panel_v3_sc_real.parquet"))
        p["trade_date"] = pd.to_datetime(p["trade_date"])
    # 用已构建的 ind_net_mf / ind_pct_real：行业内个股取行业资金流（已在面板中）
    cols = [c for c in ["ind_net_mf", "ind_pct_real"] if c in p.columns]
    ic = _ic_factor_df("board_rotation", p, cols)
    report(f"\n### 阶段4.6 板块资金流轮动 IC")
    for f, v in ic.items():
        report(f"- `{f}`: 日均IC={v[0]} ICIR={v[1]} 正IC占比={v[2]} 交易日={v[3]}")


# ======================================================================
# 阶段 5：评分卡真实回测复核（复用 scan_rotate_cost_real.py）
# ======================================================================
def scorecard_backtest_review():
    phase("5.评分卡真实回测（真实 F2/F5 面板，模型=v3_enh）")
    import subprocess
    env = dict(os.environ)
    env["BT_PANEL"] = os.path.join(DATA, "feature_panel_v3_sc_real.parquet")
    env["BT_MODEL"] = MODEL_ENH
    env["BT_META"] = META_ENH
    env["BT_OUT"] = os.path.join(REAL, "scan_rotate_cost_real_v2_report.md")
    r = subprocess.run([PY, os.path.join(PROJ, "scan_rotate_cost_real.py"), "--exec"],
                       env=env, capture_output=True, text=True, cwd=PROJ)
    log(r.stdout[-3000:] if r.stdout else "")
    if r.returncode != 0:
        log("!! 回测退出码 " + str(r.returncode))
        log(r.stderr[-2000:] if r.stderr else "")
    else:
        # 提取报告关键行
        if os.path.exists(env["BT_OUT"]):
            txt = open(env["BT_OUT"], encoding="utf-8").read()
            report(f"- **阶段5** 评分卡真实回测输出 `scan_rotate_cost_real_v2_report.md`（截取）:")
            for line in txt.splitlines():
                if any(k in line for k in ["N=", "超额", "胜率", "日均", "slippage", "滑点", "Top", "top"]):
                    report("  - " + line.strip())


def main():
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("通宵任务 2026-08-25 启动\n")
    log("==== 通宵任务 2026-08-25 启动 ====")
    run_step(build_real_f2f5_panel, "构建真实面板")
    run_step(ic_real_vs_proxy, "真实vs代理IC")
    run_step(retrain_candidate_with_real, "重训候选")
    run_step(research_chip, "筹码分布")
    run_step(research_lhb, "龙虎榜")
    run_step(research_auction, "集合竞价")
    run_step(research_report, "研报")
    run_step(research_northbound, "北向")
    run_step(research_board_rotation, "板块轮动")
    run_step(scorecard_backtest_review, "评分卡真实回测")

    # 写报告
    header = [
        "# 通宵研究结论 2026-08-25",
        "",
        f"> 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "> 数据源：E:/astock（2026-08-25 整合采购数据：moneyflow五档/板块资金流/龙虎榜/竞价/研报/筹码/北向）",
        "> 严守隔离：仅产研究面板/候选模型，未触碰 V1.1 生产资产。",
        "",
    ]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(header + _report_lines))
        f.write("\n\n---\n*仅供个人量化研究使用，不构成投资建议。市场有风险。*\n")
    log(f"\n==== 全部完成，报告: {REPORT} ====")


if __name__ == "__main__":
    main()
