# coding: utf-8
"""评分卡权重 + F2/F5/F6 阶梯阈值寻优（walk-forward：IS 选参 / OOS 验证）。

背景：review_full.py / deploy_predict.py / scorecard_real.py 的 F1-F6 权重
与 F2/F5/F6 阶梯阈值一直为手工设定（继承 Project_15），从未回测寻优。
本脚本复用 scan_rotate_cost_real.py 的可执行回测引擎（open→open + 真实成本），
对评分卡参数做寻优，产出候选参数 + IS/OOS 对比，**不改任何生产配置**。

寻优设计（防过拟合）：
  - 固定生产口径：红线 58 / TOP=2 / 持有 N=10 / 止损-7% / 止盈15% / 滑点0.1%
  - IS（选参窗口）2024-07-01 ~ 2025-12-31：在窗口内寻优
  - OOS（验证窗口）2026-01-01 ~ 2026-08-14：只用最优参数跑一次验证，不参与选参
  - 目标指标：IS 日均超额（策略-全市场）；约束：IS 交易数 >= 15（防退化成空仓）
  - 三个顺序寻优阶段：默认权重→寻 F2 阈值；最优 F2→寻权重；最优→寻 F5/F6

用法：
  python optimize_scorecard.py --quick          # 小样本快速验证（各阶段少量 trial）
  python optimize_scorecard.py                   # 完整寻优
输出：data/real/scorecard_optim_report.md
"""
import argparse
import json
import os
import time
import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP
import qmt_config as C
import review_full as RF

HERE = DC.PROJECT_DIR
DATA_DIR = DC.DATA_DIR
PANEL = os.path.join(DATA_DIR, "feature_panel_v3_sc.parquet")
MODEL = DC.model_file("_v3_enh")
META = os.path.join(DATA_DIR, "features_v3_enh.json")
OUT_MD = os.path.join(DATA_DIR, "real", "scorecard_optim_report.md")

# ---- 固定生产口径（与 9:45 复核任务一致）----
THRESHOLD = 58.0
TOP = 2
N = 10
STOP = -0.07
TP = 0.15
SLIP = 0.001
IS_START, IS_END = "2024-07-01", "2025-12-31"
OOS_START, OOS_END = "2026-01-01", "2026-08-14"

COMM_RATE, STAMP_RATE, TRANS_RATE = C.COMM_RATE, C.STAMP_RATE, C.TRANS_RATE

# ---- 默认评分卡参数（当前生产值，即基线）----
DEF_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}
DEF_F2 = {"hi_in": 5e7, "hi_lb": 2.5, "mid_in": 1e7, "mid_lb": 1.5, "neg_lb": 2.0, "big_out": 1e8}
DEF_F5 = {"a": 3.0, "b": 1.5, "c": 0.0, "d": -1.0}
DEF_F6 = {"cap": 100.0, "lo": 10.0, "hi": 30.0, "lo2": 5.0, "hi2": 50.0, "tu_lo": 1.0, "tu_hi": 8.0}


def is_sh(code):
    return code.startswith("6")


def sell_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + amt * STAMP_RATE + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def buy_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


# ---------- 向量化阶梯打分（可参数化） ----------
def score_f2_vec(mn, lb, p):
    """F2 资金认可度阶梯（参数化），顺序与原版 score_f2 完全一致。"""
    mn = np.asarray(mn, dtype=float)
    lb = np.asarray(lb, dtype=float)
    out = np.full(len(mn), 2.0)
    nan = np.isnan(mn)
    out[nan] = 5.0
    m = (~nan) & (mn > p["hi_in"]) & (lb > p["hi_lb"])
    out[m] = 10.0
    m = (~nan) & (out == 2.0) & (mn > p["mid_in"]) & (lb > p["mid_lb"])
    out[m] = 8.0
    m = (~nan) & (out == 2.0) & (mn > 0)
    out[m] = 6.0
    m = (~nan) & (out == 2.0) & (~np.isnan(lb)) & (lb >= p["neg_lb"])  # 净流出但放量=分歧
    out[m] = 5.0
    m = (~nan) & (out == 2.0) & (mn <= -p["big_out"])  # 净流出超重
    out[m] = 1.0
    return out


