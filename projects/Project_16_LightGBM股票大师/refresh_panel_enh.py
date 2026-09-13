# coding: utf-8
"""T-20260912-001：enh 面板刷新（feature_panel_v3_enh.parquet 的唯一 writer）。

背景（2026-09-12 定位）：
  - v3_enh 面板（33 特征）2026-08-23 在研发电脑一次性构建后无任何刷新路径，冻结在 2026-08-14；
  - 消费端两处：train_g2.py（周更重训，6 个 enh 慢变量逐行 asof）与 build_g2_daily.py
    （实盘评分，13 个财务/事件/行业特征 asof 最新行）——实盘慢变量停在 8/14 整一个月；
  - 原始构建脚本遗失；本脚本按「可复现口径」全量重建，历史与冻结面板对照验证。

口径（2026-09-12 实证）：
  - 行网格 + 27 基础特征 + 4 事件特征（ex_days_since/fc_pchange/dv_year_sum/ex_yoy）：
    从当次重建的 feature_panel_v2.parquet 按 features_v3_enh.json 切片（与冻结面板 100% 一致）。
  - turnover_rank：真实换手率按交易日全市场百分位排名。主库区用原生 turnover_rate；
    增量区（incremental 仅 OHLCV，merged 中为 ffill 旧值）用 vol(手)/float_share(万股) 反推
    （build_g2_daily F6 已验证口径，对官方中位误差 0.00%）。历史 100% 复现。
  - industry_mom20：stock_basic.industry（当前 vintage）+ 全市场未复权 close 20 日动量，
    按 (trade_date, industry) 等权均值。与冻结面板相关 0.983~0.9966——原始 08-23 版行业映射
    快照随研发电脑遗失不可恢复，此为可找回的最佳口径；偏差已记录 T-20260912-001。

用法：
  python refresh_panel_enh.py             # 全量重建 + 校验 + 备份 + 覆盖
  python refresh_panel_enh.py --dry-run   # 只构建校验，不写盘
校验失败（exit 1）保留旧面板不覆盖。挂在 run_scheduled.ps1 的 daily/retrain 链
refresh_panel_v3.py 之后（v2 面板与 merged 日线由它先备好）。
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

DATA = DC.DATA_DIR
LIVE = DC.LIVE_DIR
V2_PANEL = os.path.join(DATA, "feature_panel_v2.parquet")
ENH_PANEL = os.path.join(DATA, "feature_panel_v3_enh.parquet")
META = os.path.join(DATA, "features_v3_enh.json")
MERGED = os.path.join(LIVE, "merged_daily_full.parquet")
INCR = os.path.join(LIVE, "incremental_daily.parquet")
STOCK_BASIC = os.path.join(DC.ASTOCK_DIR, "basic", "stock_basic.parquet")

ENH6 = ["ex_days_since", "fc_pchange", "dv_year_sum", "ex_yoy", "industry_mom20", "turnover_rank"]
HIST_START = "2018-10-01"   # mom20 需要 20 个交易日回看，面板起点 2019-01-10
MAX_LAG_DAYS = 7            # v2 面板落后 merged 最大自然日数


def log(msg):
    print(msg, flush=True)


def fail(msg):
    log(f"!! [enh刷新] {msg}")
    log("!! [enh刷新] 校验失败，保留旧面板不动，exit 1")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # ---------- 0. 前置检查 ----------
    meta = json.load(open(META, encoding="utf-8"))
    feat33 = meta["feature_cols"]
    base27 = [c for c in feat33 if c not in ENH6]
    if len(feat33) != 33 or len(base27) != 27:
        fail(f"features_v3_enh.json 特征数异常: 33!={len(feat33)}")
    for p, name in [(V2_PANEL, "v2 面板"), (MERGED, "merged 日线"), (STOCK_BASIC, "stock_basic")]:
        if not os.path.exists(p):
            fail(f"{name} 不存在: {p}")

    log("[enh][1/6] 读取 v2 面板切片（33 特征 + label/fwd_ret）...")
    import pyarrow.parquet as pq
    v2_cols = pq.ParquetFile(V2_PANEL).schema_arrow.names
    # v2 面板产出 27 基础 + 4 事件特征；industry_mom20/turnover_rank 由本脚本自算
    need = [c for c in feat33 if c not in ("industry_mom20", "turnover_rank")] + \
           ["label", "fwd_ret", "trade_date", "ts_code"]
    missing = [c for c in need if c not in v2_cols]
    if missing:
        fail(f"v2 面板缺列: {missing}（refresh_panel_v3 是否成功？）")
    df = pd.read_parquet(V2_PANEL, columns=need)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)

    # ---------- 1. merged 日线：真实换手率 + 全市场 mom20 ----------
    log("[enh][2/6] 读取 merged 日线，构造真实换手率与 20 日动量...")
    mg = pd.read_parquet(MERGED, columns=["close", "turnover_rate", "vol", "float_share"]).reset_index()
    mg["trade_date"] = pd.to_datetime(mg["trade_date"])
    mg["ts_code"] = mg["ts_code"].astype(str)
    mg = mg[mg["trade_date"] >= pd.Timestamp(HIST_START)]
    # 增量区边界：incremental 最早日（主库/增量日期不重叠）
    if os.path.exists(INCR):
        incr_min = pd.read_parquet(INCR, columns=["trade_date"])["trade_date"].min()
        incr_min = pd.to_datetime(incr_min)
    else:
        incr_min = pd.Timestamp("2099-01-01")
    mg = mg.sort_values(["ts_code", "trade_date"])
    # 真实换手率：主库区原生值；增量区 vol(手)/float_share(万股) 反推（已验证口径）
    fs = mg["float_share"].astype(float).replace(0.0, np.nan)
    infered = (mg["vol"].astype(float) / fs).where(fs.notna())
    is_incr = mg["trade_date"] >= incr_min
    mg["true_turnover"] = np.where(is_incr, infered, mg["turnover_rate"].astype(float))
    n_infered = int(is_incr.sum())
    log(f"    merged {len(mg):,} 行 | 增量区(>= {incr_min.date()}) {n_infered:,} 行换手率反推")
    # NaN 检查：增量区反推失败比例
    if n_infered > 0:
        bad = mg.loc[is_incr, "true_turnover"].isna().mean()
        if bad > 0.10:
            fail(f"增量区换手率反推 NaN 比例 {bad:.2%}（float_share 缺失？）")

    close = mg["close"].astype(float)
    mg["mom20"] = close / close.groupby(mg["ts_code"]).shift(20) - 1.0

    # ---------- 2. turnover_rank：全市场按日百分位 ----------
    log("[enh][3/6] 计算 turnover_rank（全市场按日百分位）...")
    mg["turnover_rank"] = mg.groupby("trade_date")["true_turnover"].rank(pct=True)

    # ---------- 3. industry_mom20：行业等权均值 ----------
    log("[enh][4/6] 计算 industry_mom20（stock_basic.industry 等权均值）...")
    sb = pd.read_parquet(STOCK_BASIC, columns=["ts_code", "industry"])
    sb["ts_code"] = sb["ts_code"].astype(str)
    ind_map = sb.dropna(subset=["industry"]).set_index("ts_code")["industry"]
    mg["industry"] = mg["ts_code"].map(ind_map)
    ind_mom = (mg.dropna(subset=["industry", "mom20"])
                 .groupby(["trade_date", "industry"])["mom20"].mean()
                 .rename("industry_mom20").reset_index())
    log(f"    (date, industry) 组合 {len(ind_mom):,} | 行业 {ind_mom['industry'].nunique()}")

    # ---------- 4. 并入面板 ----------
    log("[enh][5/6] 并入 6 个 enh 特征...")
    df = df.merge(mg[["ts_code", "trade_date", "turnover_rank"]], on=["ts_code", "trade_date"],
                  how="left", validate="many_to_one")
    df["_industry"] = df["ts_code"].map(ind_map)
    df = df.merge(ind_mom, left_on=["trade_date", "_industry"], right_on=["trade_date", "industry"],
                  how="left", validate="many_to_one")
    df = df.drop(columns=["_industry", "industry"])
    for c in ENH6:
        if c not in df.columns:
            fail(f"合并后缺特征 {c}")
        df[c] = df[c].astype("float32")

    new_max = df["trade_date"].max()
    old_max = df["trade_date"].min()
    log(f"    面板 {len(df):,} 行 | {old_max.date()} ~ {new_max.date()}")

    # ---------- 5. 校验门禁 ----------
    log("[enh][6/6] 校验门禁...")
    # 5.1 v2 面板新鲜度
    mg_max = mg["trade_date"].max()
    lag = (mg_max - new_max).days
    if lag > MAX_LAG_DAYS:
        fail(f"面板末日 {new_max.date()} 落后 merged 末日 {mg_max.date()} {lag} 自然日（>{MAX_LAG_DAYS}）")

    # 5.2 enh 特征 NaN 率（事件特征 0 填充语义除外，industry/turnover 不应大面积缺失）
    for c in ["industry_mom20", "turnover_rank"]:
        nan_rate = df[c].isna().mean()
        if nan_rate > 0.02:
            fail(f"{c} NaN 率 {nan_rate:.2%}（>2%，行业映射/换手数据异常）")
        log(f"    {c} NaN 率 {nan_rate:.4f}")

    # 5.2b days_since 负值回归检查（T-20260912-001 修复 NaT 溢出后不允许再现）
    for c in ["ex_days_since", "fc_days_since", "sc_days_since"]:
        if c in df.columns:
            n_neg = int((df[c] < 0).sum())
            if n_neg:
                fail(f"{c} 存在 {n_neg} 行负值（build_features_v2 NaT 溢出回归？T-20260912-001）")

    # 5.3 与旧面板重叠期对照
    if os.path.exists(ENH_PANEL):
        prev = pd.read_parquet(ENH_PANEL)
        prev["trade_date"] = pd.to_datetime(prev["trade_date"])
        prev["ts_code"] = prev["ts_code"].astype(str)
        prev_max = prev["trade_date"].max()
        if new_max < prev_max:
            fail(f"新面板末日 {new_max.date()} 早于旧面板 {prev_max.date()}（数据回退）")
        if len(df) < 0.9 * len(prev):
            fail(f"新面板行数 {len(df):,} < 旧面板 90%（{len(prev):,}，宇宙坍缩？）")
        chk_dates = sorted(prev["trade_date"].unique())[-3:]
        pv = prev[prev["trade_date"].isin(chk_dates)]
        nv = df[df["trade_date"].isin(chk_dates)]
        m = pv.merge(nv, on=["ts_code", "trade_date"], how="inner",
                     suffixes=("_old", "_new"), validate="one_to_one")
        if len(m) < 0.9 * len(pv):
            fail(f"重叠期 join 匹配率 {len(m)/len(pv):.2%}（<90%，行网格漂移）")
        # 4 事件特征 + turnover_rank：确定性口径，必须 ~100% 一致
        for c in ["ex_days_since", "fc_pchange", "dv_year_sum", "ex_yoy", "turnover_rank"]:
            a, b = m[c + "_old"].astype(float), m[c + "_new"].astype(float)
            eq = np.isclose(a, b, atol=1e-6, equal_nan=True)
            rate = eq.mean()
            if rate < 0.99:
                fail(f"重叠期 {c} 复现率 {rate:.2%}（<99%，口径漂移）")
            log(f"    重叠期 {c:16s} 复现率 {rate*100:.2f}%")
        # industry_mom20：vintage 偏差已知，只查相关性不阻塞
        a, b = m["industry_mom20_old"].astype(float), m["industry_mom20_new"].astype(float)
        corr = a.corr(b)
        if corr < 0.90:
            fail(f"重叠期 industry_mom20 相关 {corr:.4f}（<0.90，口径异常漂移）")
        log(f"    重叠期 industry_mom20     相关 {corr:.4f}（vintage 偏差在案 T-20260912-001）")
    else:
        log("    （无旧面板，跳过对照）")

    # ---------- 6. 落盘 ----------
    if args.dry_run:
        log("[enh] --dry-run：校验通过，不写盘。")
        return
    bak = ENH_PANEL + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(ENH_PANEL):
        shutil.copy2(ENH_PANEL, bak)
        log(f"    旧面板已备份: {os.path.basename(bak)}")
    out_cols = base27 + ["trade_date", "ts_code", "label", "fwd_ret"] + ENH6
    df = df[out_cols]
    df.to_parquet(ENH_PANEL, index=False)
    meta["n_rows"] = len(df)
    meta["date_range"] = [str(df["trade_date"].min().date()), str(df["trade_date"].max().date())]
    meta["refreshed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["refresh_writer"] = "refresh_panel_enh.py（T-20260912-001）"
    meta["industry_mom20_note"] = ("stock_basic.industry 当前 vintage + 全市场未复权 20 日动量等权均值；"
                                   "原始 08-23 映射快照遗失，相关 0.983~0.9966，见 T-20260912-001")
    meta["turnover_rank_note"] = "主库区原生 turnover_rate；增量区 vol/float_share 反推（F6 已验证口径）"
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    log(f"[enh] 完成: {os.path.basename(ENH_PANEL)} | {len(df):,} 行 | "
        f"{df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")


if __name__ == "__main__":
    main()
