# -*- coding: utf-8 -*-
"""F5/F2 死代码修正口径重放 + 实盘持仓收益对比（T-20260919-002 关联）。

任务书：D:/QuantLab/data/schedules/de_r8_scripts/f5fix_rerun_task.txt
- 修正口径：F2 = Tushare moneyflow 主源（lg+elg 主净，万元×1e4 转元，量比按 NaN），
  F5 = 881 板块当日涨幅重建（成交额加权自算，成分表静态 8/14）
- 实际口径：实盘落盘 top2 CSV（SC_F2=新浪兜底、SC_F5=5.0 死代码）
- 前向收益：T 日收盘买入（前复权），T+1 起逐日 live_trail 出场
  （STOP -7% / TP +15% / TRAIL 8%激活+回撤8%保本 / MATURE 15 交易日，
   一字跌停顺延，一字涨停/停牌/无量不买）
- 只读数据；只写本目录 rerun_summary.json / rerun_daily.csv / rerun_report.md
"""
import glob
import json
import os
import datetime

import numpy as np
import pandas as pd

OUT = os.path.dirname(os.path.abspath(__file__))
ASTOCK = r"D:/astock"
PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
SEL = os.path.join(PROJ, "data", "selections", "g2")
MERGED = os.path.join(PROJ, "data_live", "merged_daily_full.parquet")
MF_PATH = os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet")
THS_PATH = os.path.join(ASTOCK, "board", "ths_daily.parquet")
COMP_PATH = os.path.join(ASTOCK, "board", "source", "parquet行业概念板块全量更新到20260814",
                         "parquet", "行业概念板块", "行业板块成分汇总_同花顺.parquet")

W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}
STOP, TP, TRAIL_PCT, TRAIL_ACT = -0.07, 0.15, 0.08, 0.08
MATURE_N = 15
BUY_END = pd.Timestamp("2026-09-11")     # 任务书：买入日 T ≤ 2026-09-11
WIN_START = pd.Timestamp("2026-08-17")


def score_f2_no_liangbi(net):
    """review_full.score_f2 严格实现，量比=NaN（CSV 无量比，任务书口径）。net 单位：元。
    量比条件全部不满足：>5e7 与 >1e7 档的量比门槛 False → 净流入一律落 >0→6.0 档。"""
    if net is None or not np.isfinite(net):
        return 5.0
    if net > 0:
        return 6.0
    # 净流出但量比≥2 → 5.0：量比 NaN 不满足
    if net <= -1e8:
        return 1.0
    return 2.0


def score_f5(ind_pct):
    """review_full.score_f5。ind_pct 单位：%。"""
    if ind_pct is None or not np.isfinite(ind_pct):
        return 5.0
    if ind_pct > 3:
        return 10.0
    if ind_pct > 1.5:
        return 8.0
    if ind_pct > 0:
        return 5.0
    if ind_pct > -1:
        return 4.0
    return 2.0


def total_new(f1, f2, f3, f4, f5, f6):
    return (W["F1"] * f1 + W["F2"] * f2 + W["F3"] * f3 + W["F4"] * f4 + W["F5"] * f5) * 10.0 \
        + W["F6"] * f6 * 10.0


def limit_pct(code):
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("4", "8")):
        return 0.30
    return 0.10


# ---------------------------------------------------------------- 数据加载
print("=" * 72)
print("[阶段0] 数据加载（只读）")

# 前向行情：merged_daily_full（主库+增量合并，覆盖到 2026-09-18）
mg = pd.read_parquet(MERGED)
mg = mg.reset_index()
if "index" in mg.columns:
    mg = mg.drop(columns=["index"])
mg["trade_date"] = pd.to_datetime(mg["trade_date"])
mg["ts_code"] = mg["ts_code"].astype(str)
mg = mg[(mg["trade_date"] >= "2026-06-01")].copy()
print("    merged_daily_full: %s ~ %s | 行 %d" %
      (mg["trade_date"].min().date(), mg["trade_date"].max().date(), len(mg)))