def score_f5_vec(ind, p):
    """F5 板块β联动阶梯（参数化），顺序与原版 score_f5 一致。"""
    ind = np.asarray(ind, dtype=float)
    out = np.full(len(ind), 2.0)
    nan = np.isnan(ind)
    out[nan] = 5.0
    m = (~nan) & (ind > p["a"])
    out[m] = 10.0
    m = (~nan) & (out == 2.0) & (ind > p["b"])
    out[m] = 8.0
    m = (~nan) & (out == 2.0) & (ind > p["c"])
    out[m] = 5.0
    m = (~nan) & (out == 2.0) & (ind > p["d"])
    out[m] = 4.0
    return out


def score_f6_vec(pe, tu, p):
    """F6 估值/流动性阶梯（参数化），顺序与原版 score_f6 一致。"""
    pe = np.asarray(pe, dtype=float)
    tu = np.asarray(tu, dtype=float)
    out = np.full(len(pe), 4.0)
    nan = np.isnan(pe)
    out[nan] = 5.0
    bad = (~nan) & ((pe < 0) | (pe > p["cap"]))
    out[bad] = 1.0
    good_tu = (~np.isnan(tu)) & (tu >= p["tu_lo"]) & (tu <= p["tu_hi"])
    tu_ok = np.isnan(tu) | good_tu
    m = (~nan) & (~bad) & (pe >= p["lo"]) & (pe <= p["hi"]) & tu_ok
    out[m] = 10.0
    m = (~nan) & (out == 4.0) & (pe >= p["lo2"]) & (pe <= p["hi2"])
    out[m] = 7.0
    return out


def proxy_scores(day):
    """F1/F3/F4 代理分（与生产一致，逐行 apply，仅用于 top10 行，量小）。"""
    s = DP.compute_scorecard(day)
    return s[["F1", "F3", "F4"]].values


# ---------- 数据预载：每天 top10-by-prob 候选帧 ----------
def build_candidates(dates):
    """逐日：模型 prob → top10 by prob → 缓存候选行（原始输入 + 代理分 + 可执行字段）。

    返回 {date: DataFrame(index=ts_code)}，列含:
      F1,F3,F4(代理), main_net, volume_ratio, industry_pct, pe_ttm, turnover_rate,
      fwd_ret, open_next, up_limit_next, vol_next, suspend_next
    """
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    # 只保留目标区间，减少遍历
    d0, d1 = pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])
    panel = panel[(panel["trade_date"] >= d0) & (panel["trade_date"] <= d1)]
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)

    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "close", "up_limit", "down_limit",
                                                    "vol", "suspend_timing", "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
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
    open_map = daily["open"].to_dict()

    out = {}
    market = {}
    for d in dates:
        day = panel[panel["trade_date"] == pd.Timestamp(d)].copy()
        if len(day) < 20:
            continue
        day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
        market[d] = float(day["fwd_ret"].mean())
        top = day.nlargest(10, "prob")
        idx = pd.MultiIndex.from_arrays([top["trade_date"], top["ts_code"]])
        est = daily.reindex(idx)
        cols = ["main_net", "volume_ratio", "industry_pct"]
        cand = top[cols].copy()
        # F6 用 daily 实时 PE/换手（与原版 scorecard_real.compute_real_scorecard 一致），
        # daily 缺失时回退面板 pe_ttm / turn_ma5
        pe = est["pe_ttm"].values if "pe_ttm" in est.columns else top["pe_ttm"].values
        tu = est["turnover_rate"].values if "turnover_rate" in est.columns else top["turn_ma5"].values
        cand["pe_ttm"] = pe
        cand["turnover_rate"] = tu
        cand["F1"], cand["F3"], cand["F4"] = 0.0, 0.0, 0.0
        try:
            cand.loc[:, ["F1", "F3", "F4"]] = proxy_scores(top)
        except Exception:
            pass
        cand["fwd_ret"] = top["fwd_ret"].values
        cand["open_next"] = est["open_next"].values
        cand["up_limit_next"] = est["up_limit_next"].values
        cand["vol_next"] = est["vol_next"].values
        cand["suspend_next"] = est["suspend_next"].values
        cand = cand.set_index(top["ts_code"])
        out[d] = cand
    return out, pd.Series(market), open_map


