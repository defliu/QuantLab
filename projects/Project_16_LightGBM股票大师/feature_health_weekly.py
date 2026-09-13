# -*- coding: utf-8 -*-
"""特征级 IC/覆盖率周报（2026-09-13 立，T-20260913-001 F1）—— 全自动监控 + 人工裁决去留。

对 V1.3 实盘面板（feature_panel_v3.parquet，27 特征）逐特征算滚动 IC 与缺失率：
  - 近 4 周（约 20 个交易日）逐日横截面 Spearman IC
  - 近 4 周缺失率（NaN 占比）
判定告警：
  - 单特征近 4 周 IC 连续趋零（|IC|均值 < 0.01）→ WATCH
  - 缺失率 > 5% → WATCH（覆盖率退化，特征可能断源）
  - 同时满足近 4 周 |IC|<0.01 且缺失率>5% → ALERT（强烈建议人工核查/剔除）
不自动改特征集——只告警，去留人工裁决（防误伤 + 符合"重训不负责找 alpha"红线）。

【2026-09-13 P0-2 补盲】--panel g2 模式（DE 意见书④-d-1，T-20260913-001）：
  G2 训练面板口径 43 特征 = 27 v3 基础 + 6 enh 慢变量 + 10 g2 增强，
  与 train_g2.py build_panel 拼接口径一致（base 面板 + enh 面板逐行 asof merge +
  lhb/northbound/research/moneyflow/board 外部表）。历史退化点恰是 6 个 enh 慢变量
  （asof 漂移 T-20260910-106），此前监控只覆盖 27 特征——本模式补齐。
  判定规则与 V1.3 27 特征口径完全一致（IC_FLOOR/MISS_FLOOR/双条件）。
  G2_EXTRA 外部表附「来源新鲜度」检查（各源 max(trade_date) vs 面板最新日），
  断源在缺失率上通常表现为 100% NaN，报告额外加「断源」注记（缺失率>=99.5%）。
  注：IC 目标统一用面板 fwd_ret（v3 面板口径）；lhb_net/lhb_count/rc_num 的缺失
  按训练口径 fillna(0) 后统计（事件缺失=0 是有效值，断源看新鲜度表而非缺失率）。

用法：
  python feature_health_weekly.py                # V1.3 面板（27 特征）周报 + 有 ALERT 时飞书告警
  python feature_health_weekly.py --check        # 只读评估不推送（dry-run）
  python feature_health_weekly.py --panel g2     # G2 训练面板口径（43 特征）周报
  python feature_health_weekly.py --panel g2 --check
产物：data/feature_health_weekly_<date>.json/.md（V1.3）
      data/feature_health_weekly_g2_<date>.json/.md（G2）
退出码：0 正常（含 WATCH）；2 有 ALERT（fail-loud 供调度感知）；1 运行错误。
"""
import argparse
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
META = os.path.join(DC.DATA_DIR, "features_v3.json")
ENH_PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3_enh.parquet")
ENH_META = os.path.join(DC.DATA_DIR, "features_v3_enh.json")
OUT_JSON = os.path.join(DC.DATA_DIR, "feature_health_weekly_%s.json" % pd.Timestamp.now().strftime("%Y%m%d"))
OUT_MD = os.path.join(DC.DATA_DIR, "feature_health_weekly_%s.md" % pd.Timestamp.now().strftime("%Y%m%d"))
OUT_JSON_G2 = os.path.join(DC.DATA_DIR, "feature_health_weekly_g2_%s.json" % pd.Timestamp.now().strftime("%Y%m%d"))
OUT_MD_G2 = os.path.join(DC.DATA_DIR, "feature_health_weekly_g2_%s.md" % pd.Timestamp.now().strftime("%Y%m%d"))

IC_FLOOR = 0.01    # 近 4 周 |IC| 均值 < 0.01 = 趋零
MISS_FLOOR = 0.05  # 缺失率 > 5% = 覆盖率退化
STALE_MISS = 0.995  # 缺失率 >= 99.5% = 断源注记（G2 模式信息项，不改变 ALERT/WATCH 判定）