# 增量段（09-14+，来自 QMT incr）缺 adj_factor → 逐股前向填充；涨跌停价缺失按 pre_close 推算
inc_mask = mg["adj_factor"].isna()
print("    adj_factor 缺失行（增量段）: %d" % inc_mask.sum())
mg = mg.sort_values(["ts_code", "trade_date"])
mg["adj_factor"] = mg.groupby("ts_code")["adj_factor"].ffill()
# down_limit/up_limit 缺失（增量段无此列）→ pre_close × (1∓limit)（不 ffill：逐日基准变化，ffill 会拿到过期价）
need_dl = mg["down_limit"].isna() & mg["pre_close"].notna() & (mg["pre_close"] > 0)
mg.loc[need_dl, "down_limit"] = (mg.loc[need_dl, "pre_close"]
                                 * (1 - mg.loc[need_dl, "ts_code"].map(limit_pct))).round(2)
need_ul = mg["up_limit"].isna() & mg["pre_close"].notna() & (mg["pre_close"] > 0)
mg.loc[need_ul, "up_limit"] = (mg.loc[need_ul, "pre_close"]
                               * (1 + mg.loc[need_ul, "ts_code"].map(limit_pct))).round(2)
print("    down_limit 推算 %d 行 / up_limit 推算 %d 行" % (int(need_dl.sum()), int(need_ul.sum())))

calendar = sorted(mg["trade_date"].unique())
cal_idx = {d: i for i, d in enumerate(calendar)}
print("    日历: %s ~ %s 共 %d 个交易日" % (pd.Timestamp(calendar[0]).date(),
                                           pd.Timestamp(calendar[-1]).date(), len(calendar)))

# 板块成分（同花顺 881，静态 2026-08-14）
comp = pd.read_parquet(COMP_PATH)
comp["股票代码"] = comp["股票代码"].astype(str)
comp["指数代码"] = comp["指数代码"].astype(str)
stk2ind = comp.drop_duplicates("股票代码").set_index("股票代码")["指数代码"].to_dict()
print("    成分表: %d 板块 / %d 股票（静态 2026-08-14）" %
      (comp["指数代码"].nunique(), comp["股票代码"].nunique()))

# 实盘 selections：日期 = 窗口内 top2 ∪ top10 文件日期（09-08 无 top2 → 实际持仓回退 top10 前 2 行）
_dset = set()
for pat in ("*_g2_top2.csv", "*_g2_top10.csv"):
    for f in glob.glob(os.path.join(SEL, pat)):
        d = pd.Timestamp(os.path.basename(f)[:8])
        if WIN_START <= d <= BUY_END:
            _dset.add(d.strftime("%Y%m%d"))
sel_dates = sorted(_dset)
print("    窗口内买入日: %d 个（%s ~ %s）" % (len(sel_dates), sel_dates[0], sel_dates[-1]))

rows_by_day = {}
for ds in sel_dates:
    top2f = os.path.join(SEL, "%s_g2_top2.csv" % ds)
    top10f = os.path.join(SEL, "%s_g2_top10.csv" % ds)
    if os.path.isfile(top10f):
        uni = pd.read_csv(top10f)
        uni["csv_rank"] = range(1, len(uni) + 1)
    else:
        uni = pd.read_csv(top2f)
        uni["csv_rank"] = range(1, len(uni) + 1)
    if os.path.isfile(top2f):
        act = pd.read_csv(top2f)
    else:
        act = uni.head(2).drop(columns=["csv_rank"])
    rows_by_day[ds] = {"universe": uni, "actual": act,
                       "has_top10": os.path.isfile(top10f)}

# 窗口内涉及的全部代码
all_codes = set()
for ds in rows_by_day:
    for k in ("universe", "actual"):
        all_codes.update(rows_by_day[ds][k]["ts_code"].astype(str))
all_codes = sorted(all_codes)
print("    涉及股票: %d 只" % len(all_codes))

# F2 主源：Tushare moneyflow（窗口日 + 涉及代码）
win_dates = [pd.Timestamp(ds) for ds in sel_dates]
mf = pd.read_parquet(MF_PATH, columns=["buy_lg_amount", "buy_elg_amount",
                                       "sell_lg_amount", "sell_elg_amount"])
mf = mf.reset_index()
mf["trade_date"] = pd.to_datetime(mf["trade_date"])
mf["ts_code"] = mf["ts_code"].astype(str)
mf = mf[mf["ts_code"].isin(all_codes) & mf["trade_date"].isin(win_dates)].copy()
mf["main_yuan"] = (mf["buy_lg_amount"] + mf["buy_elg_amount"]
                   - mf["sell_lg_amount"] - mf["sell_elg_amount"]) * 1e4  # 万元→元