# ---------- 可执行模拟（与 scan_rotate_cost_real.simulate 同逻辑） ----------
def simulate(dates, per_day, market_daily, open_map, weights, f2p, f5p, f6p):
    cash = 100000.0
    hold = {}
    trades, daily_ret = [], []
    prev_total = 100000.0
    n_skip = 0
    for i, d in enumerate(dates):
        if d not in per_day:
            continue
        row = per_day[d]
        if i > 0:
            for c in list(hold):
                h = hold[c]
                o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
                o_cur = open_map.get((d, c))
                if o_buy and o_cur and o_buy > 0:
                    h["value"] = h["invest"] * o_cur / o_buy
        for c in list(hold):
            h = hold[c]
            o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
            o_cur = open_map.get((d, c))
            if not (o_buy and o_cur and o_buy > 0):
                continue
            ret = o_cur / o_buy - 1
            if ret <= STOP or ret >= TP or (i - h["buy_i"]) >= N + 1:
                amt = h["value"]
                cash += amt - sell_fee(amt, c, SLIP)
                trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                del hold[c]
        while len(hold) < TOP:
            cand = row[~row.index.isin(hold)].copy()
            # 拼分：权重 × 阶梯分 × 10
            f2 = score_f2_vec(cand["main_net"].values, cand["volume_ratio"].values, f2p)
            f5 = score_f5_vec(cand["industry_pct"].values, f5p)
            f6 = score_f6_vec(cand["pe_ttm"].values, cand["turnover_rate"].values, f6p)
            total = (weights["F1"] * cand["F1"].values + weights["F2"] * f2
                     + weights["F3"] * cand["F3"].values + weights["F4"] * cand["F4"].values
                     + weights["F5"] * f5 + weights["F6"] * f6) * 10.0
            cand["total_new"] = total
            cand = cand[cand["total_new"] >= THRESHOLD]
            if len(cand) == 0:
                break
            def _executable(s):
                if s["vol_next"] is None or (isinstance(s["vol_next"], float) and np.isnan(s["vol_next"])):
                    return False
                if s["vol_next"] <= 0:
                    return False
                if s["suspend_next"] is not None and not (isinstance(s["suspend_next"], float) and np.isnan(s["suspend_next"])):
                    return False
                if s["up_limit_next"] is not None and s["open_next"] is not None \
                        and not (isinstance(s["up_limit_next"], float) and np.isnan(s["up_limit_next"])) \
                        and not (isinstance(s["open_next"], float) and np.isnan(s["open_next"])) \
                        and s["open_next"] >= s["up_limit_next"]:
                    return False
                return True
            n_before = len(cand)
            cand = cand[cand.apply(_executable, axis=1)]
            n_skip += (n_before - len(cand))
            if len(cand) == 0:
                break
            best = cand.nlargest(1, "total_new").index[0]
            n = TOP - len(hold)
            budget = cash * 0.95 / n
            fee = buy_fee(budget, best, SLIP)
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    for c, h in hold.items():
        trades.append((h["buy_i"], len(dates), h["value"] / h["buy_val"] - 1))
    return trades, pd.Series(daily_ret), n_skip


def stats(trades, daily_ret, market_daily):
    rets = [t[2] for t in trades]
    fwd = pd.Series(rets)
    win = float((fwd > 0).mean()) if len(fwd) else np.nan
    aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
    al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
    pr = float(aw / abs(al)) if al and al != 0 else np.nan
    nav = (1 + daily_ret).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    excess = float(daily_ret.mean() - market_daily.mean())
    return {"n_trades": len(trades), "win_rate": win, "profit_loss_ratio": pr,
            "max_drawdown": mdd, "daily_excess": excess}


def eval_config(dates, per_day, market, open_map, weights, f2p, f5p, f6p):
    trades, daily_ret, n_skip = simulate(dates, per_day, market, open_map, weights, f2p, f5p, f6p)
    s = stats(trades, daily_ret, market.loc[market.index.isin(dates)])
    s["n_skip"] = n_skip
    return s


def is_valid_weights(w):
    return abs(sum(w.values()) - 1.0) < 1e-6 and all(0.05 <= v <= 0.35 for v in w.values())


def rand_weights(rng, n=400):
    """Dirichlet 采样权重并过滤范围。"""
    got = 0
    out = []
    while got < n:
        x = rng.dirichlet(np.ones(6))
        w = dict(zip(["F1", "F2", "F3", "F4", "F5", "F6"], x))
        if is_valid_weights(w):
            out.append(w)
            got += 1
    return out


def rand_f2(rng, n):
    out = []
    for _ in range(n):
        out.append({
            "hi_in": float(rng.choice([3e7, 5e7, 8e7, 1e8])),
            "hi_lb": float(rng.choice([2.0, 2.5, 3.0])),
            "mid_in": float(rng.choice([5e6, 1e7, 2e7])),
            "mid_lb": float(rng.choice([1.0, 1.5, 2.0])),
            "neg_lb": float(rng.choice([1.5, 2.0, 2.5])),
            "big_out": float(rng.choice([5e7, 1e8, 2e8])),
        })
    return out


