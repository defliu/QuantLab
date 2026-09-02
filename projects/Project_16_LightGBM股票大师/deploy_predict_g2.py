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

HERE = DC.PROJECT_DIR
DATA = DC.DATA_DIR
LIVE = DC.LIVE_DIR
REAL = os.path.join(DATA, "real")
SELECT = os.path.join(DATA, "selections", "g2")

G2_MODEL, G2_META = DC.g2_live()  # 读 live 指针（周更重训 promote 后自动跟随），缺失回退 08-25 初始 live
SNAP = os.path.join(LIVE, "g2_latest_features.parquet")
LIVE_LOG = os.path.join(REAL, "paper_forward_live.csv")


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
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--pool", type=int, default=100)
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
    # 快照 mf_main_net 是 moneyflow 五档「万元」，转元后作为评分卡 fallback；industry_pct 取 F5 当日自算值
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    # F2 实时覆盖：对预选池逐股拉新浪当日主力净额（main_net_yuan 直接为「元」，评分卡口径）
    import g2_realtime as RT
    pool_codes = pre["ts_code"].astype(str).tolist()
    rt = RT.fetch_main_net_sina(pool_codes, target_date=target.strftime("%Y-%m-%d"))
    rt_hit = 0
    for c in pool_codes:
        if c not in rt:
            continue
        m = pre["ts_code"].astype(str) == c
        pre.loc[m, "main_net"] = rt[c].get("main_net_yuan", np.nan)
        if "mf_main_net" in pre.columns:
            pre.loc[m, "mf_main_net"] = rt[c].get("mf_main_net", np.nan)
        if "mf_main_ratio" in pre.columns:
            pre.loc[m, "mf_main_ratio"] = rt[c].get("mf_main_ratio", np.nan)
        rt_hit += 1
    print(f"    F2 实时覆盖 {rt_hit}/{len(pool_codes)} 只（新浪当日主力净额，元口径）| 未命中回退快照周更(×1e4)")
    # F5 为快照当日自算板块涨幅（build_g2_daily 已算），此处直接用 pre 的 industry_pct
    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre[f"SC_{f}"] = sc[f].values

    print("[5/5] 红线 + Top%d ..." % args.top)
    pre = pre[pre["total_new"] >= args.threshold].copy()
    if len(pre) == 0:
        print(f"    !! 预选池({args.pool})内无股票通过红线 {args.threshold} → 空仓")
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
    md += ["", "> ⚠️ F2 为主力净额实时值（新浪当日，预选池内逐股采集，未命中回退周更）；F5 为增量库当日行业涨幅自算。",
           "> ⚠️ 其余新因子（lhb/北向/研报/行业资金流）为 D:/astock 周更最新可用值（可能滞后数天）；买入时需按一字板/停牌复核可执行性。",
           "> 独立研究信号，不构成投资建议。"]
    md_path = os.path.join(SELECT, f"{date_str}_g2_selection.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    # 追加 live 日志（code 必须用 ts_code，不能用 iterrows 的整数 index）；幂等去重
    rows = [{"date": target.date(), "code": s["ts_code"], "total_new": s["total_new"], "prob": s["prob"]}
            for _, s in picks.iterrows()]
    _append_live_dedup(rows)
    print("    CSV:", csv)
    print("    MD :", md_path)
    print("    live log:", LIVE_LOG)


if __name__ == "__main__":
    main()