f2_map = {(r.trade_date, r.ts_code): r.main_yuan for r in mf.itertuples()}
print("    moneyflow 主源: 窗口内命中 %d / %d（%s ~ %s 全库覆盖到 2026-09-18）"
      % (len(f2_map), len(win_dates) * len(all_codes), sel_dates[0], sel_dates[-1]))

# F5 主源：881 板块当日涨幅自算（成交额加权，复刻 build_g2_daily/g2_realtime 口径，
# 数据源用 merged_daily_full 当日全市场行情——原增量库 08-17~09-13 已轮转删除）
day_slice = mg[mg["trade_date"].isin(win_dates)].copy()
day_slice = day_slice[day_slice["close"].notna() & day_slice["pre_close"].notna()
                      & (day_slice["pre_close"] != 0) & (day_slice["amount"].fillna(0) > 0)]
day_slice["stk_pct"] = (day_slice["close"] / day_slice["pre_close"] - 1.0) * 100.0
day_slice["board"] = day_slice["ts_code"].map(stk2ind)
day_slice = day_slice[day_slice["board"].notna()]
wsum = (day_slice["stk_pct"].fillna(0.0) * day_slice["amount"]).groupby(
    [day_slice["trade_date"], day_slice["board"]]).sum()
asum = day_slice["amount"].groupby([day_slice["trade_date"], day_slice["board"]]).sum()
ind_pct_map = (wsum / asum).to_dict()
print("    F5 板块涨幅自算: %d (日,板块) 组" % len(ind_pct_map))

# F5 交叉验证：ths_daily 官方 881 板块指数涨幅（当日有数据的日期）
td = pd.read_parquet(THS_PATH)
td = td.reset_index() if "trade_date" not in td.columns else td
td["trade_date"] = pd.to_datetime(td["trade_date"])
td["ts_code"] = td["ts_code"].astype(str)
td = td[(td["ts_code"].str.startswith("881")) & td["trade_date"].isin(win_dates)]
ths_same_day = {(r.trade_date, r.ts_code): r.pct_change for r in td.itertuples()}
vals_ths, vals_mine = [], []
for (d, b), pv in ind_pct_map.items():
    if (d, b) in ths_same_day:
        vals_ths.append(ths_same_day[(d, b)])
        vals_mine.append(pv)
f5_valid = None
if vals_ths:
    diff = np.abs(np.array(vals_mine) - np.array(vals_ths))
    f5_valid = {"n_boards_compared": len(diff),
                "median_abs_diff_pp": round(float(np.median(diff)), 3),
                "p90_abs_diff_pp": round(float(np.percentile(diff, 90)), 3),
                "within_0p5pp_pct": round(float((diff <= 0.5).mean() * 100), 1),
                "corr": round(float(np.corrcoef(vals_mine, vals_ths)[0, 1]), 4)}
    print("    F5 验证(vs ths_daily 官方板块涨幅): n=%d 中位|差|=%.3fpp 90分位=%.3fpp "
          "≤0.5pp占%.1f%% corr=%.4f" % (f5_valid["n_boards_compared"],
          f5_valid["median_abs_diff_pp"], f5_valid["p90_abs_diff_pp"],
          f5_valid["within_0p5pp_pct"], f5_valid["corr"]))

# ---------------------------------------------------------------- 前向模拟
# 每只股票一个按日历对齐的面板（后复权价 = 原始价×adj_factor，收益比值口径等价前复权）
mg2 = mg.set_index(["ts_code", "trade_date"]).sort_index()
adj_end = mg.groupby("ts_code")["adj_factor"].last().to_dict()


def stock_panel(code):
    sub = mg2.xs(code, level="ts_code").reindex(calendar)
    return sub


