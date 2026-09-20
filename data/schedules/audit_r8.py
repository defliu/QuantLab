# coding: utf-8
"""R8 独立审核复算脚本（opencode 侧，独立于 DE 实现）—— v2 全口径对齐。

用途：DE 完成 R8 报告后，用同一口径独立复算关键网格点，核对 DE 数字。
口径对齐 DE/de_r8_common.py：
  信号：T 日收盘模型 prob 前100池 → 评分卡红线 → prob top10 候选（按 total 降序买入）
  成交：T+1 open 买入，一字涨停/停牌/无量不买；等权 K 只，budget=cash*0.95/n
  出场：live_trail（open 评估）：STOP -7% → TP +15% → TRAIL(峰值≥+8%激活，回撤8%，
        保本底线 max(成本,峰值*0.92))；持有满 N+1 交易日 MATURE；一字跌停 SKIP_DOWN 顺延；
        连续 60 日停牌 DELIST 按 50% 结算
  成本：佣金万2双边最低5元 + 印花税万5卖出 + 过户费万0.1沪市双边（滑点 0）

用法：
  python audit_r8.py --strategy v14 --topk 2 --hold 5 --start 2024-07-01 --end 2026-08-14
输出：--out 指定 JSON（默认打印），不写任何项目目录。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, r"D:\QuantLab\projects\Project_16_LightGBM股票大师")
import data_config as DC
import deploy_predict as DP
import qmt_config as C

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
MAIN_DAILY = DC.MAIN_DAILY
COMM_RATE, STAMP_RATE, TRANS_RATE, COMM_MIN = C.COMM_RATE, C.STAMP_RATE, C.TRANS_RATE, C.COMM_MIN

STOP, TP = -0.07, 0.15
TRAIL_ACT, TRAIL_PCT = 0.08, 0.08
POOL_N, TOP_LIST = 100, 10
DELIST_DAYS, DELIST_LOSS = 60, 0.5

STRATEGIES = {
    # 策略 -> dict(panel, model, meta, redline, hold, score)
    "v14": dict(panel=os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet"),
                model=DC.model_file("_v3"),
                meta=os.path.join(DC.DATA_DIR, "features_v3.json"),
                redline=58.0, hold=5, score="proxy"),
    "g2":  dict(panel=os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"),
                model=None, meta=None, redline=60.0, hold=15, score="real"),
    "ens": dict(panel=os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"),
                model=None, meta=None, redline=60.0, hold=10, score="real"),
}


def is_sh(code):
    return str(code).startswith("6")


def sell_fee(amt, code):
    comm = max(COMM_MIN, amt * COMM_RATE)
    return comm + amt * STAMP_RATE + (amt * TRANS_RATE if is_sh(code) else 0.0)


def buy_fee(amt, code):
    comm = max(COMM_MIN, amt * COMM_RATE)
    return comm + (amt * TRANS_RATE if is_sh(code) else 0.0)


def resolve_model(strategy):
    if strategy == "v14":
        return STRATEGIES["v14"]["model"], STRATEGIES["v14"]["meta"]
    mp, mj = DC.g2_live()   # g2/ens 统一走 g2_live 指针
    return mp, mj


def load_daily(start, end):
    """日线上下文：open/high/down_limit/vol/suspend_timing + open_next 族 + pe_ttm/turnover_rate。"""
    daily = pd.read_parquet(MAIN_DAILY, columns=["open", "high", "low", "close", "up_limit", "down_limit",
                                                 "vol", "suspend_timing", "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["trade_date"] >= pd.Timestamp(start) - pd.Timedelta(days=45)]
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
    return daily


def build_signal(strategy, panel, meta, booster, daily, redline, start, end):
    """对齐 DE build_pools：prob top100 → 评分卡红线 → prob top10（按 total 降序）。"""
    feat_cols = meta["feature_cols"]
    score_mode = STRATEGIES[strategy]["score"]
    need_est = score_mode == "real"
    est = daily[["pe_ttm", "turnover_rate"]] if need_est else None
    dates = sorted(panel.loc[(panel["trade_date"] >= start) & (panel["trade_date"] <= end), "trade_date"].unique())
    per_day = {}
    for d in dates:
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
        pool = day.nlargest(POOL_N, "prob").copy()
        if score_mode == "proxy":
            pool["sc_total"] = DP.compute_scorecard(pool)["total"].values
        else:
            idx = pd.MultiIndex.from_arrays([pool["trade_date"], pool["ts_code"].astype(str)])
            sc = DP.compute_scorecard(pool)  # F1/F3/F4/F6 代理部分
            import scorecard_real as SR
            total = SR.compute_real_scorecard(pool, est.reindex(idx))["total_new_real"].values
            pool["sc_total"] = total
        passed = pool[pool["sc_total"] >= redline]
        cand = passed.nlargest(TOP_LIST, "prob") if len(passed) else passed
        idx2 = pd.MultiIndex.from_arrays([cand["trade_date"], cand["ts_code"].astype(str)])
        dq = daily.reindex(idx2)
        cand["open_next"] = dq["open_next"].values
        cand["up_limit_next"] = dq["up_limit_next"].values
        cand["vol_next"] = dq["vol_next"].values
        cand["suspend_next"] = dq["suspend_next"].values
        cand = cand.sort_values("sc_total", ascending=False)
        cand = cand.set_index("ts_code")
        per_day[d] = cand
    return dates, per_day


def simulate(dates, per_day, daily, topk, N):
    """等权 open→open，live_trail 出场（对齐 de_r8_common.simulate）。"""
    open_map = daily["open"].to_dict()
    high_map = daily["high"].to_dict()
    down_map = daily["down_limit"].to_dict()
    cash = 100000.0
    prev_total = cash
    hold = {}   # code -> dict(value, buy_val, invest, buy_i, px_buy, peak, missing)
    trades, daily_ret = [], []
    reasons, n_skip_exec = {}, 0
    M = len(dates)
    for i, d in enumerate(dates):
        row = per_day[d]
        # 盯市 + 出场评估（开盘价，live_trail）
        for c in list(hold):
            h = hold[c]
            o_buy = open_map.get((dates[h["buy_i"] + 1], c))
            o_cur = open_map.get((d, c))
            if o_buy is None or o_cur is None or not (o_buy > 0 and o_cur > 0):
                h["missing"] = h.get("missing", 0) + 1
                if h["missing"] >= DELIST_DAYS:
                    amt = h["value"] * (1 - DELIST_LOSS)
                    cash += amt - sell_fee(amt, c)
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
                trail_act = peak >= h["px_buy"] * (1 + TRAIL_ACT)
                trail_hit = trail_act and o_cur <= max(h["px_buy"], peak * (1 - TRAIL_PCT))
                if ret <= STOP:
                    sell, why = True, "STOP"
                elif ret >= TP:
                    sell, why = True, "TP"
                elif trail_hit:
                    sell, why = True, "TRAIL"
            if sell:
                amt = h["value"]
                cash += amt - sell_fee(amt, c)
                trades.append((h["buy_i"], i, amt / h["buy_val"] - 1))
                reasons[why] = reasons.get(why, 0) + 1
                del hold[c]
        # 买入：top10 候选按 total 降序补足 topk（对齐 DE）
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
                fee = buy_fee(budget, code)
                invest = budget - fee
                if invest <= 0:
                    break
                cash -= budget
                hold[code] = {"value": invest, "buy_val": invest, "invest": invest,
                              "buy_i": i, "px_buy": px, "peak": px, "missing": 0}
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
    return trades, pd.Series(daily_ret), {"reasons": reasons, "n_skip_exec": n_skip_exec}


def stats(trades, daily_ret, info, start, end):
    rets = [t[2] for t in trades]
    fwd = pd.Series(rets)
    n = len(daily_ret)
    out = {"n_days": n, "n_trades": len(trades)}
    if len(fwd):
        win = float((fwd > 0).mean())
        aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
        al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
        out.update(win_rate=win, profit_loss_ratio=(aw / abs(al) if al and al != 0 else np.nan))
    if n:
        nav = (1 + daily_ret).cumprod()
        years = n / 252.0
        out["total_return"] = float(nav.iloc[-1] - 1)
        out["annual_return"] = float(nav.iloc[-1] ** (1.0 / years) - 1)
        out["max_drawdown"] = float((nav / nav.cummax() - 1).min())
        sd = float(daily_ret.std())
        out["sharpe"] = float(daily_ret.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan
    out["reasons"] = info["reasons"]
    out["n_skip_exec"] = info["n_skip_exec"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    ap.add_argument("--topk", type=int, default=2)
    ap.add_argument("--hold", type=int, default=None)
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-08-14")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cfg = STRATEGIES[args.strategy]
    model_path, meta_path = resolve_model(args.strategy)
    redline = cfg["redline"]
    hold = args.hold or cfg["hold"]
    print(f"[audit_r8] strategy={args.strategy} topk={args.topk} hold={hold} redline={redline} "
          f"period={args.start}~{args.end} model={os.path.basename(model_path)}")

    panel = pd.read_parquet(cfg["panel"])
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    meta = json.load(open(meta_path, encoding="utf-8"))
    booster = lgb.Booster(model_file=model_path)
    daily = load_daily(args.start, args.end)

    dates, per_day = build_signal(args.strategy, panel, meta, booster, daily, redline, args.start, args.end)
    trades, daily_ret, info = simulate(dates, per_day, daily, args.topk, hold)
    result = {"strategy": args.strategy, "topk": args.topk, "hold": hold, "redline": redline,
              "period": [args.start, args.end]}
    result.update(stats(trades, daily_ret, info, args.start, args.end))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("[audit_r8] saved:", args.out)


if __name__ == "__main__":
    main()
