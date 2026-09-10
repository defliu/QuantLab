# -*- coding: utf-8 -*-
"""g2 独立部署选股（步骤③）—— 完全独立于 V1.1 的 deploy_predict.py。

流程（与回测口径一致）：
  1) 读 data_live/g2_latest_features.parquet（43 特征，目标日，由 build_g2_daily.py 生成）
  2) g2_strong_real 模型 → 次日上涨概率
  3) 真实评分卡 F1-F6：F2=mf_main_net(真实主力净额)、F5=ind_pct_ths(真实板块涨幅)、F6=实时PE+换手
  4) 模型分 Top100 预选池 → 评分卡红线（默认 60）→ 按 total_new 取 Top2
  5) 输出 data/selections/g2/ 报告 + 追加 data/real/paper_forward_live.csv

用法：
  python deploy_predict_g2.py --date 2026-08-25 [--threshold 60] [--top 2] [--pool 100]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import scorecard_real as SR
import position_filter as PF

HERE = DC.PROJECT_DIR
DATA = DC.DATA_DIR
LIVE = DC.LIVE_DIR
REAL = os.path.join(DATA, "real")
SELECT = os.path.join(DATA, "selections", "g2")

G2_MODEL, G2_META = DC.g2_live()  # 读 live 指针（周更重训 promote 后自动跟随），缺失回退 08-25 初始 live
SNAP = os.path.join(LIVE, "g2_latest_features.parquet")
LIVE_LOG = os.path.join(REAL, "paper_forward_live.csv")
# 纸面 A/B（--ab）：v3_enh(33特征) 模型，同一快照/评分卡/红线，纯模型对比（T-20260909 V1.4 模型决策）
AB_MODEL = "D:/QuantLab/models/lgb_model_v3_enh.txt"
AB_META = os.path.join(DATA, "features_v3_enh.json")
AB_LOG = os.path.join(REAL, "paper_forward_ab_v3enh.csv")
ENS_LOG = os.path.join(REAL, "paper_forward_ens.csv")


def _append_live_dedup(rows):
    """幂等追加 paper_forward_live.csv：读现有 → concat → 按 (date, code) 去重 → 原子写回。

    修复 T-20260831（审计）指出的 8/28 重复追加 4 次：定时任务重入/重跑不再产生重复行。
    """
    new = pd.DataFrame(rows)
    if os.path.exists(LIVE_LOG):
        old = pd.read_csv(LIVE_LOG, encoding="utf-8-sig")
        df = pd.concat([old, new], ignore_index=True)
    else:
        df = new
    df = df.drop_duplicates(subset=["date", "code"], keep="last")
    tmp = LIVE_LOG + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, LIVE_LOG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日，缺省取快照最新日")
    ap.add_argument("--threshold", type=float, default=60.0)
    ap.add_argument("--top", type=int, default=10, help="候选池数量（默认10，对齐回测 TOP10；rebalance 从池中按 total_new 选 TOP_N 持仓）")
    ap.add_argument("--pool", type=int, default=100)
    ap.add_argument("--ab", action="store_true",
                    help="纸面 A/B：同一快照额外用 v3_enh(33特征) 出候选，写 paper_forward_ab_v3enh.csv（不影响 G2 主流程）")
    ap.add_argument("--ensemble", action="store_true",
                    help="纸面融合臂：rank 融合(v3_enh+G2, w=0.5)，写 paper_forward_ens.csv（可与 --ab 同用）")
    args = ap.parse_args()

    feat_cols = json.load(open(G2_META, encoding="utf-8"))["feature_cols"]
    day = pd.read_parquet(SNAP)
    day["trade_date"] = pd.to_datetime(day["trade_date"])
    if args.date is None:
        target = day["trade_date"].max()
    else:
        target = pd.Timestamp(args.date)
    day = day[day["trade_date"] == target].copy()
    if len(day) == 0:
        print(f"!! 快照中无 {target.date()}，可用 {day['trade_date'].min().date() if len(day) else 'N/A'}")
        return
    print(f"[1/5] 快照 {target.date()} 股票数 {len(day):,} 特征 {len(feat_cols)}")

    print("[2/5] g2 模型推理 ...")
    booster = lgb.Booster(model_file=G2_MODEL)
    day["prob"] = booster.predict(day[feat_cols].astype("float32").values)

    print("[3/5] 预选池（模型 Top%d） ..." % args.pool)
    pre = day.nlargest(args.pool, "prob").copy()

    print("[4/5] 真实评分卡（F2实时主力净额 / F5当日板块涨幅 / F6实时PE换手） ...")
    # est：主库按 (trade_date, ts_code) 提供 T 日 pe_ttm/turnover_rate（T 日口径，F6 用）
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily.reindex(idx)
    # 真实列（如快照缺列则回退中性）
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan
    # F2 评分卡口径：main_net 需为「元」（score_f2 阈值 5e7/1e7/1e8 均为元）
    # 快照 mf_main_net 是 moneyflow 五档「万元」，转元后作为评分卡主值；industry_pct 取 F5 当日自算值
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    # ---- F2 主数据源自适应（2026-09-03 升级 Tushare moneyflow 为主源）----
    # Tushare 每日 19:30 刷新后快照新鲜（滞后<=2天）-> F2 用 Tushare 主源（零 skew 训练同口径）
    # 否则回退原逻辑：新浪当日实时覆盖（避免 Tushare 滞后时 F2 反而变旧）
    import g2_realtime as RT
    MF_PATH = os.path.join(DC.ASTOCK_DIR, "moneyflow", "moneyflow.parquet") \
        if hasattr(DC, "ASTOCK_DIR") else "D:/astock/moneyflow/moneyflow.parquet"
    mf_fresh = False
    try:
        _mf = pd.read_parquet(MF_PATH, columns=["net_mf_amount"])
        _latest = pd.Timestamp(_mf.index.get_level_values("trade_date").max())
        mf_fresh = (target - _latest).days <= 2
    except Exception as _e:
        print(f"    [F2] 读 Tushare 快照失败（{_e}），回退新浪兜底")
    pool_codes = pre["ts_code"].astype(str).tolist()
    rt = RT.fetch_main_net_sina(pool_codes, target_date=target.strftime("%Y-%m-%d"))
    rt_hit = 0
    for c in pool_codes:
        m = pre["ts_code"].astype(str) == c
        if not m.any():
            continue
        _snap = pre.loc[m, "main_net"].iloc[0] if "main_net" in pre.columns else np.nan
        # 仅当主源缺失/为0 时才用新浪兜底；新鲜时信任 Tushare 主源
        if c in rt and (not mf_fresh or pd.isna(_snap) or _snap == 0):
            pre.loc[m, "main_net"] = rt[c].get("main_net_yuan", np.nan)
            if "mf_main_net" in pre.columns:
                pre.loc[m, "mf_main_net"] = rt[c].get("mf_main_net", np.nan)
            if "mf_main_ratio" in pre.columns:
                pre.loc[m, "mf_main_ratio"] = rt[c].get("mf_main_ratio", np.nan)
            rt_hit += 1
    _src = "Tushare每日快照(主源)" if mf_fresh else "新浪当日(兜底,Tushare滞后)"
    print(f"    F2 主源={_src}；新浪兜底覆盖 {rt_hit}/{len(pool_codes)} 只")
    # F5 为快照当日自算板块涨幅（build_g2_daily 已算），此处直接用 pre 的 industry_pct
    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre[f"SC_{f}"] = sc[f].values

    print("[5/5] 红线 + 位置过滤 + Top%d ..." % args.top)
    pre = pre[pre["total_new"] >= args.threshold].copy()
    if len(pre) == 0:
        print(f"    !! 预选池({args.pool})内无股票通过红线 {args.threshold} → 空仓")
        return
    # ---- 位置过滤（G2-V1.0，2026-09-08）----
    # 【重要修正 2026-09-08】G2 模型(g2_strong_real)口径回测验证：full 过滤有害
    # （红线60/N=10/0.1%滑点：Base +0.149% → full -0.108%），lite 亦未见增益，
    # 因 g2 模型(N3对齐+真实F2/F5)选出的强势票位置本身健康，过滤剔掉的是盈利交易。
    # → G2 默认【不过滤】（与官方 Base 口径一致）；仅显式 PF_MODE_G2=lite/full 才启用。
    # V1.3 链路(v3_enh 模型)的 lite 过滤继续保留（58-lite +0.157% 验证有效）。
    _g2_mode = os.environ.get("PF_MODE_G2", "").strip()
    if _g2_mode and not os.environ.get("PF_DISABLE", "0") == "1":
        _reasons = {}
        pre = PF.apply_rules(pre, target_date=target, mode=_g2_mode, reason_out=_reasons)
        if _reasons:
            print(f"    !! 位置过滤({_g2_mode})剔除 {len(_reasons)} 只: {_reasons}")
        if len(pre) == 0:
            print(f"    !! 红线+位置过滤后无候选 → 空仓（宁缺毋滥）")
            return
    picks = pre.nlargest(args.top, "total_new")
    cols = ["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]
    out = picks[cols].copy()
    out["prob"] = out["prob"].round(4)
    out["total_new"] = out["total_new"].round(1)
    print(out.to_string(index=False))

    print("[6/6] 保存结果 + 追加 live 日志 ...")
    os.makedirs(SELECT, exist_ok=True)
    date_str = target.strftime("%Y%m%d")
    out["trade_date"] = date_str
    csv = os.path.join(SELECT, f"{date_str}_g2_top{args.top}.csv")
    out.to_csv(csv, index=False, encoding="utf-8-sig")
    md = [f"# g2 独立选股 Top{args.top} · {target.date()}（真实评分卡，红线 {args.threshold:.0f}）", "",
          f"> 模型：g2_strong_real（43特征）| 评分卡：F2实时主力净额(新浪)/F5当日板块涨幅(增量库自算)/F6实时PE换手 | 口径与回测一致", "",
          "| 排名 | 代码 | 模型概率 | 评分卡总分 | F1 | F2 | F3 | F4 | F5 | F6 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for i, (_, r) in enumerate(out.iterrows(), 1):
        md.append(f"| {i} | {r['ts_code']} | {r['prob']:.3f} | {r['total_new']:.0f} | {r['SC_F1']:.0f} | "
                  f"{r['SC_F2']:.0f} | {r['SC_F3']:.0f} | {r['SC_F4']:.0f} | {r['SC_F5']:.0f} | {r['SC_F6']:.0f} |")
    md += ["", "> ⚠️ F2(主力净额)：主源为 Tushare moneyflow 每日快照（零 skew 训练同口径），仅当快照缺失时用新浪当日兜底；F5 为增量库当日行业涨幅自算。",
           "> ⚠️ 其余新因子（lhb/北向/研报/行业资金流）为 D:/astock 周更最新可用值（可能滞后数天）；买入时需按一字板/停牌复核可执行性。",
           "> 独立研究信号，不构成投资建议。"]
    md_path = os.path.join(SELECT, f"{date_str}_g2_selection.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    # 追加 live 日志（code 必须用 ts_code，不能用 iterrows 的整数 index）；幂等去重
    # rank=1..N 按 total_new 降序（picks 已 nlargest 排序）；实盘 g2_config.TOP_N=2，
    # 前向统计时 rank<=2 即实盘口径子集，Top10 全集用于更快累积 N>30 样本。
    rows = [{"date": target.date(), "code": s["ts_code"], "rank": rk,
             "total_new": s["total_new"], "prob": s["prob"]}
            for rk, (_, s) in enumerate(picks.iterrows(), 1)]
    _append_live_dedup(rows)
    print("    CSV:", csv)
    print("    MD :", md_path)
    print("    live log:", LIVE_LOG)

    # ---- 纸面 A/B（--ab）：同一快照 + v3_enh(33特征) 候选（同红线/Top/评分卡，纯模型对比）----
    if args.ab:
        print("\n[AB] v3_enh(33特征) 纸面 A/B 候选 ...")
        if not (os.path.exists(AB_MODEL) and os.path.exists(AB_META)):
            print("    [AB] v3_enh 模型/meta 缺失，跳过（不影响 G2 主流程）")
        else:
            ab_meta = json.load(open(AB_META, encoding="utf-8"))["feature_cols"]
            ab_bst = lgb.Booster(model_file=AB_MODEL)
            day["ab_prob"] = ab_bst.predict(day[ab_meta].astype("float32").values)
            ab_pre = day.nlargest(args.pool, "ab_prob").copy()
            ab_est = daily.reindex(pd.MultiIndex.from_arrays([ab_pre["trade_date"], ab_pre["ts_code"]]))
            for c in ["main_net", "industry_pct", "volume_ratio"]:
                if c not in ab_pre.columns:
                    ab_pre[c] = np.nan
            if "main_net" not in ab_pre.columns and "mf_main_net" in ab_pre.columns:
                ab_pre["main_net"] = ab_pre["mf_main_net"] * 1e4
            if "industry_pct" not in ab_pre.columns and "ind_pct_ths" in ab_pre.columns:
                ab_pre["industry_pct"] = ab_pre["ind_pct_ths"]
            ab_sc = SR.compute_real_scorecard(ab_pre, ab_est)
            ab_pre["total_new"] = ab_sc["total_new_real"].values
            ab_pre = ab_pre[ab_pre["total_new"] >= args.threshold].copy()
            if len(ab_pre) == 0:
                print(f"    [AB] v3_enh 无候选通过红线 {args.threshold:.0f} → 空仓")
            else:
                ab_picks = ab_pre.nlargest(args.top, "total_new")
                ab_rows = [{"date": target.date(), "code": s["ts_code"], "rank": rk,
                            "total_new": s["total_new"], "prob_ab": s["ab_prob"]}
                           for rk, (_, s) in enumerate(ab_picks.iterrows(), 1)]
                _ab_df = pd.DataFrame(ab_rows)
                if os.path.exists(AB_LOG):
                    _old = pd.read_csv(AB_LOG, encoding="utf-8-sig")
                    _ab_df = pd.concat([_old, _ab_df], ignore_index=True)
                _ab_df = _ab_df.drop_duplicates(subset=["date", "code"], keep="last")
                _tmp = AB_LOG + ".tmp"
                _ab_df.to_csv(_tmp, index=False, encoding="utf-8-sig")
                os.replace(_tmp, AB_LOG)
                print(f"    [AB] v3_enh 候选 {len(ab_picks)} 只（Top{args.top}） -> {AB_LOG}")

    # ---- 纸面融合臂（--ensemble）：rank 融合(v3_enh+G2, w=0.5)，同红线/评分卡 ----
    if args.ensemble:
        print("\n[ENS] rank 融合（v3_enh + G2, w=0.5）纸面候选 ...")
        if not (os.path.exists(AB_MODEL) and os.path.exists(AB_META)):
            print("    [ENS] v3_enh 模型/meta 缺失，跳过（不影响 G2 主流程）")
        else:
            en_meta = json.load(open(AB_META, encoding="utf-8"))["feature_cols"]
            en_bst = lgb.Booster(model_file=AB_MODEL)
            day["en_pv"] = en_bst.predict(day[en_meta].astype("float32").values)
            day["en_pg"] = day["prob"].copy()
            day["rank_v"] = day["en_pv"].rank(pct=True)
            day["rank_g"] = day["en_pg"].rank(pct=True)
            day["ens"] = (day["rank_v"] + day["rank_g"]) / 2
            en_pre = day[(day["en_pg"] >= args.threshold / 100) | (day["en_pv"] >= args.threshold / 100)].copy()
            en_pool = en_pre.sort_values("ens", ascending=False).head(args.pool).copy()
            en_est = daily.reindex(pd.MultiIndex.from_arrays([en_pool["trade_date"], en_pool["ts_code"]]))
            for c in ["main_net", "industry_pct", "volume_ratio"]:
                if c not in en_pool.columns:
                    en_pool[c] = np.nan
            if "main_net" not in en_pool.columns and "mf_main_net" in en_pool.columns:
                en_pool["main_net"] = en_pool["mf_main_net"] * 1e4
            if "industry_pct" not in en_pool.columns and "ind_pct_ths" in en_pool.columns:
                en_pool["industry_pct"] = en_pool["ind_pct_ths"]
            en_sc = SR.compute_real_scorecard(en_pool, en_est)
            en_pool["total_new"] = en_sc["total_new_real"].values
            en_pool = en_pool[en_pool["total_new"] >= args.threshold].copy()
            if len(en_pool) == 0:
                print(f"    [ENS] 融合无候选通过红线 {args.threshold:.0f} → 空仓")
            else:
                en_picks = en_pool.nlargest(args.top, "ens")
                en_rows = [{"date": target.date(), "code": s["ts_code"], "rank": rk,
                            "total_new": s["total_new"], "ens": s["ens"],
                            "pv": s["en_pv"], "pg": s["en_pg"]}
                           for rk, (_, s) in enumerate(en_picks.iterrows(), 1)]
                _en_df = pd.DataFrame(en_rows)
                if os.path.exists(ENS_LOG):
                    _old = pd.read_csv(ENS_LOG, encoding="utf-8-sig")
                    _en_df = pd.concat([_old, _en_df], ignore_index=True)
                _en_df = _en_df.drop_duplicates(subset=["date", "code"], keep="last")
                _tmp = ENS_LOG + ".tmp"
                _en_df.to_csv(_tmp, index=False, encoding="utf-8-sig")
                os.replace(_tmp, ENS_LOG)
                print(f"    [ENS] 融合候选 {len(en_picks)} 只（Top{args.top}） -> {ENS_LOG}")


if __name__ == "__main__":
    main()
