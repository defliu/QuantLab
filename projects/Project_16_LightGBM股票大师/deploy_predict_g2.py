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
ENS_SELECT = os.path.join(DATA, "selections", "g2_ens")   # 融合实盘选股落盘目录（rebalance_ens 消费）


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


def _fill_f3_events(df, verbose=False):
    """F3 面板外事件 fail-safe（T-20260924-001 方案 C，2026-09-25 诚哥拍板）。

    build_g2_daily 的事件列由 enh 面板 map 而来（面板宇宙 2026-09-23 仅 2791 只），
    面板外候选 fc/ex/dv 四列全 NaN → deploy_predict._score_f3 加成条件全 False →
    恒基线 4.0（SC_F3 死维度）。此处按 build_features_v2 同源口径为 NaN 行重算四列：
      fc/ex：ann_date<=T 最近一次（merge_asof backward 语义），无事件=9999；
             fc_pchange 窗口外(EVENT_WINDOW=90)置 0；PIT 安全（防未来函数）。
      dv：T 日回溯至多 30 条分红记录，累计 DIV_WINDOW=365 天税后现金分红。
    面板内行保持面板值不动；四列同时是模型特征（g2 43/AB 33 均含），故只在模型
    predict 之后的评分池副本上调用（day 原帧不动）→ 模型输入/输出零变化，仅评分卡
    F3 分量改变。任一数据源缺失/异常时保持 NaN（评分中性），不阻断选股。
    返回重算行数。
    """
    EVENT_WINDOW, DIV_WINDOW = 90, 365
    cols = ["fc_days_since", "ex_days_since", "fc_pchange", "dv_year_sum"]
    need = [c for c in cols if c in df.columns]
    if not need or len(df) == 0:
        return 0
    for c in need:
        if df[c].dtype != np.float64:
            df[c] = df[c].astype("float64")  # 池副本归一 dtype，避免 float32 列 setitem 拒写
    miss = df[need].isna().any(axis=1)
    n_miss = int(miss.sum())
    if n_miss == 0:
        if verbose:
            print("    [F3] 事件列全部非空（面板内），无需 fail-safe")
        return 0
    try:
        fc = pd.read_parquet(DC.FIN_FORECAST, columns=["ts_code", "ann_date", "p_change_max"])
        ex = pd.read_parquet(DC.FIN_EXPRESS, columns=["ts_code", "ann_date"])
        dv = pd.read_parquet(DC.FIN_DIVIDEND, columns=["ts_code", "ann_date", "cash_div_tax"])
    except Exception as e:
        if verbose:
            print(f"    [F3] fail-safe 源读取失败（{e}），NaN 保持中性，不阻断")
        return 0
    for _df in (fc, ex, dv):
        _df["ann_date"] = pd.to_datetime(_df["ann_date"], errors="coerce")
    fc = fc.dropna(subset=["ann_date"]).sort_values(["ts_code", "ann_date"])
    ex = ex.dropna(subset=["ann_date"]).sort_values(["ts_code", "ann_date"])
    dv = dv.dropna(subset=["ann_date"]).sort_values(["ts_code", "ann_date"])
    fc_g = {k: g for k, g in fc.groupby("ts_code")}
    ex_g = {k: g for k, g in ex.groupby("ts_code")}
    dv_g = {k: g for k, g in dv.groupby("ts_code")}

    def _latest(g, td64):
        """最近一次 ann_date<=T 的 (days, row)；无则 (9999, None)。"""
        if g is None:
            return 9999, None
        pos = int(np.searchsorted(g["ann_date"].values, td64, side="right"))
        if pos == 0:
            return 9999, None
        days = int((td64 - g["ann_date"].values[pos - 1]) / np.timedelta64(1, "D"))
        return days, g.iloc[pos - 1]

    filled = 0
    for i in df.index[miss]:
        row = df.loc[i]
        code = str(row["ts_code"])
        td64 = pd.Timestamp(row["trade_date"]).to_datetime64()
        fd, frow = _latest(fc_g.get(code), td64)
        if "fc_days_since" in need and pd.isna(row.get("fc_days_since")):
            df.at[i, "fc_days_since"] = fd
        if "fc_pchange" in need and pd.isna(row.get("fc_pchange")):
            df.at[i, "fc_pchange"] = 0.0 if (frow is None or fd > EVENT_WINDOW) else frow["p_change_max"]
        ed, _ = _latest(ex_g.get(code), td64)
        if "ex_days_since" in need and pd.isna(row.get("ex_days_since")):
            df.at[i, "ex_days_since"] = ed
        if "dv_year_sum" in need and pd.isna(row.get("dv_year_sum")):
            g = dv_g.get(code)
            s = 0.0
            if g is not None:
                gdates = g["ann_date"].values
                pos = int(np.searchsorted(gdates, td64, side="right"))
                lo = max(0, pos - 30)
                seg = g["cash_div_tax"].fillna(0.0).to_numpy()[lo:pos]
                segd = (td64 - gdates[lo:pos]).astype("timedelta64[D]").astype(int)
                s = float(seg[(segd <= DIV_WINDOW) & (segd >= 0)].sum())
            df.at[i, "dv_year_sum"] = s
        filled += 1
    if verbose:
        print(f"    [F3] 面板外事件 fail-safe：{filled}/{n_miss} 只重算"
              f"（forecast/express/dividend PIT<={df['trade_date'].max().date()}）")
    return filled


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
    ap.add_argument("--ens-live", action="store_true",
                    help="融合实盘选股落盘（需 --ensemble）：写 data/selections/g2_ens/<date>_g2_top10.csv 供 rebalance_ens 消费")
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
    # 2026-09-14 主库被重建为扁平结构（trade_date/ts_code 变为数据列 + 多余 'index' 列）：
    # 旧写法 .reset_index() 会得到 'index' 列 → KeyError: trade_date（当晚候选断链根因）。
    # 修复：探测 trade_date 是否在数据列，兼容新旧两种布局。
    # est 数据源优先 daily_basic（Tushare 日刷，当日真实估值口径，2026-09-26 方案A），
    # 文件缺失/读取失败回退主库 MAIN_DAILY（周更快照）。布局探测同下：兼容扁平/旧两种。
    daily = None
    if os.path.exists(DC.DAILY_BASIC):
        try:
            daily = pd.read_parquet(DC.DAILY_BASIC, columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
            if "trade_date" not in daily.columns:
                raise KeyError("trade_date 不在数据列，回退旧布局")
        except Exception:
            try:
                daily = pd.read_parquet(DC.DAILY_BASIC, columns=["pe_ttm", "turnover_rate"]).reset_index()
            except Exception:
                daily = None
    if daily is None:
        try:
            daily = pd.read_parquet(DC.MAIN_DAILY, columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
            if "trade_date" not in daily.columns:
                raise KeyError("trade_date 不在数据列，回退旧布局")
        except Exception:
            daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
    est = daily.reindex(idx)
    # ---- F6 est 时效自检（T-20260924-001 修复，2026-09-25 拍板落地）：主库周更滞后于快照日时 est 全 NaN ----
    # 实锤：MAIN_DAILY max=2026-09-11（周更只读库），快照日 09-23 起 reindex 全 miss → SC_F6 全池恒 5.0
    # （G2 上线即死维度，历史 16 交易日 CSV 全部 SC_F6=[5.0]）。且 est 带列全 NaN 会掩盖
    # scorecard_real 的快照 pe_ttm/turn_ma5 兜底（est 非 None 即整列用 est）。
    # 修复：est 命中率 < 50% 视为主库滞后 → est=None，F6 走快照兜底（build_g2_daily 已按 EPS 反推
    # T 日口径，G2_RUNBOOK 验证 pe/pb 中位误差 0.00%）。AB/ENS 臂同判定（ab_est/en_est 同源）。
    # 审核修正：历史产物 0902~0924 共 17 个 CSV 中 16 个恒 [5.0]，例外 20260911（09-14 重跑时
    # est 恰命中主库 max=09-11，F6=[7,10] 反证根因），非「16 日全部恒」。
    _est_hit = float(est["pe_ttm"].notna().mean()) if ("pe_ttm" in est.columns and len(est)) else 0.0
    if _est_hit < 0.5:
        print(f"    [F6] 主库 est 命中率 {_est_hit:.0%}（主库滞后快照日 {target.date()}）"
              f"→ est=None，F6 走快照 pe_ttm/turn_ma5 兜底")
        est = None
    # F2 评分卡口径：main_net 需为「元」（score_f2 阈值 5e7/1e7/1e8 均为元）
    # 快照 mf_main_net 是 moneyflow 五档「万元」，转元后作为评分卡主值；industry_pct 取 F5 当日自算值
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    # 真实列（如快照缺列则回退中性）
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan
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
    # F3 面板外事件 fail-safe（T-20260924-001 方案 C）：predict 已完成，day 原帧不动
    _fill_f3_events(pre, verbose=True)
    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre[f"SC_{f}"] = sc[f].values
    # ---- FAIL-LOUD 自检（T-20260919-002 防线D，2026-09-20）：评分卡坍缩立即中止落盘 ----
    # 防 2026-08-17~09-18 F2/F5 死代码事故复发：SC_F2/SC_F5 恒值或 main_net 全 NaN → 中止（宁可空仓不选错）
    if pre["SC_F2"].nunique() <= 1 or pre["SC_F5"].nunique() <= 1 or pre["main_net"].notna().sum() == 0:
        raise SystemExit("!! FAIL-LOUD：SC_F2/SC_F5 恒值或 main_net 全 NaN（F2/F5 主源失效）——已中止落盘，勿用此候选")

    print("[5/5] 概率Top10 → 红线 + 位置过滤 + Top%d ..." % args.top)
    # ---- 作用池对齐回测引擎（T-20260929-005 方案1，2026-09-29 诚哥拍板）----
    # 引擎 scan_rotate_cost_real：Top100(prob) → Top10(prob) → 红线 → total 排序
    # （PRE_POOL=100/TOP10=10，L221/L223 + simulate L247）。旧部署序红线直接作用于
    # Top100（picks 在红线后按 total 取），与引擎成交票重合仅 Jaccard 0.031；0825
    # 已验证配置反事实（XVAL5 工作包C）：日均超额 +0.1495% → -0.0293%、MDD
    # -16.3% → -30.8%，6 阈值档全负 → 判定必须改部署端（方案1，约3行）。
    # 空仓日不落候选 CSV：rebalance_g2.load_g2_selection 缺文件时宁缺毋滥 → 池空
    # 不买入、到期卖出照常执行，与引擎「无候选不买入」一致。
    pre = pre.nlargest(10, "prob").copy()
    pre = pre[pre["total_new"] >= args.threshold].copy()
    if len(pre) == 0:
        print(f"    !! 概率Top10(池{args.pool}→10)内无股票通过红线 {args.threshold} → 空仓（不落候选，rebalance 自然无买入）")
        return
    # ---- ST 风险警示剔除（T-20261012-001，诚哥拍板 2026-10-12）：候选生成端根治 ----
    # 背景：特征快照（g2_latest_features.parquet）无 is_st 列，候选链看不到 ST 状态，
    # 10-12 G2 实盘买入 *ST华鹏 603021.SH 实锤。主库 is_st 快照批量剔除（快照滞后
    # 约 2 周，新戴帽由执行端 st_guard 腾讯实时名称兜底）。
    _st_codes = PF.load_st_codes()
    if _st_codes:
        _st_hit = sorted(set(pre["ts_code"]) & _st_codes)
        if _st_hit:
            pre = pre[~pre["ts_code"].isin(_st_codes)].copy()
            print("    !! [ST剔除] 候选剔除 %d 只: %s" % (len(_st_hit), ",".join(_st_hit)))
        if len(pre) == 0:
            print("    !! ST 剔除后无候选 → 空仓（不落候选，宁缺毋滥）")
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
    # rank=1..N 按 total_new 降序（picks 已 nlargest 排序）；实盘 g2_config.TOP_N=3（2026-09-21 拍板 2→3），
    # 前向统计/降级闸按各臂实盘 TOP_N 过滤（G2=3/V1.3=5/ENS=5，见 paper_forward_downgrade.ARMS），
    # Top 全集写入用于更快累积 N>30 样本。
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
            ab_est = daily.reindex(pd.MultiIndex.from_arrays([ab_pre["trade_date"], ab_pre["ts_code"]])) \
                if est is not None else None
            if "main_net" not in ab_pre.columns and "mf_main_net" in ab_pre.columns:
                ab_pre["main_net"] = ab_pre["mf_main_net"] * 1e4
            if "industry_pct" not in ab_pre.columns and "ind_pct_ths" in ab_pre.columns:
                ab_pre["industry_pct"] = ab_pre["ind_pct_ths"]
            for c in ["main_net", "industry_pct", "volume_ratio"]:
                if c not in ab_pre.columns:
                    ab_pre[c] = np.nan
            _fill_f3_events(ab_pre)
            ab_sc = SR.compute_real_scorecard(ab_pre, ab_est)
            ab_pre["total_new"] = ab_sc["total_new_real"].values
            for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
                ab_pre[f"SC_{f}"] = ab_sc[f].values
            # ---- FAIL-LOUD 自检（T-20260919-002 防线D）：AB 臂评分卡坍缩立即中止 ----
            if ab_pre["SC_F2"].nunique() <= 1 or ab_pre["SC_F5"].nunique() <= 1 or ab_pre["main_net"].notna().sum() == 0:
                raise SystemExit("!! FAIL-LOUD（AB）：SC_F2/SC_F5 恒值或 main_net 全 NaN——已中止 AB 落盘")
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
            en_est = daily.reindex(pd.MultiIndex.from_arrays([en_pool["trade_date"], en_pool["ts_code"]])) \
                if est is not None else None
            if "main_net" not in en_pool.columns and "mf_main_net" in en_pool.columns:
                en_pool["main_net"] = en_pool["mf_main_net"] * 1e4
            if "industry_pct" not in en_pool.columns and "ind_pct_ths" in en_pool.columns:
                en_pool["industry_pct"] = en_pool["ind_pct_ths"]
            for c in ["main_net", "industry_pct", "volume_ratio"]:
                if c not in en_pool.columns:
                    en_pool[c] = np.nan
            _fill_f3_events(en_pool)
            en_sc = SR.compute_real_scorecard(en_pool, en_est)
            en_pool["total_new"] = en_sc["total_new_real"].values
            for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
                en_pool[f"SC_{f}"] = en_sc[f].values
            # 空池先判：空表 nunique=0 会误触发下方 FAIL-LOUD（假阳性中止主臂落盘）
            if len(en_pool) == 0:
                print(f"    [ENS] 融合预筛后空池（概率/红线前）→ 空仓")
            else:
                # ---- FAIL-LOUD 自检（T-20260919-002 防线D）：融合臂评分卡坍缩立即中止 ----
                if en_pool["SC_F2"].nunique() <= 1 or en_pool["SC_F5"].nunique() <= 1 or en_pool["main_net"].notna().sum() == 0:
                    raise SystemExit("!! FAIL-LOUD（ENS）：SC_F2/SC_F5 恒值或 main_net 全 NaN——已中止 ENS 落盘")
                en_pool = en_pool[en_pool["total_new"] >= args.threshold].copy()
                # ---- ST 风险警示剔除（T-20261012-001）：候选生成端根治（同 G2 主臂）----
                _st_codes_e = PF.load_st_codes()
                if _st_codes_e and len(en_pool) > 0:
                    _st_hit_e = sorted(set(en_pool["ts_code"]) & _st_codes_e)
                    if _st_hit_e:
                        en_pool = en_pool[~en_pool["ts_code"].isin(_st_codes_e)].copy()
                        print("    !! [ENS][ST剔除] 候选剔除 %d 只: %s" % (len(_st_hit_e), ",".join(_st_hit_e)))
                if len(en_pool) == 0:
                    print(f"    [ENS] 融合无候选通过红线 {args.threshold:.0f}/ST剔除后为空 → 空仓")
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
                    # 融合实盘选股落盘（--ens-live，供 rebalance_ens 消费：池=top10 by ens，买入=by total_new）
                    if args.ens_live:
                        os.makedirs(ENS_SELECT, exist_ok=True)
                        date_str2 = target.strftime("%Y%m%d")
                        _ens_out = en_picks[["ts_code", "total_new", "ens", "en_pv", "en_pg"]].copy()
                        _ens_out["trade_date"] = date_str2
                        _ens_out = _ens_out.rename(columns={"en_pv": "pv", "en_pg": "pg"})
                        _ens_out["total_new"] = _ens_out["total_new"].round(1)
                        _ens_out["ens"] = _ens_out["ens"].round(4)
                        _live_csv = os.path.join(ENS_SELECT, f"{date_str2}_g2_top{args.top}.csv")
                        _ens_out.to_csv(_live_csv, index=False, encoding="utf-8-sig")
                        print(f"    [ENS-LIVE] 融合实盘选股已写 {_live_csv}（{len(_ens_out)} 只，Top{args.top} by ens）")


if __name__ == "__main__":
    main()