# ---- G2 训练口径常量（与 train_g2.py L43-53 逐字对齐，勿单边改动）----
LHB_FEATS = ["lhb_net", "lhb_count"]
NORTH_FEATS = ["north_chg"]
RC_FEATS = ["rc_rating", "rc_num"]
MF_FEATS = ["mf_main_net", "mf_elg_net", "mf_main_ratio"]
IND_FEATS = ["ind_pct_ths", "ind_net_ths"]
G2_EXTRA = LHB_FEATS + NORTH_FEATS + RC_FEATS + MF_FEATS + IND_FEATS
G2_WINDOW_DAYS = 60  # G2 模式窗口加载天数（4 周监控 + fwd_ret 尾部 NaN 缓冲）


def calc_weekly_ic(panel, feat_cols):
    """逐日横截面 IC（Spearman 秩相关）→ 按周聚合（最近 4 周）。返回 (ic_week, coverage_week)。"""
    df = panel.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["week"] = df["trade_date"].dt.to_period("W")
    ic_daily = {}
    for d, g in df.groupby("trade_date"):
        if len(g) < 20:
            continue
        r = g[feat_cols].rank()
        rr = g["fwd_ret"].rank()
        # 常数列（如 lhb_count 全 0）在 corrwith 中触发 numpy RuntimeWarning，结果 NaN 为预期行为
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ic = r.corrwith(rr)
        ic_daily[d] = ic.to_dict()
    if not ic_daily:
        return None, None
    ic_df = pd.DataFrame(ic_daily).T  # date x feature
    ic_df["week"] = pd.to_datetime(ic_df.index).to_period("W")
    ic_week = ic_df.groupby("week")[feat_cols].mean()
    cov = 1 - df.groupby("week")[feat_cols].apply(lambda s: s.isna().mean())
    return ic_week.tail(4), cov.tail(4)


def _max_panel_date(panel_path):
    """读面板最新交易日（只读 trade_date 列）。"""
    d = pd.read_parquet(panel_path, columns=["trade_date"])
    return pd.to_datetime(d["trade_date"]).max()


def _read_window(path, columns, cutoff, datecol="trade_date"):
    """按日期列 >= cutoff 窗口读 parquet（pyarrow 谓词下推，防全量行进内存）。
    列缺失时自动收缩（对齐 train_g2 的 enh_cols 可用性探测）。datecol 支持
    trade_date / report_date 等不同命名（research 表用 report_date）。"""
    import pyarrow.parquet as pq
    avail = set(pq.ParquetFile(path).schema_arrow.names)
    cols = [c for c in columns if c in avail]
    if datecol not in cols:
        cols.append(datecol)
    t = pq.read_table(path, columns=cols, filters=[(datecol, ">=", pd.Timestamp(cutoff))])
    df = t.to_pandas()
    # 部分 astock 表把 trade_date/report_date 存为 pandas index（train_g2 用 reset_index 取回）
    if df.index.name is not None or df.index.nlevels > 1:
        try:
            df = df.reset_index()
        except Exception:
            pass
    keep = [c for c in columns if c in df.columns]
    if datecol in df.columns and datecol not in keep:
        keep.append(datecol)
    df = df[keep]
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df, keep


def _source_freshness(panel_max_date):
    """G2_EXTRA 10 特征的 6 张外部表来源新鲜度（max(trade_date) vs 面板最新日）。
    只读日期列，返回 {来源: {"features": [...], "max_date": str, "lag_days": int}}。"""
    import pyarrow.parquet as pq
    A = DC.ASTOCK_DIR
    srcs = [
        ("lhb/top_list.parquet", "trade_date", LHB_FEATS),
        ("northbound/hk_hold_full.parquet", "trade_date", NORTH_FEATS),
        ("research/report_rc_daily.parquet", "report_date", RC_FEATS),
        ("moneyflow/moneyflow.parquet", "trade_date", MF_FEATS),
        ("board/ths_daily.parquet", "trade_date", IND_FEATS),
        ("board_fundflow/moneyflow_ind_ths.parquet", "trade_date", IND_FEATS),
    ]
    out = {}
    for rel, datecol, feats in srcs:
        p = os.path.join(A, rel.replace("/", os.sep))
        entry = {"features": feats, "max_date": None, "lag_days": None}
        try:
            if os.path.exists(p):
                d = pq.read_table(p, columns=[datecol]).to_pandas()
                if d.index.name is not None or d.index.nlevels > 1:
                    try:
                        d = d.reset_index()
                    except Exception:
                        pass
                mx = pd.to_datetime(d[datecol]).max()
                entry["max_date"] = str(mx.date())
                entry["lag_days"] = int((panel_max_date - mx).days)
        except Exception as e:
            entry["max_date"] = "读取失败: %s" % type(e).__name__
        out[rel] = entry
    return out