def simulate(code, t_idx, panel_cache):
    """T 日收盘买入，T+1 起逐日 live_trail 出场（日线近似：close 触发、high 计峰值）。
    返回 (entry_fq, exit_day, exit_px_fq, ret, reason, hold_days)；不可买返回 (None, reason)。"""
    sub = panel_cache.get(code)
    if sub is None:
        sub = stock_panel(code)
        panel_cache[code] = sub
    d = calendar[t_idx]
    row = sub.loc[d]
    # 买入约束：有行情 / 无量 / 一字涨停 不买（实盘执行约束）
    if not (np.isfinite(row["close"]) and row["close"] > 0):
        return None, "SUSPEND_NOBAR", None, None, None, None
    if not (np.isfinite(row["vol"]) and row["vol"] > 0):
        return None, "NO_VOLUME", None, None, None, None
    ul = row["up_limit"]
    if np.isfinite(ul) and np.isfinite(row["high"]) and np.isfinite(row["low"]) \
            and row["high"] == row["low"] and row["close"] >= ul - 1e-9:
        return None, "LIMIT_UP_SEALED", None, None, None, None
    adj_t = row["adj_factor"]
    if not np.isfinite(adj_t) or adj_t <= 0:
        return None, "NO_ADJ", None, None, None, None
    entry = row["close"] * adj_t  # 后复权（比值口径）
    o = sub["open"].values.astype(float)
    h = sub["high"].values.astype(float)
    c = sub["close"].values.astype(float)
    dl = sub["down_limit"].values.astype(float)
    af = sub["adj_factor"].values.astype(float)
    peak = entry
    last_j = t_idx
    for j in range(t_idx + 1, len(calendar)):
        if not (np.isfinite(c[j]) and c[j] > 0 and np.isfinite(af[j]) and af[j] > 0):
            continue  # 停牌/缺行情：不推进
        last_j = j
        if np.isfinite(h[j]) and h[j] > 0:
            peak = max(peak, h[j] * af[j])
        px = c[j] * af[j]
        ret = px / entry - 1.0
        # 一字/开盘跌停卖不掉：顺延
        if np.isfinite(o[j]) and np.isfinite(dl[j]) and o[j] > 0 and o[j] <= dl[j] + 1e-9:
            continue
        if j - t_idx >= MATURE_N:
            return entry, j, px, ret, "MATURE", j - t_idx
        if ret <= STOP:
            return entry, j, px, ret, "STOP", j - t_idx
        if ret >= TP:
            return entry, j, px, ret, "TP", j - t_idx
        if peak >= entry * (1 + TRAIL_ACT):
            trail_line = max(entry, peak * (1 - TRAIL_PCT))
            if px <= trail_line:
                return entry, j, px, ret, "TRAIL", j - t_idx
    # 数据走完仍未出场 → 截断（浮动收益按最后可得收盘）
    if last_j > t_idx:
        af_l = af[last_j]
        px = c[last_j] * af_l
        return entry, last_j, px, px / entry - 1.0, "TRUNCATED", last_j - t_idx
    return entry, None, None, np.nan, "TRUNCATED_NODATA", None


# ---------------------------------------------------------------- 重放
print("=" * 72)
print("[阶段1] 逐日重放：修正评分 → 重排 → 前向模拟")

detail_rows = []
day_records = []
panel_cache = {}
sim_cache = {}
n_f2_changed = n_f5_changed = n_total_checked = n_total_mismatch = 0