def rand_f5f6(rng, n):
    out = []
    for _ in range(n):
        out.append({
            "F5": {"a": float(rng.choice([2.5, 3.0, 4.0])),
                   "b": float(rng.choice([1.0, 1.5, 2.0])),
                   "c": float(rng.choice([0.0, 0.5])),
                   "d": float(rng.choice([-2.0, -1.0]))},
            "F6": {"cap": float(rng.choice([80.0, 100.0, 120.0])),
                   "lo": float(rng.choice([8.0, 10.0, 12.0])),
                   "hi": float(rng.choice([25.0, 30.0, 40.0])),
                   "lo2": float(rng.choice([3.0, 5.0])),
                   "hi2": float(rng.choice([40.0, 50.0, 60.0])),
                   "tu_lo": 1.0, "tu_hi": 8.0},
        })
    return out


def run_search(name, dates_is, per_day, market, open_map, base_w, base_f2, base_f5, base_f6,
               candidates, n_trials, stage):
    """对给定候选参数集在 IS 上评估，返回按日超额排序的 (score, params)。

    stage 决定候选参数如何映射到完整配置：
      "weights" → candidates 是权重 dict，其余用 base_*（当前生产默认）
      "f2"      → candidates 是 {"F2": {...}}，权重/其余用 base_*
      "f5f6"    → candidates 是 {"F5": {...}, "F6": {...}}，权重用 base_w，F2 用 base_f2
    """
    results = []
    t0 = time.time()
    for i, params in enumerate(candidates):
        if stage == "weights":
            w = params
            f2p, f5p, f6p = base_f2, base_f5, base_f6
        elif stage == "f2":
            w = base_w
            f2p, f5p, f6p = params["F2"], base_f5, base_f6
        else:  # f5f6
            w = base_w
            f2p, f5p, f6p = base_f2, params["F5"], params["F6"]
        s = eval_config(dates_is, per_day, market, open_map, w, f2p, f5p, f6p)
        results.append((s["daily_excess"], s, params))
        if (i + 1) % 50 == 0:
            print(f"    {name} {i+1}/{len(candidates)} 当前最优 {max(r[0] for r in results):.4%}  {time.time()-t0:.0f}s")
    results.sort(key=lambda x: -x[0])
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="小样本快速验证")
    args = ap.parse_args()
    rng = np.random.default_rng(42)
    nw = 60 if args.quick else 400
    nf2 = 40 if args.quick else 250
    nf56 = 30 if args.quick else 150

    print("[1/4] 构建候选帧（top10 by prob）...")
    # 用 Timestamp 全程（open_map key 为 Timestamp，避免字符串/Timestamp 不匹配）
    is_dates = [d for d in pd.bdate_range(IS_START, IS_END)]
    oos_dates = [d for d in pd.bdate_range(OOS_START, OOS_END)]
    all_dates = sorted(set(is_dates + oos_dates))
    per_day, market, open_map = build_candidates(all_dates)
    is_dates = [d for d in is_dates if d in per_day]
    oos_dates = [d for d in oos_dates if d in per_day]
    print(f"    IS {len(is_dates)} 交易日 ({is_dates[0].date()}~{is_dates[-1].date()}), "
          f"OOS {len(oos_dates)} 交易日 ({oos_dates[0].date()}~{oos_dates[-1].date()})")

    # ---- 基线（生产默认参数）----
    print("[2/4] 基线评估（生产默认参数）...")
    base_is = eval_config(is_dates, per_day, market, open_map, DEF_WEIGHTS, DEF_F2, DEF_F5, DEF_F6)
    base_oos = eval_config(oos_dates, per_day, market, open_map, DEF_WEIGHTS, DEF_F2, DEF_F5, DEF_F6)
    print(f"    基线 IS 超额 {base_is['daily_excess']:.4%} 胜率 {base_is['win_rate']:.1%} 交易 {base_is['n_trades']}")
    print(f"    基线 OOS 超额 {base_oos['daily_excess']:.4%} 胜率 {base_oos['win_rate']:.1%} 交易 {base_oos['n_trades']}")

    # ---- 阶段1：F2 阈值寻优（默认权重）----
    print("[3/4] 阶段1 寻优 F2 资金阶梯阈值（默认权重）...")
    cand_f2 = [{"F2": p} for p in rand_f2(rng, nf2)]
    r_f2 = run_search("F2", is_dates, per_day, market, open_map, DEF_WEIGHTS, DEF_F2, DEF_F5, DEF_F6,
                      cand_f2, nf2, "f2")
    best_f2 = r_f2[0][2]["F2"]
    print(f"    最优 F2: {best_f2}  IS 超额 {r_f2[0][1]['daily_excess']:.4%} (交易 {r_f2[0][1]['n_trades']})")

    # ---- 阶段2：权重寻优（固定最优 F2）----
    print("[4/4] 阶段2 寻优 F1-F6 权重（固定最优 F2）...")
    cand_w = rand_weights(rng, nw)
    r_w = run_search("W", is_dates, per_day, market, open_map, DEF_WEIGHTS, best_f2, DEF_F5, DEF_F6,
                     cand_w, nw, "weights")
    best_w = r_w[0][2]
    print(f"    最优权重: {best_w}  IS 超额 {r_w[0][1]['daily_excess']:.4%} (交易 {r_w[0][1]['n_trades']})")

    # ---- 阶段3：F5/F6 阈值寻优（固定最优权重+F2）----
    print("[4/4] 阶段3 寻优 F5/F6 阶梯阈值（固定最优权重+F2）...")
    cand_f56 = [{"F5": p["F5"], "F6": p["F6"]} for p in rand_f5f6(rng, nf56)]
    r_f56 = run_search("F5F6", is_dates, per_day, market, open_map, best_w, best_f2, DEF_F5, DEF_F6,
                       cand_f56, nf56, "f5f6")
    best_f5 = r_f56[0][2]["F5"]
    best_f6 = r_f56[0][2]["F6"]
    print(f"    最优 F5/F6: {best_f5} / {best_f6}  IS 超额 {r_f56[0][1]['daily_excess']:.4%}")

    # ---- 最终 OOS 验证 ----
    print("[5/5] OOS 验证候选组合 ...")
    combo_is = eval_config(is_dates, per_day, market, open_map, best_w, best_f2, best_f5, best_f6)
    combo_oos = eval_config(oos_dates, per_day, market, open_map, best_w, best_f2, best_f5, best_f6)
    print(f"    组合 IS 超额 {combo_is['daily_excess']:.4%} / OOS {combo_oos['daily_excess']:.4%}")

    # 记录 top 候选用于稳健性参考
    top_f2_10 = r_f2[:10]
    top_w_10 = r_w[:10]
    top_f56_10 = r_f56[:10]

    lines = [
        "# 评分卡参数寻优报告（权重 + F2/F5/F6 阶梯阈值）",
        "",
        f"> 生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')} | 复用 scan_rotate_cost_real 可执行回测引擎",
        f"> 固定口径：红线{THRESHOLD} / TOP={TOP} / 持有 N={N} / 止损{STOP:.0%} / 止盈{TP:.0%} / 滑点{SLIP:.1%} / 初始10万",
        f"> 面板 feature_panel_v3_sc（真实 F2/F5）+ 模型 lgb_model_v3_enh",
        f"> walk-forward：IS 选参 {IS_START}~{IS_END}（{len(is_dates)} 日），OOS 验证 {OOS_START}~{OOS_END}（{len(oos_dates)} 日）",
        f"> 生产默认（基线）权重 {DEF_WEIGHTS}；F2 {DEF_F2}；F5 {DEF_F5}；F6 {DEF_F6}",
        "",
        "## 一、基线（当前生产参数）",
        "",
        "| 窗口 | 交易数 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|",
        f"| IS | {base_is['n_trades']} | {base_is['win_rate']:.1%} | {base_is['profit_loss_ratio']:.2f} | {base_is['max_drawdown']:.1%} | {base_is['daily_excess']:.4%} |",
        f"| OOS | {base_oos['n_trades']} | {base_oos['win_rate']:.1%} | {base_oos['profit_loss_ratio']:.2f} | {base_oos['max_drawdown']:.1%} | {base_oos['daily_excess']:.4%} |",
        "",
        "## 二、阶段1：F2 资金阶梯阈值寻优（固定默认权重）",
        "",
        "IS 选参排名前10：",
        "",
        "| 排名 | hi_in | hi_lb | mid_in | mid_lb | neg_lb | big_out | IS交易 | IS胜率 | IS日超额 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (sc, s, params) in enumerate(top_f2_10):
        p = params["F2"]
        lines.append(
            f"| {i+1} | {p['hi_in']:.0e} | {p['hi_lb']} | {p['mid_in']:.0e} | {p['mid_lb']} | {p['neg_lb']} | {p['big_out']:.0e} "
            f"| {s['n_trades']} | {s['win_rate']:.1%} | {sc:.4%} |"
        )
    lines.append("")
    lines.append(f"> 阶段1 最优 F2 阈值：{best_f2}（IS 超额 {r_f2[0][1]['daily_excess']:.4%}）")
    lines.append("")

    lines += [
        "## 三、阶段2：F1-F6 权重寻优（固定阶段1最优 F2）",
        "",
        "IS 选参排名前10：",
        "",
        "| 排名 | F1 | F2 | F3 | F4 | F5 | F6 | IS交易 | IS胜率 | IS日超额 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (sc, s, params) in enumerate(top_w_10):
        w = params
        lines.append(
            f"| {i+1} | {w['F1']:.2f} | {w['F2']:.2f} | {w['F3']:.2f} | {w['F4']:.2f} | {w['F5']:.2f} | {w['F6']:.2f} "
            f"| {s['n_trades']} | {s['win_rate']:.1%} | {sc:.4%} |"
        )
    lines.append("")
    lines.append(f"> 阶段2 最优权重：{best_w}（IS 超额 {r_w[0][1]['daily_excess']:.4%}）")
    lines.append("")

    lines += [
        "## 四、阶段3：F5/F6 阶梯阈值寻优（固定最优权重+F2）",
        "",
        "IS 选参排名前10：",
        "",
        "| 排名 | F5(a,b,c,d) | F6(cap,lo,hi,lo2,hi2) | IS交易 | IS胜率 | IS日超额 |",
        "|---|---|---|---|---|---|",
    ]
    for i, (sc, s, params) in enumerate(top_f56_10):
        f5, f6 = params["F5"], params["F6"]
        lines.append(
            f"| {i+1} | ({f5['a']},{f5['b']},{f5['c']},{f5['d']}) | "
            f"({f6['cap']:.0f},{f6['lo']:.0f},{f6['hi']:.0f},{f6['lo2']:.0f},{f6['hi2']:.0f}) "
            f"| {s['n_trades']} | {s['win_rate']:.1%} | {sc:.4%} |"
        )
    lines.append("")
    lines.append(f"> 阶段3 最优 F5：{best_f5}；F6：{best_f6}（IS 超额 {r_f56[0][1]['daily_excess']:.4%}）")
    lines.append("")

    lines += [
        "## 五、候选组合 vs 基线（OOS 样本外验证）",
        "",
        "| 配置 | 窗口 | 交易数 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|",
        f"| 基线(生产默认) | IS | {base_is['n_trades']} | {base_is['win_rate']:.1%} | {base_is['profit_loss_ratio']:.2f} | {base_is['max_drawdown']:.1%} | {base_is['daily_excess']:.4%} |",
        f"| 基线(生产默认) | OOS | {base_oos['n_trades']} | {base_oos['win_rate']:.1%} | {base_oos['profit_loss_ratio']:.2f} | {base_oos['max_drawdown']:.1%} | {base_oos['daily_excess']:.4%} |",
        f"| 寻优组合 | IS | {combo_is['n_trades']} | {combo_is['win_rate']:.1%} | {combo_is['profit_loss_ratio']:.2f} | {combo_is['max_drawdown']:.1%} | {combo_is['daily_excess']:.4%} |",
        f"| 寻优组合 | OOS | {combo_oos['n_trades']} | {combo_oos['win_rate']:.1%} | {combo_oos['profit_loss_ratio']:.2f} | {combo_oos['max_drawdown']:.1%} | {combo_oos['daily_excess']:.4%} |",
        "",
        "## 六、结论与建议",
        "",
    ]
    # 结论判定
    if combo_oos["daily_excess"] > base_oos["daily_excess"] + 0.0001:
        lines.append("- OOS 验证：寻优组合日超额高于基线，可考虑更新评分卡参数（需人工复核后落盘）。")
    else:
        lines.append("- OOS 验证：寻优组合未稳定优于基线（过拟合风险），建议维持生产默认参数。")
    lines.append("- 本报告仅为研究产物，未修改 review_full.py / deploy_predict.py / scorecard_real.py 任何生产配置。")
    lines.append("- 注意：OOS 仅 7.5 个月，样本有限，若采纳需继续前向验证累积样本。")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