def build_g2_view():
    """构建 G2 训练口径监控视图（窗口化，近 G2_WINDOW_DAYS 自然日）：
    27 v3 基础（BASE_PANEL）+ 6 enh 慢变量（ENH_PANEL 逐行 asof，与 train_g2 L77-94 一致）
    + 10 g2 增强（lhb/north/rc/mf/ths，与 train_g2 L96-148 一致，仅窗口化加速）。
    返回 (panel_df, feat_cols(43), groups, freshness)。"""
    panel_max = _max_panel_date(PANEL)
    cutoff = panel_max - pd.Timedelta(days=G2_WINDOW_DAYS)

    # ---- 27 v3 基础（窗口读，含 fwd_ret 做 IC 目标）----
    meta_v3 = json.load(open(META, encoding="utf-8"))
    base_feat = meta_v3["feature_cols"]
    p, _ = _read_window(PANEL, base_feat + ["trade_date", "ts_code", "fwd_ret"], cutoff)
    p["trade_date"] = pd.to_datetime(p["trade_date"])
    p = p.sort_values("trade_date")
    print("    v3 基础窗口 %s ~ %s，%d 行" % (cutoff.date(), panel_max.date(), len(p)))

    # ---- 6 enh 慢变量：逐行 asof merge backward（T-20260910-106 口径，勿改成广播）----
    import pyarrow.parquet as pq
    enh_avail = set(pq.ParquetFile(ENH_PANEL).schema_arrow.names)
    enh_cols = [c for c in ["dv_year_sum", "ex_days_since", "ex_yoy", "fc_pchange",
                            "industry_mom20", "turnover_rank"] if c in enh_avail]
    enh, _ = _read_window(ENH_PANEL, ["trade_date", "ts_code"] + enh_cols, cutoff)
    enh = enh.drop_duplicates(["ts_code", "trade_date"], keep="last").sort_values("trade_date")
    p = pd.merge_asof(p, enh, on="trade_date", by="ts_code", direction="backward")
    enh_feats = [c for c in enh_cols]  # 已按 train_g2 顺序
    print("    enh 慢变量 %d 个（asof 并入）" % len(enh_feats))

    # ---- 10 g2 增强（窗口化构建，逻辑逐句对齐 train_g2.build_panel）----
    A = DC.ASTOCK_DIR
    # 龙虎榜（事件特征：缺失 fillna(0) 与训练同口径）
    tl, _ = _read_window(os.path.join(A, "lhb", "top_list.parquet"),
                         ["trade_date", "ts_code", "net_amount"], cutoff)
    if len(tl):
        g = tl.groupby(["ts_code", "trade_date"]).agg(
            lhb_net=("net_amount", "sum"), lhb_count=("net_amount", "size")).reset_index()
        p = p.merge(g, on=["ts_code", "trade_date"], how="left")
    else:
        p["lhb_net"] = np.nan
        p["lhb_count"] = np.nan
    p["lhb_net"] = p["lhb_net"].fillna(0.0)
    p["lhb_count"] = p["lhb_count"].fillna(0.0)
    # 北向（注意：训练口径为精确日期 merge 非 asof，源停更后即 NaN——如实暴露）
    import pyarrow.parquet as _pqa
    _hk_t = _pqa.read_table(os.path.join(A, "northbound", "hk_hold_full.parquet"),
                            columns=["ts_code", "trade_date", "ratio"])
    hk = _hk_t.to_pandas()
    if hk.index.name is not None or hk.index.nlevels > 1:
        try:
            hk = hk.reset_index()
        except Exception:
            pass
    hk = hk[["ts_code", "trade_date", "ratio"]]
    hk["trade_date"] = pd.to_datetime(hk["trade_date"])
    hk = hk.sort_values(["ts_code", "trade_date"])
    hk["north_chg"] = hk.groupby("ts_code")["ratio"].diff()
    hk = hk[hk["trade_date"] >= cutoff]
    p = p.merge(hk[["ts_code", "trade_date", "north_chg"]], on=["ts_code", "trade_date"], how="left")
    # 研报（rc_num fillna(0) 与训练同口径；rc_rating 保留 NaN；日期列为 report_date）
    rc, _ = _read_window(os.path.join(A, "research", "report_rc_daily.parquet"),
                         ["report_date", "ts_code", "rating"], cutoff, datecol="report_date")
    rc["trade_date"] = pd.to_datetime(rc["report_date"])
    rc["rc_rating_up"] = rc["rating"].map(
        {"买入": 2, "增持": 1, "持有": 0, "中性": -1, "减持": -2, "卖出": -3})
    rcg = rc.groupby(["ts_code", "trade_date"]).agg(
        rc_num=("rating", "size"), rc_rating=("rc_rating_up", "mean")).reset_index()
    p = p.merge(rcg, on=["ts_code", "trade_date"], how="left")
    p["rc_num"] = p["rc_num"].fillna(0)
    # 真实 F2（moneyflow 五档，源新鲜）
    mf, _ = _read_window(os.path.join(A, "moneyflow", "moneyflow.parquet"),
                         ["trade_date", "ts_code", "buy_lg_amount", "buy_elg_amount",
                          "sell_lg_amount", "sell_elg_amount",
                          "buy_sm_amount", "buy_md_amount"], cutoff)
    mf["mf_main_net"] = mf["buy_lg_amount"] + mf["buy_elg_amount"] - mf["sell_lg_amount"] - mf["sell_elg_amount"]
    mf["mf_elg_net"] = mf["buy_elg_amount"] - mf["sell_elg_amount"]
    tot = mf[["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]].sum(axis=1)
    mf["mf_main_ratio"] = np.where(tot > 0, mf["mf_main_net"] / tot, np.nan)
    p = p.merge(mf[["ts_code", "trade_date", "mf_main_net", "mf_elg_net", "mf_main_ratio"]],
                on=["ts_code", "trade_date"], how="left")
    # 同花顺板块（ths_ind 映射 + 行业涨幅/行业资金流，精确日期 merge）
    comp = pd.read_parquet(os.path.join(
        A, "board", "source", "parquet行业概念板块全量更新到20260814",
        "parquet", "行业概念板块", "行业板块成分汇总_同花顺.parquet"))
    comp["股票代码"] = comp["股票代码"].astype(str)
    comp["指数代码"] = comp["指数代码"].astype(str)
    stk2ind = comp.set_index("股票代码")["指数代码"].to_dict()
    p["ths_ind"] = p["ts_code"].map(stk2ind)
    td, _ = _read_window(os.path.join(A, "board", "ths_daily.parquet"),
                         ["trade_date", "ts_code", "pct_change"], cutoff)
    td["ts_code"] = td["ts_code"].astype(str)
    td = td[td["ts_code"].str.startswith("881")]
    ind_pct = td[["trade_date", "ts_code", "pct_change"]].rename(
        columns={"ts_code": "ths_ind", "pct_change": "ind_pct_ths"})
    p = p.merge(ind_pct, on=["trade_date", "ths_ind"], how="left")
    iths = pd.read_parquet(os.path.join(A, "board_fundflow", "moneyflow_ind_ths.parquet"))
    iths = iths.reset_index()
    iths["trade_date"] = pd.to_datetime(iths["trade_date"])
    iths["ts_code"] = iths["ts_code"].astype(str)
    iths = iths[iths["trade_date"] >= cutoff]
    ind_net = iths[["trade_date", "ts_code", "net_amount"]].rename(
        columns={"ts_code": "ths_ind", "net_amount": "ind_net_ths"})
    p = p.merge(ind_net, on=["trade_date", "ths_ind"], how="left")

    feat = list(dict.fromkeys(base_feat + enh_feats + [c for c in G2_EXTRA if c in p.columns]))
    groups = {}
    for f in base_feat:
        groups[f] = "v3基础"
    for f in enh_feats:
        groups[f] = "enh慢变量"
    for f in G2_EXTRA:
        if f in groups:
            continue
        groups[f] = "g2增强"
    freshness = _source_freshness(panel_max)
    return p, feat, groups, freshness


def _notify(text):
    try:
        from qmt_bridge_client_ens import notify_feishu
        notify_feishu(text)
    except Exception:
        try:
            from qmt_bridge_client import notify_feishu as nf2
            nf2(text)
        except Exception as e:
            print("[通知] 飞书异常: %s" % e)
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description="特征级 IC/覆盖率周报")
    ap.add_argument("--check", action="store_true", help="只读评估不推送")
    ap.add_argument("--panel", choices=["v3", "g2"], default="v3",
                    help="监控面板：v3=V1.3 面板 27 特征（默认）；g2=G2 训练口径 43 特征（P0-2 补盲）")
    args = ap.parse_args()

    out_json = OUT_JSON
    out_md = OUT_MD
    groups = None
    freshness = None
    if args.panel == "g2":
        # ---- G2 训练口径 43 特征（P0-2 补盲，2026-09-13）----
        if not (os.path.exists(PANEL) and os.path.exists(ENH_PANEL)):
            print("!! G2 面板缺失（%s / %s）" % (PANEL, ENH_PANEL))
            return 1
        out_json = OUT_JSON_G2
        out_md = OUT_MD_G2
        print("[1/3] 构建 G2 训练口径视图（27 v3 + 6 enh + 10 g2 增强，窗口 %d 天）..." % G2_WINDOW_DAYS)
        panel, feat_cols, groups, freshness = build_g2_view()
        print("    G2 特征数 %d（27 v3 基础 + 6 enh + 10 g2 增强）" % len(feat_cols))
        ic_week, cov_week = calc_weekly_ic(panel, feat_cols)
    else:
        if not os.path.exists(PANEL) or not os.path.exists(META):
            print("!! 面板或 meta 缺失（%s / %s）" % (PANEL, META))
            return 1
        meta = json.load(open(META, encoding="utf-8"))
        feat_cols = meta["feature_cols"]
        print("[1/3] 读取面板 %s（%d 特征）..." % (os.path.basename(PANEL), len(feat_cols)))
        panel = pd.read_parquet(PANEL)
        ic_week, cov_week = calc_weekly_ic(panel, feat_cols)
    if ic_week is None:
        print("!! 无足够数据计算 IC")
        return 1

    print("[2/3] 计算近 4 周 IC 与覆盖率 ...")
    summary = {}
    for f in feat_cols:
        ic_mean = float(ic_week[f].abs().mean()) if f in ic_week else np.nan
        miss = float((1 - cov_week[f]).mean()) if f in cov_week else np.nan
        ic_zero = (not np.isnan(ic_mean)) and ic_mean < IC_FLOOR
        cov_bad = (not np.isnan(miss)) and miss > MISS_FLOOR
        if ic_zero and cov_bad:
            verdict = "ALERT"
        elif ic_zero or cov_bad:
            verdict = "WATCH"
        else:
            verdict = "OK"
        # 断源注记（信息项，不改判定）：G2 模式下外部源停更 → 特征全 NaN
        stale = (not np.isnan(miss)) and miss >= STALE_MISS
        summary[f] = {"ic_abs_mean_4w": round(ic_mean, 5) if not np.isnan(ic_mean) else None,
                      "missing_rate_4w": round(miss, 5) if not np.isnan(miss) else None,
                      "verdict": verdict,
                      "stale_source": bool(stale)}
        if groups is not None:
            summary[f]["group"] = groups.get(f, "?")

    alerts = [f for f, v in summary.items() if v["verdict"] == "ALERT"]
    watches = [f for f, v in summary.items() if v["verdict"] == "WATCH"]

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "panel": "G2 训练面板口径（feature_panel_v3 + _enh asof + g2 增强）" if args.panel == "g2"
                 else os.path.basename(PANEL),
        "mode": args.panel,
        "n_features": len(feat_cols),
        "rules": {"ALERT": "近4周|IC|<%.2f 且 缺失率>%.0f%%" % (IC_FLOOR, MISS_FLOOR * 100),
                  "WATCH": "近4周|IC|<%.2f 或 缺失率>%.0f%%" % (IC_FLOOR, MISS_FLOOR * 100)},
        "features": summary,
        "alerts": alerts,
        "watches": watches,
    }
    if freshness is not None:
        report["g2_extra_source_freshness"] = freshness
    # P2-11 修复：--check 只读评估不落盘（dry-run 语义严格化）；非 check 才写 json/md
    if not args.check:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print("[3/3] 生成周报 ..." if not args.check else "[3/3] dry-run：不落盘")
    title = "G2 特征级 IC/覆盖率周报（43 特征，P0-2 补盲）" if args.panel == "g2" else "特征级 IC/覆盖率周报"
    lines = [
        "# %s" % title,
        "",
        "> 生成：%s" % report["generated_at"],
        "> 面板：%s（%d 特征）| 近 4 周横截面 IC + 缺失率" % (report["panel"], report["n_features"]),
        "",
        "## 判定规则",
        "",
        "- **ALERT**：%s" % report["rules"]["ALERT"],
        "- **WATCH**：%s" % report["rules"]["WATCH"],
        "",
    ]
    if args.panel == "g2":
        lines += [
            "> IC 目标 = 面板 fwd_ret（v3 口径）；enh 慢变量为逐行 asof（T-20260910-106 口径）；",
            "> lhb_net/lhb_count/rc_num 缺失按训练口径 fillna(0) 后统计（事件缺失=0 为有效值，断源看下方新鲜度表）。",
            "",
        ]
    hdr = "| 特征 | 分组 | 近4周IC(abs均值) | 缺失率 | 判定 |"
    sep = "|---|---|---|---|---|"
    if args.panel != "g2":
        hdr = "| 特征 | 近4周IC(abs均值) | 缺失率 | 判定 |"
        sep = "|---|---|---|---|"
    lines += ["## 特征健康表", "", hdr, sep]
    for f in feat_cols:
        v = summary[f]
        ic = "%.4f" % v["ic_abs_mean_4w"] if v["ic_abs_mean_4w"] is not None else "—"
        ms = "%.2f%%" % (v["missing_rate_4w"] * 100) if v["missing_rate_4w"] is not None else "—"
        if v.get("stale_source"):
            ms += "（断源）"
        if args.panel == "g2":
            lines.append("| %s | %s | %s | %s | **%s** |" % (f, v.get("group", "?"), ic, ms, v["verdict"]))
        else:
            lines.append("| %s | %s | %s | **%s** |" % (f, ic, ms, v["verdict"]))
    lines += [
        "",
        "## 汇总",
        "",
        "- **ALERT（%d）**：%s" % (len(alerts), ", ".join(alerts) if alerts else "无"),
        "- **WATCH（%d）**：%s" % (len(watches), ", ".join(watches) if watches else "无"),
        "",
    ]
    if args.panel == "g2" and freshness is not None:
        lines += [
            "## G2_EXTRA 来源新鲜度（外部表 max(trade_date) vs 面板最新日）",
            "",
            "| 来源表 | 覆盖特征 | 源最新日 | 落后天数 |",
            "|---|---|---|---|",
        ]
        for rel, e in freshness.items():
            lag = e["lag_days"]
            lag_s = str(lag) if lag is not None else "—"
            if lag is not None and lag > 7:
                lag_s += "（⚠️ >7 天，训练面板同口径落后，特征在 asof/精确 merge 下退化为 NaN/0）"
            lines.append("| %s | %s | %s | %s |" % (rel, ", ".join(e["features"]), e["max_date"], lag_s))
        lines += [
            "",
            "> 注：北向/研报/行业表在 train_g2 中为**精确日期 merge**（非 asof），源停更后特征直接 NaN——",
            "> 与训练面板行为一致，属数据源天花板（PROJECT_MEMORY 2026-09-12「数据天花板」节），非本脚本 bug。",
            "",
        ]
    lines.append("> 仅监控告警，特征去留需人工裁决；告警特征在下一轮重训前建议核查数据源/口径。")
    if not args.check:
        with open(out_md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    if not args.check:
        print("    JSON:", out_json)
        print("    MD  :", out_md)
    else:
        print("    [CHECK] 仅评估：json/md 不落盘，未推送")
    print("    ALERT:", len(alerts), "| WATCH:", len(watches))
    if alerts and not args.check:
        _notify("【%s】%d 个特征 ALERT（近4周IC趋零且缺失率>5%%）：%s\nWATCH %d 个：%s" % (
            "G2特征健康周报" if args.panel == "g2" else "特征健康周报",
            len(alerts), ", ".join(alerts), len(watches), ", ".join(watches) if watches else "无"))
    if args.check:
        print("[CHECK] dry-run：未推送")
    return 2 if (alerts and not args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