for ds in sel_dates:
    d = pd.Timestamp(ds)
    t_idx = cal_idx.get(d)
    info = rows_by_day[ds]
    uni = info["universe"].copy()
    uni["ts_code"] = uni["ts_code"].astype(str)
    act_df = info["actual"].copy()
    act_df["ts_code"] = act_df["ts_code"].astype(str)
    act_top2 = act_df["ts_code"].tolist()
    # 打分面板 = 重排宇宙(top10/top2) ∪ 实际持仓独有码（后者只记录不参与重排，如 09-10 双跑差异）
    extra = act_df[~act_df["ts_code"].isin(set(uni["ts_code"]))].copy()
    if len(extra):
        print("    !! %s 实际 top2 未见于重排宇宙(只记录收益,不参与重排): %s"
              % (ds, extra["ts_code"].tolist()))
    uni["in_universe"] = True
    if len(extra):
        extra["csv_rank"] = 999
        extra["in_universe"] = False
    sdf = pd.concat([uni, extra], ignore_index=True)

    # 修正 F2 / F5 / total_new（对打分面板全量计算；重排仅用 in_universe 行）
    f2s, f5s, tnews = [], [], []
    for r in sdf.itertuples():
        net = f2_map.get((d, r.ts_code), np.nan)
        f2s.append(score_f2_no_liangbi(net))
        board = stk2ind.get(r.ts_code)
        ind = ind_pct_map.get((d, board), np.nan) if board else np.nan
        # 与 build_g2_daily 一致：有板块映射但当日板块无数据 → 填 0.0 → score_f5(0)=4.0
        if not np.isfinite(ind):
            ind_eff = 0.0 if board else np.nan
        else:
            ind_eff = ind
        f5s.append(score_f5(ind_eff))
        tnews.append(total_new(r.SC_F1, f2s[-1], r.SC_F3, r.SC_F4, f5s[-1], r.SC_F6))
        # 口径自检：用 CSV 原 SC_* 复算原 total_new 应与 CSV 落盘一致
        t_old = total_new(r.SC_F1, r.SC_F2, r.SC_F3, r.SC_F4, r.SC_F5, r.SC_F6)
        n_total_checked += 1
        if abs(t_old - r.total_new) > 0.051:
            n_total_mismatch += 1
    sdf["F2_new"] = f2s
    sdf["F5_new"] = f5s
    sdf["total_new_corr"] = tnews
    n_f2_changed += int((sdf["F2_new"] != sdf["SC_F2"]).sum())
    n_f5_changed += int((sdf["F5_new"] != sdf["SC_F5"]).sum())
    sdf = sdf.drop_duplicates("ts_code", keep="first")

    # 修正后 top2（总分降序，平分按 CSV 原名次；仅重排宇宙内）
    corr = sdf[sdf["in_universe"]].sort_values(
        ["total_new_corr", "csv_rank"], ascending=[False, True]).head(2)
    corr_top2 = corr["ts_code"].tolist()

    for arm, codes in (("actual", act_top2), ("corrected", corr_top2)):
        for rank, code in enumerate(codes, 1):
            key = (ds, code)
            if key not in sim_cache:
                sim_cache[key] = simulate(code, t_idx, panel_cache)
            entry, x1, x2, x3, x4, x5 = sim_cache[key]
            urow = sdf[sdf["ts_code"] == code].iloc[0]
            ae = adj_end.get(code, np.nan)
            detail_rows.append({
                "trade_date": ds, "arm": arm, "rank": rank, "ts_code": code,
                "has_top10": info["has_top10"],
                "SC_F1": urow.SC_F1, "SC_F2": urow.SC_F2, "SC_F3": urow.SC_F3,
                "SC_F4": urow.SC_F4, "SC_F5": urow.SC_F5, "SC_F6": urow.SC_F6,
                "F2_new": urow.F2_new, "F5_new": urow.F5_new,
                "total_new_csv": urow.total_new, "total_new_corr": urow.total_new_corr,
                "buyable": entry is not None,
                "skip_reason": x1 if entry is None else "",
                "entry_day": ds if entry is not None else "",
                "entry_px_fq": round(entry / ae, 3) if entry is not None and np.isfinite(ae) and ae > 0 else np.nan,
                "exit_day": str(pd.Timestamp(calendar[x1]).date()) if (entry is not None and isinstance(x1, (int, np.integer))) else "",
                "exit_px_fq": round(x2 / ae, 3) if (entry is not None and x2 is not None and np.isfinite(ae) and ae > 0) else np.nan,
                "ret": x3 if entry is not None else np.nan,
                "reason": x4 if entry is not None else "",
                "hold_days": x5,
            })
    day_records.append({
        "date": ds, "has_top10": info["has_top10"],
        "actual_top2": act_top2, "corrected_top2": corr_top2,
        "composition_differs": set(act_top2) != set(corr_top2),
    })

det = pd.DataFrame(detail_rows)
print("    逐笔明细 %d 行（%d 日 × 2 口径 × ≤2 只）" % (len(det), len(sel_dates)))
print("    口径自检: CSV total_new 复算一致 %d/%d" % (n_total_checked - n_total_mismatch, n_total_checked))
print("    F2 分变化 %d 只 / F5 分变化 %d 只（相对 CSV 落盘）" % (n_f2_changed, n_f5_changed))

