# -*- coding: utf-8 -*-
"""XVAL T-20260928 命题3/4/5 —— 回测框架独立重推（只读生产代码与数据）。

框架口径 = 生产回测引擎 scan_rotate_cost_real.py（audit_stats_walkforward.py 同款 env）：
  面板 feature_panel_v3_enh2_n3_bt.parquet + g2_strong_real 0825 模型（43特征）
  + 真实评分卡（scorecard_real 语义，向量化复刻 xval_de_scorecard）
  + 红线 60 / N=10 / TOP2 / 滑点0.1% / fixed 出场(-7%/+15%) / open→open 可执行口径
  + 测试期 2024-07-01 ~ 2026-08-14

三步保真校验：
  V2) 我的 build（top10 池 total/mkt）vs 生产 BT.build_per_day（窗口 2026-08-10~08-14，逐值相等）
  V3) 我的 simulate vs 生产 BT.simulate（同输入全期，trades/daily_ret 一致）
      + 锚点：全期 thr60 ≈ 102 笔 / 日均超额 ≈ +0.15%（audit_stats_walkforward.py:136 源码内锚）

命题3：红线 60/58/55/52/50 日均通过数（Top100 池=deploy 预选池口径；Top10 池=回测引擎实际口径）。
命题4：绝对阈值 F5 vs rank-F5（当日横截面分位）的 IS/OOS 日均超额与最大回撤。
命题5：audit_stats_walkforward.align_market 按位置对齐缺陷的量化（OOS 位置对齐 vs 按日期 join）。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import xval_de_scorecard as SCV

PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet")
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
META = os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260825.json")
START = pd.Timestamp("2024-07-01")
END = pd.Timestamp("2026-08-14")
IS_CUT = pd.Timestamp("2025-07-01")     # 与 audit_stats_walkforward.py:42 一致
N_HOLD = 10
TOP = 2
SLIP = 0.001
STOP, TP = -0.07, 0.15
DELIST_DAYS, DELIST_LOSS = 60, 0.5
CACHE = os.path.join(HERE, "xval_de_bt_cache.parquet")
CACHE_MKT = os.path.join(HERE, "xval_de_bt_cache_mkt.parquet")


# ---------------- 费用（qmt_config 实盘口径，scan_rotate_cost_real.py:128-142 同款） ----------------
def is_sh(code):
    return code.startswith("6")


def sell_fee(amt, code, slip):
    comm = max(5.0, amt * 0.0002)
    return comm + amt * 0.0005 + (amt * 0.00001 if is_sh(code) else 0.0) + amt * slip


def buy_fee(amt, code, slip):
    comm = max(5.0, amt * 0.0002)
    return comm + (amt * 0.00001 if is_sh(code) else 0.0) + amt * slip


def build():
    """构造逐日 Top100 打分数据（语义=生产 build_per_day 的预选池步）。"""
    t0 = time.time()
    feat_cols = json.load(open(META, encoding="utf-8"))["feature_cols"]
    cols = ["trade_date", "ts_code", "main_net", "industry_pct", "fwd_ret",
            "pos_250", "volume_ratio", "fc_days_since", "ex_days_since",
            "fc_pchange", "dv_year_sum", "rsi6", "macd_hist"] + feat_cols
    cols = list(dict.fromkeys(cols))
    print("[build] 读面板（in-range）...", flush=True)
    panel = pd.read_parquet(PANEL, columns=cols)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    panel = panel[(panel["trade_date"] >= START) & (panel["trade_date"] <= END)].copy()
    print(f"    in-range rows={len(panel):,}  ({time.time()-t0:.0f}s)", flush=True)

    booster = lgb.Booster(model_file=MODEL)
    print("[build] 模型推理（one-shot，逐行独立=生产逐日 predict）...", flush=True)
    panel["prob"] = booster.predict(panel[feat_cols].astype("float32").values)
    print(f"    done ({time.time()-t0:.0f}s)", flush=True)

    # 市场基准：当日全池 fwd_ret 均值（生产 market_avgs，按日期对齐的权威口径）
    mkt_by_date = panel.groupby("trade_date")["fwd_ret"].mean()

    # rank-F5（命题4）：当日全市场横截面 industry_pct 分位（NaN → NaN → 中性 5.0）
    panel["ind_rank_pct"] = panel.groupby("trade_date")["industry_pct"].rank(pct=True)

    # 逐日 Top100（deploy 预选池 / 生产 PRE_POOL 口径）
    print("[build] 逐日 Top100 提取...", flush=True)
    panel = panel.sort_values(["trade_date", "prob"], ascending=[True, False], kind="stable")
    top100 = panel.groupby("trade_date", sort=True).head(100).copy()
    print(f"    top100 rows={len(top100):,}  days={top100['trade_date'].nunique()}", flush=True)

    print("[build] 读 MAIN_DAILY（可执行口径 + F6 est）...", flush=True)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=[
        "open", "high", "up_limit", "down_limit", "vol", "suspend_timing",
        "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    _uni = set(panel["ts_code"].unique())
    daily = daily[daily["ts_code"].isin(_uni)]
    daily = daily[daily["trade_date"] >= START - pd.Timedelta(days=45)]
    daily = daily.sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1)
    daily = daily.set_index(["trade_date", "ts_code"])
    print(f"    daily rows={len(daily):,}  ({time.time()-t0:.0f}s)", flush=True)

    # est：top100 (date, code) 对的 pe_ttm/turnover_rate（生产 est=daily.reindex(idx)）
    idx = pd.MultiIndex.from_arrays([top100["trade_date"], top100["ts_code"]])
    est = daily.reindex(idx)[["pe_ttm", "turnover_rate", "open_next", "up_limit_next",
                              "vol_next", "suspend_next"]]
    for c in ["open_next", "up_limit_next", "vol_next", "suspend_next"]:
        top100[c] = est[c].values
    pe = est["pe_ttm"].values
    tu = est["turnover_rate"].values

    # ---- 评分卡（向量化，语义=scorecard_real；est 提供 F6 的 pe/换手） ----
    f1 = SCV.score_f1_vec(top100["pos_250"].values)
    f2 = SCV.score_f2_vec(top100["main_net"].values, top100["volume_ratio"].values)
    f3 = SCV.score_f3_vec(top100["fc_days_since"].values, top100["ex_days_since"].values,
                          top100["fc_pchange"].values, top100["dv_year_sum"].values)
    f4 = SCV.score_f4_vec(top100["rsi6"].values, top100["macd_hist"].values)
    f5_abs = SCV.score_f5_vec(top100["industry_pct"].values)
    f6 = SCV.score_f6_vec(pe, tu)

    # rank-F5 主口径：连续分 10×全市场分位（NaN → 5.0 中性）
    r = top100["ind_rank_pct"].values
    f5_rku = np.where(np.isnan(r), 5.0, 10.0 * r)
    # 敏感性1：全市场分位映射回原档位阶梯 {2,4,5,8,10}
    f5_rkl = np.where(np.isnan(r), 5.0,
                      np.select([r >= 0.8, r >= 0.6, r >= 0.4, r >= 0.2],
                                [10.0, 8.0, 5.0, 4.0], default=2.0))
    # 敏感性2：Top100 池内分位（连续）
    r_pool = top100.groupby("trade_date")["industry_pct"].rank(pct=True).values
    f5_rkp = np.where(np.isnan(r_pool), 5.0, 10.0 * r_pool)

    top100["total_abs"] = SCV.total_new_vec(f1, f2, f3, f4, f5_abs, f6)
    top100["total_rku"] = SCV.total_new_vec(f1, f2, f3, f4, f5_rku, f6)
    top100["total_rkl"] = SCV.total_new_vec(f1, f2, f3, f4, f5_rkl, f6)
    top100["total_rkp"] = SCV.total_new_vec(f1, f2, f3, f4, f5_rkp, f6)

    keep = ["trade_date", "ts_code", "prob", "fwd_ret", "industry_pct", "ind_rank_pct",
            "open_next", "up_limit_next", "vol_next", "suspend_next",
            "total_abs", "total_rku", "total_rkl", "total_rkp"]
    top100[keep].to_parquet(CACHE, index=False)
    mkt_by_date.to_frame("mkt").to_parquet(CACHE_MKT)
    print(f"[build] cache -> {CACHE}  ({time.time()-t0:.0f}s)", flush=True)


# ---------------- simulate（语义=scan_rotate_cost_real.simulate exec_ok=True, EXIT_MODE=fixed） ----------------
def simulate(dates, per_day, threshold, n=N_HOLD, slip=SLIP, open_map=None,
             down_map=None, top=TOP):
    M = len(dates)
    cash = 100000.0
    hold = {}
    trades, daily_ret = [], []
    prev_total = 100000.0
    n_skip = 0
    n_skip_down = 0
    n_delist = 0
    for i, d in enumerate(dates):
        row = per_day[d]
        if i > 0:
            for c in list(hold):
                h = hold[c]
                o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < M else None
                o_cur = open_map.get((d, c))
                if o_buy and o_cur and o_buy > 0:
                    h["value"] = h["invest"] * o_cur / o_buy
        for c in list(hold):
            h = hold[c]
            o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < M else None
            o_cur = open_map.get((d, c))
            if not (o_buy and o_cur and o_buy > 0):
                h["missing_days"] = h.get("missing_days", 0) + 1
                if h["missing_days"] >= DELIST_DAYS:
                    amt = h["value"]
                    cash += amt * (1 - DELIST_LOSS) - sell_fee(amt, c, slip)
                    trades.append((h["buy_i"], i, amt * (1 - DELIST_LOSS) / h["buy_val"] - 1))
                    del hold[c]
                    n_delist += 1
                continue
            h["missing_days"] = 0
            ret = o_cur / o_buy - 1
            down = down_map.get((d, c)) if down_map is not None else None
            one_word_down = down is not None and not (isinstance(down, float) and np.isnan(down)) \
                and o_cur <= down + 1e-9
            if one_word_down:
                n_skip_down += 1
                continue
            if (i - h["buy_i"]) >= n + 1:
                sell = True
            elif (i - h["buy_i"]) < 2:
                sell = False
            else:
                sell = (ret <= STOP or ret >= TP)
            if sell:
                amt = h["value"]
                cash += amt - sell_fee(amt, c, slip)
                trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                del hold[c]
        while len(hold) < top:
            cand = row[~row.index.isin(hold)]
            cand = cand[cand["total_new"] >= threshold]
            if len(cand) == 0:
                break
            ok = ~(cand["vol_next"].isna() | (cand["vol_next"] <= 0)
                   | cand["suspend_next"].notna()
                   | (cand["up_limit_next"].notna() & cand["open_next"].notna()
                      & (cand["open_next"] >= cand["up_limit_next"])))
            n_skip += int((~ok).sum())
            cand = cand[ok]
            if len(cand) == 0:
                break
            best = cand.nlargest(1, "total_new").index[0]
            k = top - len(hold)
            budget = cash * 0.95 / k
            fee = buy_fee(budget, best, slip)
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
    return trades, np.array(daily_ret), n_skip, n_skip_down, n_delist


def mdd(daily_ret):
    nav = np.cumprod(1 + np.asarray(daily_ret))
    return float((nav / np.maximum.accumulate(nav) - 1).min())


def load():
    df = pd.read_parquet(CACHE)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    mkt = pd.read_parquet(CACHE_MKT).reset_index()
    mkt["trade_date"] = pd.to_datetime(mkt["trade_date"])
    return df, mkt


def make_per_day(df, total_col):
    """per_day[d] = 当日 Top10 by prob（生产 build_per_day 的 PRE100→TOP10 截断口径）。"""
    df = df.sort_values(["trade_date", "prob"], ascending=[True, False], kind="stable")
    top10 = df.groupby("trade_date", sort=True).head(10).copy()
    per_day = {}
    for d, g in top10.groupby("trade_date", sort=True):
        gg = g.set_index("ts_code")
        gg = gg.rename(columns={total_col: "total_new"})
        per_day[pd.Timestamp(d)] = gg[["total_new", "fwd_ret", "prob",
                                       "open_next", "up_limit_next", "vol_next", "suspend_next"]]
    return per_day


def main():
    if not os.path.exists(CACHE):
        build()
    df, mkt = load()
    dates = sorted(df["trade_date"].unique())
    dates = [pd.Timestamp(d) for d in dates]
    print(f"[load] {len(dates)} 日 {dates[0].date()} ~ {dates[-1].date()} | top100 rows={len(df):,}")
    mkt_by_date = dict(zip(mkt["trade_date"], mkt["mkt"]))
    assert len(mkt_by_date) == len(dates), "mkt 与日期未一一对应"

    print("[load] 读 MAIN_DAILY open/down_limit 构建 open_map/down_map ...", flush=True)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "down_limit"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    _uni = set(df["ts_code"].unique())
    daily = daily[daily["ts_code"].isin(_uni)]
    daily = daily[daily["trade_date"] >= START - pd.Timedelta(days=45)]
    daily = daily.set_index(["trade_date", "ts_code"])
    open_map = daily["open"].to_dict()
    down_map = daily["down_limit"].to_dict()
    del daily
    print(f"    open_map={len(open_map):,}", flush=True)

    # ================= V2：生产 BT.build_per_day 窗口对比 =================
    print("\n===== 保真校验 V2：生产 BT.build_per_day（2026-08-10 ~ 08-14）vs 我的 build =====",
          flush=True)
    os.environ["BT_PANEL"] = PANEL
    os.environ["BT_MODEL"] = MODEL
    os.environ["BT_META"] = META
    os.environ["BT_THRESHOLD"] = "60.0"
    os.environ["BT_TOP"] = "2"
    os.environ["BT_START"] = "2026-08-10"
    os.environ["BT_END"] = "2026-08-14"
    os.environ["BT_EXIT"] = "fixed"
    os.environ["BT_POSFILTER"] = ""
    os.environ["BT_PKOUT"] = "0"
    import scan_rotate_cost_real as BT
    v_dates, v_per_day, v_mkt, v_open = BT.build_per_day()
    my_top10 = df.sort_values(["trade_date", "prob"], ascending=[True, False],
                              kind="stable").groupby("trade_date", sort=True).head(10)
    n_checked = n_diff = 0
    for d in v_dates:
        prod_df = v_per_day[d]
        mine = my_top10[my_top10["trade_date"] == pd.Timestamp(d)].set_index("ts_code")
        assert set(prod_df.index) == set(mine.index), f"{d} top10 集合不一致"
        for c in prod_df.index:
            n_checked += 1
            if abs(float(prod_df.loc[c, "total_new"]) - float(mine.loc[c, "total_abs"])) > 1e-9:
                n_diff += 1
        v_m = float(v_mkt.iloc[list(v_dates).index(d)])
        my_m = float(mkt_by_date[pd.Timestamp(d)])
        assert (abs(v_m - my_m) < 1e-12) or (np.isnan(v_m) and np.isnan(my_m)), \
            f"{d} mkt 不一致"
    print(f"    top10 total 对比 {n_checked} 行，diff>1e-9 的 {n_diff} 行"
          f"（{'PASS 完全一致' if n_diff == 0 else '!! 不一致'}）")
    print(f"    mkt 序列一致 PASS | open_map 前5000键抽检一致: "
          f"{all(abs((v_open.get(k) or np.nan) - (open_map.get(k) or np.nan)) < 1e-9 for k in list(v_open)[:5000])}")

    # ================= V3：生产 BT.simulate vs 我的 simulate（全期 thr60） =================
    print("\n===== 保真校验 V3：生产 BT.simulate vs 我的 simulate（全期 thr60） =====", flush=True)
    BT.START, BT.END = "2024-07-01", "2026-08-14"
    BT.THRESHOLD = 60.0
    BT.EXIT_MODE = "fixed"
    BT._DOWN_LIMIT_MAP = down_map
    BT._HIGH_MAP = {}
    BT.PK_OUT = False
    BT.BT_POSFILTER = ""
    BT.TOP = TOP
    BT.STOP, BT.TP = STOP, TP
    BT.DELIST_DAYS, BT.DELIST_LOSS = DELIST_DAYS, DELIST_LOSS
    per_day_abs = make_per_day(df, "total_abs")
    bt_trades, bt_dr, _ = BT.simulate(dates, per_day_abs, N_HOLD, SLIP, open_map, exec_ok=True)
    my_trades, my_dr, my_skip, my_skipdn, my_delist = simulate(
        dates, per_day_abs, 60.0, N_HOLD, SLIP, open_map, down_map)
    max_dr_diff = float(np.max(np.abs(np.asarray(bt_dr) - my_dr)))
    same_trades = len(bt_trades) == len(my_trades) and all(
        a[0] == b[0] and a[1] == b[1] and abs(a[2] - b[2]) < 1e-9
        for a, b in zip(bt_trades, my_trades))
    print(f"    生产 trades={len(bt_trades)} 我的 trades={len(my_trades)} | trades 逐笔一致: {same_trades}")
    print(f"    daily_ret 最大逐日差异 = {max_dr_diff:.3e}（{'PASS' if max_dr_diff < 1e-12 else '!!'}）")
    mkt_arr = np.array([mkt_by_date[d] for d in dates])
    ex_full = float(np.nanmean(my_dr - mkt_arr))
    print(f"    [锚点] 全期 thr60: {len(my_trades)} 笔（源码锚 102）| 日均超额 {ex_full:+.4%}"
          f"（源码锚 +0.149%/+0.150%）| 回撤 {mdd(my_dr):.1%}")
    print(f"    [锚点] 买入被一字涨停/停牌跳过 {my_skip} 次 | 卖出一字跌停跳过 {my_skipdn} 次"
          f" | 退市清仓 {my_delist} 次")

    # ================= 命题 3 =================
    print("\n" + "=" * 70)
    print(f"命题3：红线各档日均通过数（{dates[0].date()} ~ {dates[-1].date()}，{len(dates)} 个交易日）")
    print("=" * 70)
    df_sorted = df.sort_values(["trade_date", "prob"], ascending=[True, False], kind="stable")
    top10_all = df_sorted.groupby("trade_date", sort=True).head(10)
    print("\n主口径 [Top100 预选池 by prob]（deploy_predict_g2 --pool 100 生产口径）")
    print("红线 | 日均通过 | 中位数 | 有候选天数 | 有≥10只天数")
    for thr in (60, 58, 55, 52, 50):
        cnt = df_sorted[df_sorted["total_abs"] >= thr].groupby("trade_date").size()
        cnt = cnt.reindex(dates, fill_value=0)
        print(f"  {thr} | {cnt.mean():.2f} | {cnt.median():.1f} | {(cnt>0).sum()} | {(cnt>=10).sum()}")
    print("\n敏感性 [Top10 池]（scan_rotate_cost_real.build_per_day 实际截断口径，"
          "红线在截断后过滤）")
    print("红线 | 日均通过 | 中位数 | 有候选天数 | 有2只天数")
    for thr in (60, 58, 55, 52, 50):
        cnt = top10_all[top10_all["total_abs"] >= thr].groupby("trade_date").size()
        cnt = cnt.reindex(dates, fill_value=0)
        print(f"  {thr} | {cnt.mean():.2f} | {cnt.median():.1f} | {(cnt>0).sum()} | {(cnt>=2).sum()}")

    # ================= 命题 4 =================
    print("\n" + "=" * 70)
    print("命题4：绝对阈值 F5 vs rank-F5 —— IS/OOS 对比")
    print("=" * 70)
    is_dates = [d for d in dates if d < IS_CUT]
    oos_dates = [d for d in dates if d >= IS_CUT]
    print(f"IS/OOS 切分: IS_CUT={IS_CUT.date()}（audit_stats_walkforward.py:42 同款）"
          f" | IS {len(is_dates)} 日 | OOS {len(oos_dates)} 日")
    print("市场基准对齐：按日期 join（正确口径）；位置对齐缺陷见命题5。\n")

    variants = {"abs": "total_abs", "rank_univ": "total_rku",
                "rank_univ_ladder": "total_rkl", "rank_pool": "total_rkp"}
    thr_grid = (50, 52, 55, 58, 60)
    results = {}
    for name, col in variants.items():
        pd_day = make_per_day(df, col)
        for seg_name, seg in (("IS", is_dates), ("OOS", oos_dates)):
            tr, dr, skip, skipdn, delist = simulate(seg, pd_day, 60.0, N_HOLD, SLIP,
                                                    open_map, down_map)
            mkt_seg = np.array([mkt_by_date[d] for d in seg])
            results[(name, seg_name, 60)] = dict(
                n=len(tr), ex=float(np.nanmean(dr - mkt_seg)), mdd=mdd(dr))
        best_thr, best_ex = None, -9e9
        for thr in thr_grid:
            tr, dr, skip, skipdn, delist = simulate(is_dates, pd_day, float(thr),
                                                    N_HOLD, SLIP, open_map, down_map)
            mkt_seg = np.array([mkt_by_date[d] for d in is_dates])
            ex = float(np.nanmean(dr - mkt_seg))
            results[(name, "IS", thr)] = dict(n=len(tr), ex=ex, mdd=mdd(dr))
            if ex > best_ex:
                best_thr, best_ex = thr, ex
        tr, dr, skip, skipdn, delist = simulate(oos_dates, pd_day, float(best_thr),
                                                N_HOLD, SLIP, open_map, down_map)
        mkt_seg = np.array([mkt_by_date[d] for d in oos_dates])
        results[(name, "OOS", best_thr)] = dict(n=len(tr), ex=float(np.nanmean(dr - mkt_seg)),
                                                mdd=mdd(dr))
        print(f"[{name}] IS 最优 thr={best_thr}（IS ex={best_ex:+.4%}）")

    print("\n[固定红线 60] 变体 | 段 | 笔数 | 日均超额 | 最大回撤")
    for name in variants:
        for seg in ("IS", "OOS"):
            r = results[(name, seg, 60)]
            print(f"  {name} | {seg} | {r['n']} | {r['ex']:+.4%} | {r['mdd']:.1%}")
    print("\n[walk-forward：IS 定参 -> OOS] 变体 | IS最优thr | OOS笔数 | OOS日均超额 | OOS最大回撤")
    for name in variants:
        is_exs = {k[2]: v["ex"] for k, v in results.items() if k[0] == name and k[1] == "IS"}
        best_thr = max(is_exs, key=is_exs.get)
        rr = results[(name, "OOS", best_thr)]
        print(f"  {name} | {best_thr} | {rr['n']} | {rr['ex']:+.4%} | {rr['mdd']:.1%}")

    # ================= 命题 5 =================
    print("\n" + "=" * 70)
    print("命题5：audit_stats_walkforward.align_market 按位置对齐缺陷量化")
    print("=" * 70)
    mkt_full = pd.Series([mkt_by_date[d] for d in dates])  # 与生产 market_avgs 同序
    tr_oos, dr_oos, *_ = simulate(oos_dates, per_day_abs, 60.0, N_HOLD, SLIP,
                                  open_map, down_map)
    # 生产缺陷口径（audit_stats_walkforward.py:46-54）：OOS 第 i 天配 mkt_full.iloc[i]（= IS 段市场）
    mkt_pos = np.array([float(mkt_full.iloc[i]) for i in range(len(oos_dates))])
    ex_bug = float(np.nanmean(dr_oos - mkt_pos))
    # 正确口径（按日期 join）
    mkt_join = np.array([mkt_by_date[d] for d in oos_dates])
    ex_ok = float(np.nanmean(dr_oos - mkt_join))
    print(f"\nOOS 段（{oos_dates[0].date()} ~ {oos_dates[-1].date()}，{len(oos_dates)} 日，"
          f"thr=60，abs-F5，{len(tr_oos)} 笔）：")
    print(f"  按位置对齐（生产 align_market 缺陷）日均超额 = {ex_bug:+.4%}")
    print(f"  按日期 join（正确）            日均超额 = {ex_ok:+.4%}")
    print(f"  偏差 = {ex_bug - ex_ok:+.4%}")
    print(f"  对照声称值：缺陷口径 +0.078% / 修正口径 +0.181%")
    tr_is, dr_is, *_ = simulate(is_dates, per_day_abs, 60.0, N_HOLD, SLIP,
                                open_map, down_map)
    mkt_pos_is = np.array([float(mkt_full.iloc[i]) for i in range(len(is_dates))])
    mkt_join_is = np.array([mkt_by_date[d] for d in is_dates])
    print(f"\nIS 段（{is_dates[0].date()} ~ {is_dates[-1].date()}，{len(is_dates)} 日）：")
    print(f"  位置对齐 vs 日期 join 完全一致: "
          f"{np.array_equal(mkt_pos_is, mkt_join_is)}（IS 是 dates 前缀，不受缺陷影响）")
    print(f"  IS 日均超额（thr=60）= {float(np.nanmean(dr_is - mkt_join_is)):+.4%}")
    print("\n注：全期口径 dates=全集，位置 i 恰对应当日 -> 不受影响；仅非前缀子集（OOS）受影响，")
    print("    生产缺陷把 OOS 的市场基准错配成 IS 段（2024-07~2025-06）的市场日收益。")


if __name__ == "__main__":
    main()
