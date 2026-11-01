# -*- coding: utf-8 -*-
"""build_v13_final.py — V1.3 口径对齐方案B：信号日晚间 scorecard_real 定名单（PAPER 阶段）。

SPEC：specs/V13口径对齐方案B落地_SPEC_20260930.md（T-20260929-006）。
口径锚点 = scan_rotate_cost_real.build_per_day()（R8 引擎）：
  池 = 模型 prob Top10（非 combo）→ scorecard_real 真实评分卡 → 红线 58 → total 降序 Top5。
真实数据 asof = 信号日当日收盘后（无前视）：
  F2 main_net   : 新浪（主，与回测 fund_flow_real 同源家族）→ Tushare moneyflow 兜底（19:30 已刷）
  F5 industry_pct: 行情源真实 pct_chg × stock_basic.industry 等权均值（同 build_panel_v3_sc）
                   行情源 = 主库 D:/astock/daily（17:40 Tushare 日刷）优先 → incremental_daily 兜底
  F6 est        : daily_basic（Tushare 日刷 20:05）→ 缺失回退面板 pe_ttm/turn_ma5（scorecard_real 内置）
  volume_ratio  : 信号日真实 vol / 主库前5日 vol 均值（回测用主库预计算列，此处现算，诚实偏差见 SPEC §2）

2026-10-31 数据源修正（T-20261031-003 同源）：incremental_daily 自 10-23 断流（xtdata 连不上行情服务），
旧实现把「增量库最新日」当闸 → 每晚 [SKIP] 且 exit=0，纸面链静默停摆 5 个交易日。现改为主库优先、
增量库兜底，两库均无当日则 fail-loud（不再静默 SKIP 成假绿）。

PAPER 阶段红线：不接 rebalance、不下单、不改任何生产文件（只新增 <T>_final_list.csv/.meta.json）。

用法：
  python build_v13_final.py                 # 信号日 = 行情最新日（主库/增量库取较新；非交易日自动 [SKIP]）
  python build_v13_final.py --date 2026-09-30   # 研究模式：显式指定信号日
  python build_v13_final.py --dry-run       # 只打印不落盘
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP
import scorecard_real as SR
from g2_realtime import fetch_main_net_sina

PROJ = DC.PROJECT_DIR
SELECT_DIR = os.path.join(DC.DATA_DIR, "selections")
INCR = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")
MAIN_DAILY = os.path.join(DC.ASTOCK_DIR, "daily", "stock_daily.parquet")
SNAP = os.path.join(DC.LIVE_DIR, "latest_features.parquet")
MONEYFLOW = os.path.join(DC.ASTOCK_DIR, "moneyflow", "moneyflow.parquet")
STOCK_BASIC = os.path.join(DC.BASIC_DIR, "stock_basic.parquet")
DAILY_BASIC = DC.DAILY_BASIC

POOL_N = 10        # 引擎 per_day 口径：prob Top10
REDLINE = 58.0     # 与引擎 BT_THRESHOLD / rebalance_daily REDLINE 同值
TOP_N = 5          # 与 rebalance_daily TOP_N 同值


def sha256_of(path, max_mb=50):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:16]


def fail(msg):
    print("!! FAIL-LOUD: %s" % msg)
    sys.exit(1)


def load_market_day(target):
    """信号日全天行情（ts_code / close / pre_close / vol），主库优先、增量库兜底。

    2026-10-31 修（T-20261031-003 同源）：增量库 incremental_daily 自 10-23 起断流
    （xtdata 连不上行情服务），旧实现以「增量库最新日 < 今日」为闸 → 每晚 [SKIP] 且 exit=0，
    纸面链静默停摆 5 个交易日。主库 D:/astock/daily 由 Quant_Astock_DailyTushare_1740
    （工作日 17:40）每日补到当日，与 build_g2_daily 的 max(主库, 增量) 口径一致。

    返回 (day_df, source)；day_df 列：ts_code(str) / close / pre_close / vol。
    **单位**：主库 vol 与增量库 volume 均为「手」（000001.SZ 2026-10-23 实测 1448805.6 vs
    1448806）；仅 amount 不同（主库千元 / 增量库元，差 1000 倍，本函数不取 amount）。
    两库均未覆盖 → fail-loud（20:20 时主库应已含当日）。
    """
    main_err = ""
    try:
        if os.path.isfile(MAIN_DAILY):
            m = pd.read_parquet(MAIN_DAILY, columns=["close", "pre_close", "vol"])
            if not isinstance(m.index, pd.MultiIndex):
                m = m.set_index(["trade_date", "ts_code"])
            m.index.names = ["trade_date", "ts_code"]
            m = m[m.index.get_level_values("trade_date") == target]
            if len(m):
                d = m.reset_index()
                d["ts_code"] = d["ts_code"].astype(str)
                return d[["ts_code", "close", "pre_close", "vol"]], "main"
            main_err = "主库无 %s" % target.date()
    except Exception as e:
        main_err = "主库读取失败: %s" % e
    if os.path.isfile(INCR):
        inc = pd.read_parquet(INCR)
        inc["trade_date"] = pd.to_datetime(inc["trade_date"])
        inc = inc[inc["trade_date"] == target]
        if len(inc):
            inc = inc[inc["suspendFlag"].fillna(0) == 0]
            d = pd.DataFrame({
                "ts_code": inc["ts_code"].astype(str).values,
                "close": inc["close"].values,
                "pre_close": inc["preClose"].values,
                "vol": inc["volume"].values,   # 增量库 volume 单位=手，与主库 vol 同量纲
            })
            print("    [WARN] 行情走增量库兜底（%s）" % (main_err or "主库缺该日"))
            return d, "incr"
    fail("主库与增量库均无信号日 %s 行情（%s）—— 数据链断裂，不落盘" %
         (target.date(), main_err or "增量库无该日"))


def latest_market_date():
    """最近可用行情日（主库 / 增量库取较新），返回 (Timestamp, source)。"""
    cands = []
    try:
        if os.path.isfile(MAIN_DAILY):
            idx = pd.read_parquet(MAIN_DAILY, columns=["close"]).index
            if isinstance(idx, pd.MultiIndex):
                cands.append(("main", pd.Timestamp(idx.get_level_values("trade_date").max())))
            else:
                d = pd.read_parquet(MAIN_DAILY, columns=["trade_date"])
                cands.append(("main", pd.Timestamp(d["trade_date"].max())))
    except Exception as e:
        print("    [WARN] 主库最新日读取失败: %s" % e)
    try:
        if os.path.isfile(INCR):
            d = pd.read_parquet(INCR, columns=["trade_date"])
            cands.append(("incr", pd.Timestamp(d["trade_date"].max())))
    except Exception as e:
        print("    [WARN] 增量库最新日读取失败: %s" % e)
    if not cands:
        fail("主库与增量库均不可读（D:/astock/daily + incremental_daily）")
    src, d = max(cands, key=lambda x: x[1])
    return d, src


def compute_volume_ratio_real(pool_codes, target, day):
    """今日真实量比 = 信号日 vol / 主库（+周增量）前5个交易日 vol 均值。

    day 由 load_market_day() 提供（主库 vol / 增量库 volume 均为「手」，同量纲）；
    回测用主库预计算列，此处现算（诚实偏差见 SPEC §2）。
    """
    daily = DC.read_main_daily(columns=["vol"])
    dates_all = sorted(set(daily.index.get_level_values(0)))
    prev_dates = [d for d in dates_all if d < target][-5:]
    out = {}
    for code in pool_codes:
        try:
            s = daily.loc[(slice(None), code), "vol"]
        except KeyError:
            out[code] = np.nan
            continue
        s = s[s.index.get_level_values(0).isin(prev_dates)]
        if len(s) < 3:  # 至少 3 天历史
            out[code] = np.nan
            continue
        out[code] = float(s.mean())
    today_vol = day.set_index("ts_code")["vol"]
    vr = {}
    for code in pool_codes:
        ma = out.get(code)
        v = today_vol.get(code)
        vr[code] = float(v) / ma if (ma and ma > 0 and v and v > 0) else np.nan
    return vr


def fetch_main_net(pool_codes, target):
    """F2 主力净额（元）：新浪主源（回测同源家族）→ Tushare moneyflow 兜底 → NaN 中性 5 分。"""
    src, vals = {}, {}
    try:
        rt = fetch_main_net_sina(list(pool_codes), target_date=target.strftime("%Y-%m-%d"))
    except Exception as e:
        print("    [WARN] 新浪资金流接口异常: %s" % e)
        rt = {}
    for code in pool_codes:
        v = (rt.get(code) or {}).get("main_net_yuan")
        if v is not None and np.isfinite(v):
            vals[code] = float(v)
            src[code] = "sina"
        else:
            vals[code] = np.nan
            src[code] = ""
    # moneyflow 兜底（单位万元 → 元）
    if np.isnan(list(vals.values())).any() and os.path.isfile(MONEYFLOW):
        try:
            mf = pd.read_parquet(MONEYFLOW).reset_index()
            mf["trade_date"] = pd.to_datetime(mf["trade_date"])
            mf["ts_code"] = mf["ts_code"].astype(str)
            mf = mf[mf["trade_date"] == target].set_index("ts_code")
            for code in pool_codes:
                if np.isnan(vals.get(code, np.nan)) and code in mf.index:
                    r = mf.loc[code]
                    if isinstance(r, pd.DataFrame):
                        r = r.iloc[0]
                    v = (float(r.get("buy_lg_amount", 0)) + float(r.get("buy_elg_amount", 0))
                         - float(r.get("sell_lg_amount", 0)) - float(r.get("sell_elg_amount", 0))) * 1e4
                    vals[code] = v
                    src[code] = "tushare_mf"
        except Exception as e:
            print("    [WARN] moneyflow 兜底读取失败: %s" % e)
    n_missing = sum(1 for c in pool_codes if src.get(c) == "")
    if n_missing:
        print("    [WARN] F2 main_net 缺失 %d/%d 只（该部分 F2 走中性 5 分）" % (n_missing, len(pool_codes)))
    if all(src.get(c) == "" for c in pool_codes):
        fail("F2 main_net 全部缺失（新浪+moneyflow 双源失效）—— 宁可空名单不选错")
    return vals, src


def compute_industry_pct(pool_codes, target, day, src_tag):
    """F5 行业当日涨幅：信号日真实涨跌幅（close/pre_close-1）× stock_basic.industry 等权均值。

    day/src_tag 由 load_market_day() 提供（主库优先、增量兜底，2026-10-31 修）；
    公式与 build_panel_v3_sc（回测面板 industry_pct 来源）一致。
    """
    if not os.path.isfile(STOCK_BASIC):
        print("    [WARN] stock_basic.parquet 缺失，F5 全走中性 5 分")
        return {c: np.nan for c in pool_codes}, {c: "" for c in pool_codes}
    basic = pd.read_parquet(STOCK_BASIC).reset_index()
    basic["ts_code"] = basic["ts_code"].astype(str)
    if "industry" not in basic.columns:
        print("    [WARN] stock_basic 无 industry 列，F5 全走中性 5 分")
        return {c: np.nan for c in pool_codes}, {c: "" for c in pool_codes}
    ind_map = basic.set_index("ts_code")["industry"].to_dict()
    day = day.copy()
    day["pct"] = np.where(day["pre_close"] > 0, day["close"] / day["pre_close"] - 1.0, np.nan)
    day = day.dropna(subset=["pct"])
    day["industry"] = day["ts_code"].map(ind_map)
    day = day.dropna(subset=["industry"])
    if len(day) < 100:
        fail("F5 行业涨幅基数异常（有效行 %d < 100）—— 行情源 %s 可能不新鲜" % (len(day), src_tag))
    ind_mean = day.groupby("industry")["pct"].mean()
    vals, src = {}, {}
    for code in pool_codes:
        ind = ind_map.get(code)
        if ind is not None and ind in ind_mean.index:
            vals[code] = float(ind_mean[ind]) * 100.0  # score_f5 阈值是百分数口径（>3 / >1.5）
            src[code] = "%s_eqw" % src_tag
        else:
            vals[code] = np.nan
            src[code] = ""
    if all(src.get(c) == "" for c in pool_codes):
        fail("F5 industry_pct 全部缺失 —— 行业映射失效，宁可空名单不选错")
    return vals, src


def build_est(pool, target):
    """F6 est（pe_ttm/turnover_rate，信号日真实口径）：daily_basic（20:05 刷至信号日）→ None 回退面板。"""
    if not os.path.isfile(DAILY_BASIC):
        return None, "panel_fallback", None
    try:
        db = pd.read_parquet(DAILY_BASIC).reset_index()
    except Exception as e:
        print("    [WARN] daily_basic 读取失败（F6 回退面板）: %s" % e)
        return None, "panel_fallback", None
    db["trade_date"] = pd.to_datetime(db["trade_date"])
    db["ts_code"] = db["ts_code"].astype(str)
    db = db[db["trade_date"] == target]
    if len(db) == 0 or "pe_ttm" not in db.columns or "turnover_rate" not in db.columns:
        print("    [WARN] daily_basic 无 %s 数据（F6 回退面板 pe_ttm/turn_ma5）" % target.date())
        return None, "panel_fallback", None
    db = db.set_index(["trade_date", "ts_code"])[["pe_ttm", "turnover_rate"]]
    idx = pd.MultiIndex.from_arrays([pool["trade_date"], pool["ts_code"]])
    est = db.reindex(idx)
    covered = int(est["pe_ttm"].notna().sum())
    if covered == 0:
        print("    [WARN] daily_basic 对池内覆盖 0/%d（F6 回退面板）" % len(pool))
        return None, "panel_fallback", None
    db_max = target
    return est, "daily_basic", db_max


def crosscheck_proxy(pool_out, target):
    """验收自检：同信号日 model_top10 若存在，重叠代码的代理分 F1/F3/F4 应逐值一致（同快照同代码）。"""
    path = os.path.join(SELECT_DIR, "%s_model_top10.csv" % target.strftime("%Y%m%d"))
    if not os.path.isfile(path):
        print("    [自检] 无 %s_model_top10.csv，跳过代理分交叉校验" % target.strftime("%Y%m%d"))
        return
    mt = pd.read_csv(path)
    mt["ts_code"] = mt["ts_code"].astype(str)
    merged = pool_out.merge(mt, on="ts_code", suffixes=("", "_mt"))
    n = len(merged)
    if n == 0:
        print("    [自检] 池与 model_top10 无重叠（prob Top10 vs combo Top10 池差异，正常）")
        return
    ok = True
    for f in ("F1", "F3", "F4"):
        if ("SC_%s" % f) not in merged.columns:
            continue
        diff = (merged[f] - merged["SC_%s" % f]).abs()
        bad = int((diff > 1e-9).sum())
        if bad:
            ok = False
            print("    [自检][FAIL] %s 与 model_top10 SC_%s 不一致 %d/%d" % (f, f, bad, n))
    print("    [自检] 代理分交叉校验（重叠 %d 只）: %s" % (n, "PASS" if ok else "FAIL"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="信号日 YYYY-MM-DD（默认行情最新日：主库/增量库取较新）")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mkt_max, mkt_src = latest_market_date()
    if args.date:
        target = pd.Timestamp(args.date)
    else:
        target = mkt_max
        today = pd.Timestamp(datetime.date.today())
        if mkt_max < today:
            print("[SKIP] 行情最新日 %s（%s）< 今日 %s（非交易日或 17:40 主库链未跑）" % (
                mkt_max.date(), mkt_src, today.date()))
            return 0
    if target != mkt_max:
        print("[SKIP] 指定日 %s 非行情最新日 %s（研究模式请直接用最新日）" % (target.date(), mkt_max.date()))
        return 0

    print("[1/6] 加载快照 + 模型（与 16:30 deploy_predict 同源）...")
    meta_path = os.path.join(DC.DATA_DIR, "features_v3.json")
    panel_path = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
    feat_cols = json.load(open(meta_path, encoding="utf-8"))["feature_cols"]
    df = pd.read_parquet(panel_path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = DP._prefer_snapshot(df, feat_cols)
    day = df[df["trade_date"] == target].copy()
    if len(day) == 0:
        fail("快照/面板无 %s 行（16:30 链异常）" % target.date())
    model_path = DC.model_file("_v3")
    booster = lgb.Booster(model_file=model_path)
    print("    信号日 %s | %d 行 | 模型 %s" % (target.date(), len(day), os.path.basename(model_path)))

    print("[2/6] 模型推理 → prob Top%d 池（引擎口径，非 combo）..." % POOL_N)
    day["model_prob"] = booster.predict(day[feat_cols].astype("float32").values)
    pool = day.nlargest(POOL_N, "model_prob").copy()
    pool_codes = list(pool["ts_code"])

    print("[3/6] 真实数据接入（asof 信号日，无前视）...")
    mkt_day, mkt_day_src = load_market_day(target)
    print("    行情源 %s | %d 只 | 信号日 %s" % (mkt_day_src, len(mkt_day), target.date()))
    vr = compute_volume_ratio_real(pool_codes, target, mkt_day)
    mn, mn_src = fetch_main_net(pool_codes, target)
    ip, ip_src = compute_industry_pct(pool_codes, target, mkt_day, mkt_day_src)
    pool["volume_ratio"] = pool["ts_code"].map(vr)
    pool["main_net"] = pool["ts_code"].map(mn)
    pool["industry_pct"] = pool["ts_code"].map(ip)

    print("[4/6] scorecard_real 真实评分卡 + 红线 %.1f ..." % REDLINE)
    est, f6_src, db_max = build_est(pool, target)
    # est 按代码挂到行上（避免后续排序错位）
    if est is not None:
        pool["pe_ttm_real"] = est["pe_ttm"].values
        pool["turnover_rate_real"] = est["turnover_rate"].values
    else:
        pool["pe_ttm_real"] = np.nan
        pool["turnover_rate_real"] = np.nan
    sc = SR.compute_real_scorecard(pool, est)
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pool[f] = sc[f].values
    pool["total"] = sc["total_new_real"].values
    # 代理版总分（透明对照，非决策依据）
    pool["proxy_score_total"] = DP.compute_scorecard(pool)["total"].values
    if pool["main_net"].notna().sum() == 0:
        fail("main_net 全 NaN（已在取数步拦截，防御复检）")
    n_f2_nan = int(pool["F2"].isna().sum())
    if n_f2_nan:
        print("    [WARN] F2 NaN %d/%d（score_f2 对 NaN 给中性 5 分）" % (n_f2_nan, len(pool)))

    print("[5/6] 排名与红线过滤（total 降序，并列按 prob）...")
    pool = pool.sort_values(["total", "model_prob", "ts_code"], ascending=[False, False, True]).reset_index(drop=True)
    pool["redline_pass"] = pool["total"] >= REDLINE
    pool["rank"] = range(1, len(pool) + 1)
    buys = pool[pool["redline_pass"]].head(args.top)
    n_pass = int(pool["redline_pass"].sum())
    print("    过红线 %d/%d | 拟买 Top%d：%s" % (
        n_pass, len(pool), args.top, ", ".join(buys["ts_code"]) if len(buys) else "(空)"))

    print("[6/6] 落盘（PAPER 阶段：不接 rebalance、不下单）...")
    date_str = target.strftime("%Y%m%d")
    basic_name = {}
    if os.path.isfile(STOCK_BASIC):
        try:
            b = pd.read_parquet(STOCK_BASIC).reset_index()
            b["ts_code"] = b["ts_code"].astype(str)
            if "name" in b.columns:
                basic_name = b.set_index("ts_code")["name"].to_dict()
        except Exception:
            pass
    out = pd.DataFrame({
        "ts_code": pool["ts_code"],
        "name": pool["ts_code"].map(basic_name),
        "model_prob": pool["model_prob"].round(4),
        "proxy_score_total": pool["proxy_score_total"].round(1),
        "F1": pool["F1"], "F2": pool["F2"], "F3": pool["F3"],
        "F4": pool["F4"], "F5": pool["F5"], "F6": pool["F6"],
        "total": pool["total"].round(2),
        "rank": pool["rank"],
        "redline_pass": pool["redline_pass"].astype(bool),
        "buy_rank": [i + 1 if i < len(buys) else 0 for i in range(len(buys))] + [0] * (len(pool) - len(buys)),
        "main_net": pool["main_net"],
        "industry_pct": pool["industry_pct"].round(4),
        "volume_ratio": pool["volume_ratio"].round(4),
        "pe_ttm": pool["pe_ttm_real"],
        "turnover_rate": pool["turnover_rate_real"],
        "f2_source": pool["ts_code"].map(mn_src),
        "f5_source": pool["ts_code"].map(ip_src),
        "trade_date": date_str,
    })
    crosscheck_proxy(out, target)

    if not args.dry_run:
        csv_path = os.path.join(SELECT_DIR, "%s_final_list.csv" % date_str)
        out.to_csv(csv_path, index=False, encoding="utf-8-sig")
        meta = {
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "signal_date": date_str,
            "spec": "specs/V13口径对齐方案B落地_SPEC_20260930.md",
            "pool": "model_prob top%d (engine per_day)" % POOL_N,
            "redline": REDLINE, "top": args.top,
            "model": {"path": model_path, "sha256_16": sha256_of(model_path)},
            "snapshot": {"path": SNAP, "sha256_16": sha256_of(SNAP)},
            "data_max_date": {
                "market": str(mkt_max.date()),
                "market_source": mkt_src,
                "daily_basic": str(db_max.date()) if db_max is not None else None,
                "moneyflow": None,
            },
            "f2_source_counts": {k: sum(1 for v in mn_src.values() if v == k) for k in ("sina", "tushare_mf", "")},
            "f5_source": "%s_eqw(stock_basic.industry)" % mkt_day_src,
            "f6_source": f6_src,
            "paper_phase": True,
        }
        if os.path.isfile(MONEYFLOW):
            try:
                mf_dates = pd.read_parquet(MONEYFLOW).reset_index()["trade_date"]
                meta["data_max_date"]["moneyflow"] = str(pd.to_datetime(mf_dates).max().date())
            except Exception:
                pass
        meta_path_out = os.path.join(SELECT_DIR, "%s_final_list.meta.json" % date_str)
        with open(meta_path_out, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print("    CSV :", csv_path)
        print("    META:", meta_path_out)
    else:
        print("    [dry-run] 不落盘")
    print(out[["rank", "ts_code", "name", "model_prob", "F1", "F2", "F3", "F4", "F5", "F6",
               "total", "redline_pass", "buy_rank"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