# ---------------------------------------------------------------- 汇总
def arm_stats(df):
    b = df[df["buyable"]].copy()
    closed = b[b["reason"].isin(["STOP", "TP", "TRAIL", "MATURE"])]
    trunc = b[~b["reason"].isin(["STOP", "TP", "TRAIL", "MATURE"])]
    out = {
        "positions": int(len(df)),
        "buyable": int(len(b)),
        "unbuyable": int(len(df) - len(b)),
        "closed": int(len(closed)),
        "truncated": int(len(trunc)),
    }
    if len(closed):
        out.update({
            "closed_mean_ret_pct": round(float(closed["ret"].mean() * 100), 3),
            "closed_median_ret_pct": round(float(closed["ret"].median() * 100), 3),
            "closed_win_rate_pct": round(float((closed["ret"] > 0).mean() * 100), 1),
            "closed_sum_ret_pct": round(float(closed["ret"].sum() * 100), 3),
        })
    else:
        out.update({"closed_mean_ret_pct": None, "closed_median_ret_pct": None,
                    "closed_win_rate_pct": None, "closed_sum_ret_pct": None})
    if len(b):
        out.update({
            "all_mean_ret_pct_float": round(float(b["ret"].mean() * 100), 3),
            "all_sum_ret_pct_float": round(float(b["ret"].sum() * 100), 3),
            "all_win_rate_pct_float": round(float((b["ret"] > 0).mean() * 100), 1),
        })
    out["exit_reason_dist"] = b["reason"].value_counts().to_dict() if len(b) else {}
    out["skip_reason_dist"] = df[~df["buyable"]]["skip_reason"].value_counts().to_dict()
    return out


stats_act = arm_stats(det[det["arm"] == "actual"])
stats_corr = arm_stats(det[det["arm"] == "corrected"])

# 逐日配对（buyable 持仓的等权日收益）
day_rows = []
for rec in day_records:
    ds = rec["date"]
    a = det[(det["trade_date"] == ds) & (det["arm"] == "actual") & det["buyable"]]
    c = det[(det["trade_date"] == ds) & (det["arm"] == "corrected") & det["buyable"]]
    ra = float(a["ret"].mean()) if len(a) else np.nan
    rc = float(c["ret"].mean()) if len(c) else np.nan
    day_rows.append({"date": ds, "has_top10": rec["has_top10"],
                     "actual_top2": ",".join(rec["actual_top2"]),
                     "corrected_top2": ",".join(rec["corrected_top2"]),
                     "composition_differs": rec["composition_differs"],
                     "n_actual_buyable": len(a), "n_corrected_buyable": len(c),
                     "day_ret_actual_pct": round(ra * 100, 3) if np.isfinite(ra) else None,
                     "day_ret_corrected_pct": round(rc * 100, 3) if np.isfinite(rc) else None,
                     "day_diff_pct": round((rc - ra) * 100, 3) if (np.isfinite(ra) and np.isfinite(rc)) else None})
day_df = pd.DataFrame(day_rows)
div_days = day_df[day_df["composition_differs"]].copy()
div_days["abs_diff"] = pd.to_numeric(div_days["day_diff_pct"], errors="coerce").abs()
top5_div = div_days.sort_values("abs_diff", ascending=False, na_position="last").head(5)

# 逐笔配对（两臂共有代码）
piv = det[det["buyable"]].pivot_table(index=["trade_date", "ts_code"], columns="arm", values="ret")
both = piv.dropna()
paired_diff = (both["corrected"] - both["actual"])

# 分歧持仓（各臂独有）
bb = det[det["buyable"]].copy()
bb["in_both"] = bb.set_index(["trade_date", "ts_code"]).index.isin(both.index)
only_a = bb[(bb["arm"] == "actual") & ~bb["in_both"]].reset_index(drop=True)
only_c = bb[(bb["arm"] == "corrected") & ~bb["in_both"]].reset_index(drop=True)
CLOSED = ["STOP", "TP", "TRAIL", "MATURE"]


def _uniq(df):
    if not len(df):
        return {"n": 0}
    return {"n": int(len(df)),
            "closed": int(df["reason"].isin(CLOSED).sum()),
            "mean_ret_pct": round(float(df["ret"].mean() * 100), 3),
            "sum_ret_pct": round(float(df["ret"].sum() * 100), 3),
            "detail": [{"date": r.trade_date, "code": r.ts_code,
                        "ret_pct": round(r.ret * 100, 2), "reason": r.reason}
                       for r in df.itertuples()]}


