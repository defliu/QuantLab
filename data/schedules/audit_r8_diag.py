# coding: utf-8
"""R8 复算诊断：打印 V14 K4 的出场分布、持仓天数、单笔收益分布，与 DE 对照定位差异。"""
import json
import os
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, r"D:\QuantLab\projects\Project_16_LightGBM股票大师")
sys.path.insert(0, r"D:\QuantLab\data\schedules")
import data_config as DC
import deploy_predict as DP
import qmt_config as C
import audit_r8 as A

PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
START, END = "2024-07-01", "2026-08-14"
REDLINE, N, TOPK = 58.0, 5, 4


def build():
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    meta = json.load(open(os.path.join(DC.DATA_DIR, "features_v3.json"), encoding="utf-8"))
    booster = lgb.Booster(model_file=DC.model_file("_v3"))
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "high", "low", "close", "up_limit", "down_limit",
                                                    "vol", "suspend_timing"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    _d0 = pd.Timestamp(START) - pd.Timedelta(days=45)
    daily = daily[daily["trade_date"] >= _d0].sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1)
    daily = daily.set_index(["trade_date", "ts_code"])
    dates, per_day = A.build_signal(panel, meta, booster, daily, REDLINE, START, END)
    return dates, per_day, daily["open"].to_dict(), daily["down_limit"].to_dict()


def sim_detail(dates, per_day, open_map, down_map):
    """同 audit_r8.simulate 但记录每笔交易明细 + 出场原因。"""
    cash = 100000.0
    prev_total = cash
    hold = {}
    trades, daily_ret = [], []
    reasons = {}
    for i, d in enumerate(dates):
        row = per_day[d]
        for c in list(hold):
            h = hold[c]
            if h["buy_i"] + 1 >= len(dates):
                continue
            o_buy = open_map.get((dates[h["buy_i"] + 1], c))
            o_cur = open_map.get((d, c))
            if o_buy and o_cur and o_buy > 0:
                h["value"] = h["invest"] * o_cur / o_buy
            else:
                h["missing_days"] = h.get("missing_days", 0) + 1
                if h["missing_days"] >= 60:
                    amt = h["value"]
                    cash += amt * 0.5 - A.sell_fee(amt, c)
                    trades.append((h["buy_i"], i, 0.5 * amt / h["buy_val"] - 1, "DELIST"))
                    del hold[c]
                continue
            h["missing_days"] = 0
        for c in list(hold):
            h = hold[c]
            if h["buy_i"] + 1 >= len(dates):
                continue
            o_buy = open_map.get((dates[h["buy_i"] + 1], c))
            o_cur = open_map.get((d, c))
            if not (o_buy and o_cur and o_buy > 0):
                continue
            down = down_map.get((d, c))
            one_word_down = down is not None and not (isinstance(down, float) and np.isnan(down)) and o_cur <= down + 1e-9
            if one_word_down:
                continue
            ret = o_cur / o_buy - 1
            age = i - h["buy_i"]
            sell, why = False, ""
            if age >= N + 1:
                sell, why = True, "MATURE"
            elif age < 2:
                sell, why = False, ""
            elif ret <= -0.07 or ret >= 0.15:
                why = "STOP" if ret <= -0.07 else "TP"
                sell = True
            if sell:
                amt = h["value"]
                cash += amt - A.sell_fee(amt, c)
                trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1, why))
                reasons[why] = reasons.get(why, 0) + 1
                del hold[c]
        while len(hold) < TOPK:
            cand = row[~row["ts_code"].isin(hold)]
            cand = cand[cand["sc_total"] >= REDLINE]
            if len(cand) == 0:
                break
            cand = cand[cand["vol_next"].notna() & (cand["vol_next"] > 0)
                        & (cand["suspend_next"].isna())
                        & (cand["up_limit_next"].isna() | (cand["open_next"] < cand["up_limit_next"]))]
            if len(cand) == 0:
                break
            best = cand.nlargest(1, "prob").iloc[0]
            n = TOPK - len(hold)
            budget = cash * 0.95 / n
            fee = A.buy_fee(budget, best["ts_code"])
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best["ts_code"]] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    for c, h in hold.items():
        trades.append((h["buy_i"], len(dates), h["value"] / h["buy_val"] - 1, "EOC"))
    return trades, pd.Series(daily_ret), reasons


def main():
    dates, per_day, open_map, down_map = build()
    print(f"dates {len(dates)} {dates[0].date()}~{dates[-1].date()}")
    trades, daily_ret, reasons = sim_detail(dates, per_day, open_map, down_map)
    rets = [t[2] for t in trades]
    fwd = pd.Series(rets)
    hold_days = [t[1] - t[0] for t in trades]
    print("n_trades:", len(trades), "reasons:", reasons)
    print("avg_hold_days:", np.mean(hold_days), "median:", np.median(hold_days))
    print("win_rate:", (fwd > 0).mean(), "avg_win:", fwd[fwd > 0].mean() if (fwd > 0).any() else None,
          "avg_loss:", fwd[fwd < 0].mean() if (fwd < 0).any() else None)
    print("annual:", (1 + daily_ret).cumprod().iloc[-1] ** (252 / len(daily_ret)) - 1,
          "mdd:", ((1 + daily_ret).cumprod() / (1 + daily_ret).cumprod().cummax() - 1).min())
    print("\n前 15 笔明细 (buy_i, sell_i, hold, ret, why):")
    for t in trades[:15]:
        print(" ", t[0], t[1], t[1] - t[0], round(t[2], 4), t[3])


if __name__ == "__main__":
    main()
