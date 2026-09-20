# coding: utf-8
"""R8 差异桥接诊断：在 audit_r8 同引擎下输出 G2/V14 K2 的日超额 + 现金占用，
对比历史审计口径（日超额 +0.213% G2 / +0.157% V1.4），定位"绝对年化 vs 超额"差异构成。"""
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, r"D:\QuantLab\data\schedules")
import audit_r8 as A


def simulate_bridge(dates, per_day, daily, topk, N):
    """同 audit_r8.simulate，额外记录每日现金占比。"""
    open_map = daily["open"].to_dict()
    high_map = daily["high"].to_dict()
    down_map = daily["down_limit"].to_dict()
    cash = 100000.0
    prev_total = cash
    hold = {}
    trades, daily_ret, cash_frac = [], [], []
    reasons, n_skip_exec = {}, 0
    M = len(dates)
    for i, d in enumerate(dates):
        row = per_day[d]
        for c in list(hold):
            h = hold[c]
            o_buy = open_map.get((dates[h["buy_i"] + 1], c))
            o_cur = open_map.get((d, c))
            if o_buy is None or o_cur is None or not (o_buy > 0 and o_cur > 0):
                h["missing"] = h.get("missing", 0) + 1
                if h["missing"] >= A.DELIST_DAYS:
                    amt = h["value"] * (1 - A.DELIST_LOSS)
                    cash += amt - A.sell_fee(amt, c)
                    trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                    reasons["DELIST"] = reasons.get("DELIST", 0) + 1
                    del hold[c]
                continue
            h["missing"] = 0
            h["value"] = h["invest"] * o_cur / o_buy
            h["px_buy"] = o_buy
            hi = high_map.get((d, c))
            peak = h.get("peak") or h["px_buy"]
            if (i - h["buy_i"]) >= 2 and hi is not None and np.isfinite(hi) and hi > 0:
                h["peak"] = max(peak, hi)
            ret = o_cur / h["px_buy"] - 1
            down = down_map.get((d, c))
            if down is not None and np.isfinite(down) and o_cur <= down + 1e-9:
                reasons["SKIP_DOWN"] = reasons.get("SKIP_DOWN", 0) + 1
                continue
            sell, why = False, ""
            if (i - h["buy_i"]) >= N + 1:
                sell, why = True, "MATURE"
            elif (i - h["buy_i"]) < 2:
                sell = False
            else:
                trail_act = peak >= h["px_buy"] * (1 + A.TRAIL_ACT)
                trail_hit = trail_act and o_cur <= max(h["px_buy"], peak * (1 - A.TRAIL_PCT))
                if ret <= A.STOP:
                    sell, why = True, "STOP"
                elif ret >= A.TP:
                    sell, why = True, "TP"
                elif trail_hit:
                    sell, why = True, "TRAIL"
            if sell:
                amt = h["value"]
                cash += amt - A.sell_fee(amt, c)
                trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                reasons[why] = reasons.get(why, 0) + 1
                del hold[c]
        if row is not None and len(row) > 0 and i + 1 < M:
            buy_date = dates[i + 1]
            for ri in range(len(row)):
                if len(hold) >= topk:
                    break
                code = row.index[ri]
                if code in hold:
                    continue
                r = row.iloc[ri]
                if not (pd.notna(r["vol_next"]) and r["vol_next"] > 0
                        and (pd.isna(r["suspend_next"]) or str(r["suspend_next"]).strip() == "")
                        and (pd.isna(r["up_limit_next"]) or pd.isna(r["open_next"])
                             or not (r["open_next"] >= r["up_limit_next"]))):
                    n_skip_exec += 1
                    continue
                px = open_map.get((buy_date, code))
                if px is None or not (np.isfinite(px) and px > 0):
                    n_skip_exec += 1
                    continue
                n = topk - len(hold)
                budget = cash * 0.95 / n
                fee = A.buy_fee(budget, code)
                invest = budget - fee
                if invest <= 0:
                    break
                cash -= budget
                hold[code] = {"value": invest, "buy_val": invest, "invest": invest,
                              "buy_i": i, "px_buy": px, "peak": px, "missing": 0}
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        cash_frac.append(cash / total_now if total_now > 0 else 1.0)
        prev_total = total_now
    return trades, pd.Series(daily_ret), np.array(cash_frac), reasons, n_skip_exec


def run(strategy, topk, hold, model_override=None):
    cfg = A.STRATEGIES[strategy]
    model_path, meta_path = A.resolve_model(strategy)
    if model_override:
        model_path = model_override
    redline = cfg["redline"]
    panel = pd.read_parquet(cfg["panel"])
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    meta = json.load(open(meta_path, encoding="utf-8"))
    booster = lgb.Booster(model_file=model_path)
    daily = A.load_daily("2024-07-01", "2026-08-14")
    dates, per_day = A.build_signal(strategy, panel, meta, booster, daily, redline, "2024-07-01", "2026-08-14")
    trades, daily_ret, cash_frac, reasons, n_skip = simulate_bridge(dates, per_day, daily, topk, hold)

    # 等权基准（close->close，panel fwd_ret 均值，与历史审计同口径）
    bdf = pd.read_parquet(cfg["panel"], columns=["trade_date", "fwd_ret"])
    bdf["trade_date"] = pd.to_datetime(bdf["trade_date"])
    bdf = bdf[(bdf["trade_date"] >= pd.Timestamp("2024-07-01")) & (bdf["trade_date"] <= pd.Timestamp("2026-08-14"))]
    base = bdf.groupby("trade_date")["fwd_ret"].mean().reindex(daily_ret.index).fillna(0.0)

    excess = daily_ret - base
    n = len(daily_ret)
    nav = (1 + daily_ret).cumprod()
    bnav = (1 + base).cumprod()
    enav = (1 + excess).cumprod()
    print(f"== {strategy} K{topk} N{hold} model={os.path.basename(model_path)} ==")
    print(f"n_trades={len(trades)}  reasons={reasons}  n_skip_exec={n_skip}")
    print(f"avg_cash_frac={cash_frac.mean():.3f}  (全持仓期比例 {100*(1-cash_frac.mean()):.0f}%)")
    print(f"abs  : daily={daily_ret.mean()*100:.4f}%/日  ann={nav.iloc[-1]**(252/n)-1:.4f}  mdd={(nav/nav.cummax()-1).min():.4f}")
    print(f"bench: daily={base.mean()*100:.4f}%/日  ann={bnav.iloc[-1]**(252/n)-1:.4f}")
    print(f"excs : daily={excess.mean()*100:.4f}%/日  ann={enav.iloc[-1]**(252/n)-1:.4f}")
    # 持仓数分布
    n_hold = []
    for d in dates:
        row = per_day[d]
        n_hold.append(len(row) if row is not None else 0)
    ph = pd.Series(n_hold)
    print(f"cand/dist: 每日候选数 mean={ph.mean():.1f}  topk满补比例={100*((pd.Series(n_hold)>=topk).mean()):.0f}%")
    return excess.mean()


if __name__ == "__main__":
    # 模型版本对照：1522t(现live) vs 1964t(历史审计用) vs 2014t(若存在)
    for s, k, h, m in [
        ("g2", 2, 15, None),
        ("g2", 2, 15, r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"),
        ("v14", 2, 5, None),
    ]:
        run(s, k, h, m)
        print()