uuniq = det.drop_duplicates(subset=["trade_date", "ts_code"])
summary = {
    "task": "F5/F2 死代码修正口径重放 + 实盘持仓收益对比",
    "task_ref": "T-20260919-002 关联；任务书 data/schedules/de_r8_scripts/f5fix_rerun_task.txt",
    "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "window": {"buy_days": len(sel_dates), "first": sel_dates[0], "last": sel_dates[-1],
               "forward_data_until": str(pd.Timestamp(calendar[-1]).date())},
    "coverage": {
        "daily_merged": "2009-01-05 ~ 2026-09-18（主库 09-11 + 增量 09-14~09-18 合并；前向行情比任务书假设的 9/11 多 5 个交易日）",
        "moneyflow": "2007-01-04 ~ 2026-09-18，窗口内逐日 ~5545 只全覆盖",
        "incr_rollover_gap": "data_live/incremental_daily.parquet 仅保留 2026-09-14~09-18，08-17~09-13 增量已轮转删除 → F5 自算改用 merged_daily_full 同公式重建",
        "ths_daily_881": "覆盖到 2026-09-11 但周更不完整（8/14=10 板块、8/19=6、8/20=12、8/25=17），仅作交叉验证",
        "comp_table": "90 个 881 板块 / 5557 只股票，静态 2026-08-14",
        "selections": "top2: 08-17~09-11 全有（09-08 缺→回退 top10 前2行）；top10: 仅 09-02~09-11 有（10行/日）→ 08-17~09-01 共 12 天修正重排宇宙退化为 top2 本身",
    },
    "f5_validation_vs_ths_daily": f5_valid,
    "score_changes": {"f2_changed_vs_csv": int(n_f2_changed), "f5_changed_vs_csv": int(n_f5_changed),
                      "total_new_recheck_mismatch": int(n_total_mismatch),
                      "sc_f2_dist": uuniq["SC_F2"].value_counts().sort_index().to_dict(),
                      "f2_new_dist": uuniq["F2_new"].value_counts().sort_index().to_dict(),
                      "sc_f5_dist": uuniq["SC_F5"].value_counts().sort_index().to_dict(),
                      "f5_new_dist": uuniq["F5_new"].value_counts().sort_index().to_dict(),
                      "total_new_csv_mean": round(float(uuniq["total_new_csv"].mean()), 2),
                      "total_new_corr_mean": round(float(uuniq["total_new_corr"].mean()), 2),
                      "below_threshold60_after_corr": int((uuniq["total_new_corr"] < 60).sum())},
    "actual_arm": stats_act,
    "corrected_arm": stats_corr,
    "unique_positions": {"actual_only": _uniq(only_a), "corrected_only": _uniq(only_c)},
    "paired_common_positions": {
        "n": int(len(both)),
        "mean_diff_pp": round(float(paired_diff.mean() * 100), 4) if len(both) else None,
    },
    "composition_diff_days": int(len(div_days)),
    "divergence_agg": {
        "n_days": int(len(div_days)),
        "corrected_better_days": int((pd.to_numeric(div_days["day_diff_pct"], errors="coerce") > 0).sum()),
        "corrected_worse_days": int((pd.to_numeric(div_days["day_diff_pct"], errors="coerce") < 0).sum()),
        "cum_day_diff_pp": round(float(pd.to_numeric(div_days["day_diff_pct"], errors="coerce").sum()), 3),
        "mean_day_diff_pp": round(float(pd.to_numeric(div_days["day_diff_pct"], errors="coerce").mean()), 3),
    },
    "top5_divergence_days": top5_div[["date", "actual_top2", "corrected_top2",
                                      "day_ret_actual_pct", "day_ret_corrected_pct",
                                      "day_diff_pct"]].to_dict("records"),
}

# ---------------------------------------------------------------- 输出
det.to_csv(os.path.join(OUT, "rerun_daily.csv"), index=False, encoding="utf-8-sig")
day_df.to_csv(os.path.join(OUT, "rerun_daily_byday.csv"), index=False, encoding="utf-8-sig")
with open(os.path.join(OUT, "rerun_summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

print("=" * 72)
print("[阶段2] 汇总")
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
print("\n逐日:")
print(day_df.to_string(index=False))
print("\n分歧日 top5:")
print(top5_div.to_string(index=False))
print("\n明细/汇总已写:", OUT)
