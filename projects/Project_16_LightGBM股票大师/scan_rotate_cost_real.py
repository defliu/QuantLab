# coding: utf-8
"""含真实交易成本的轮动回测 —— 真实评分卡版（F2/F5 换真实数据）。

本文件是 scan_rotate_cost.py 的副本，唯一区别：逐日拼分改用真实版评分卡
（scorecard_real.compute_real_scorecard）：
  - F2 = RF.score_f2(main_net, volume_ratio)   真实主力净额 + 量比（替代量比代理）
  - F5 = RF.score_f5(industry_pct)             真实行业当日涨幅（替代相对动量代理）
  - F6 = RF.score_f6(pe_ttm, turnover_rate)    与原版 F6_new 一致
  - F1/F3/F4 沿用 DP.compute_scorecard 代理分
其余（成本/滑点/可执行口径/前100池/红线/top2/止损止盈）与原版完全一致。

用法（用环境变量指定 v3_sc 面板 + v3_enh 模型）：
  $env:BT_PANEL="data\feature_panel_v3_sc.parquet"
  $env:BT_MODEL="D:\QuantLab\models\lgb_model_v3_enh.txt"
  $env:BT_META="data\features_v3_enh.json"
  python scan_rotate_cost_real.py --exec
输出：data/real/scan_rotate_cost_real_report.md
"""
import argparse
import json
import os

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP
import qmt_config as C
import review_full as RF
import scorecard_real as SR

HERE = DC.PROJECT_DIR
PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3_sc.parquet")
MODEL = DC.model_file("_v3_enh")
META = os.path.join(DC.DATA_DIR, "features_v3_enh.json")
OUT_MD = os.path.join(DC.DATA_DIR, "real", "scan_rotate_cost_real_report.md")
# 支持自定义面板/模型/输出（A/B 对比测试用），可用环境变量覆盖
PANEL = os.environ.get("BT_PANEL", PANEL)
MODEL = os.environ.get("BT_MODEL", MODEL)
META = os.environ.get("BT_META", META)
OUT_MD = os.environ.get("BT_OUT", OUT_MD)

THRESHOLD = float(os.environ.get("BT_THRESHOLD", "58.0"))  # 红线评分阈值，可用 BT_THRESHOLD 覆盖
PRE_POOL = 100
TOP10 = 10
TOP = int(os.environ.get("BT_TOP", "2"))  # 持仓只数，可用环境变量 BT_TOP 覆盖（默认 2）
STOP = float(os.environ.get("BT_STOP", "-0.07"))  # 止损，可用 BT_STOP 覆盖
TP = float(os.environ.get("BT_TP", "0.15"))  # 止盈，可用 BT_TP 覆盖
N_LIST = [1, 3, 5, 10]
SLIPS = [0.0, 0.001, 0.002]  # 单边滑点
START = os.environ.get("BT_START", "2024-07-01")  # 测试区间起，可用 BT_START/BT_END 覆盖
END = os.environ.get("BT_END", "2026-08-14")
W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}

COMM_RATE, STAMP_RATE, TRANS_RATE = C.COMM_RATE, C.STAMP_RATE, C.TRANS_RATE  # 统一取 qmt_config（实盘口径）


def is_sh(code):
    return code.startswith("6")


def sell_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + amt * STAMP_RATE + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def buy_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def build_per_day():
    print("[1/3] 加载 + 逐日打分（真实版 F2/F5） ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)
    # 可执行口径需开盘价/涨跌停/停牌；一次取全量并按 ts_code shift(-1) 得到 T+1 快照
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
    open_map = daily["open"].to_dict()  # 全市场 open，(date, code) -> open，供持仓收益取价

    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    per_day, market_avgs = {}, []
    for d in dates:
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
        idx = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"]])
        est = daily.reindex(idx)
        sc_real = SR.compute_real_scorecard(day, est)
        for k in ("F1", "F2", "F3", "F4", "F5"):
            day[k] = sc_real[k].values
        day["F6_new"] = sc_real["F6"].values
        day["total_new"] = sc_real["total_new_real"].values
        # T+1 快照（shift(-1) 已对齐）：用于可执行口径的一字板/停牌过滤与 open→open 收益
        day["open_next"] = est["open_next"].values
        day["up_limit_next"] = est["up_limit_next"].values
        day["vol_next"] = est["vol_next"].values
        day["suspend_next"] = est["suspend_next"].values
        market_avgs.append(float(day["fwd_ret"].mean()))
        pre = day.nlargest(PRE_POOL, "prob")[["ts_code", "total_new", "fwd_ret", "prob", "open_next",
                                              "up_limit_next", "vol_next", "suspend_next"]].set_index("ts_code")
        per_day[d] = pre.nlargest(TOP10, "prob")
    return dates, per_day, pd.Series(market_avgs), open_map


def simulate(dates, per_day, N, slip, open_map, exec_ok=True):
    """资金模拟：真实金额(初始10万)，满仓2只，换仓扣真实费用。返回 trades 与日收益序列。

    exec_ok=True  → 可执行口径（open→open + 一字板/停牌过滤，审计 R1 新基线）
    exec_ok=False → 原 close→close 口径（对比用，保留历史行为）
    """
    M = len(dates)
    cash = 100000.0  # 真实金额，佣金最低5元才正确生效
    hold = {}      # code -> dict(value=市值, buy_val=买入成本市值, buy_i, invest=投入本金)
    trades, daily_ret = [], []
    prev_total = 100000.0
    n_skip = 0
    for i, d in enumerate(dates):
        row = per_day[d]
        if i > 0:
            for c in list(hold):
                h = hold[c]
                if exec_ok:
                    # 可执行口径：持仓按全市场 open 逐日盯市（buy_i 决策 → buy_i+1 开盘买入）
                    o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
                    o_cur = open_map.get((d, c))
                    if o_buy and o_cur and o_buy > 0:
                        h["value"] = h["invest"] * o_cur / o_buy
                else:
                    prev = per_day[dates[i - 1]]
                    r = float(prev.loc[c, "fwd_ret"]) if c in prev.index else 0.0
                    h["value"] *= (1 + r)
        # 卖出：止损/止盈/期满
        for c in list(hold):
            h = hold[c]
            if exec_ok:
                o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
                o_cur = open_map.get((d, c))
                if not (o_buy and o_cur and o_buy > 0):
                    continue
                ret = o_cur / o_buy - 1
                if ret <= STOP or ret >= TP or (i - h["buy_i"]) >= N + 1:  # 买入在 buy_i+1 开盘，持有 N 天，卖出在 buy_i+1+N
                    amt = h["value"]
                    cash += amt - sell_fee(amt, c, slip)
                    trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                    del hold[c]
            else:
                if h["value"] / h["buy_val"] - 1 <= STOP or h["value"] / h["buy_val"] - 1 >= TP or (i - h["buy_i"]) >= N:
                    amt = h["value"]
                    cash += amt - sell_fee(amt, c, slip)
                    trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                    del hold[c]
        # 买入：补足到 TOP
        while len(hold) < TOP:
            cand = row[~row.index.isin(hold)]
            cand = cand[cand["total_new"] >= THRESHOLD]
            if len(cand) == 0:
                break
            # 可执行口径：一字涨停/停牌买不进 → 剔除
            if exec_ok:
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
            fee = buy_fee(budget, best, slip)
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        # 轮动：持仓未满2 且 len>0，新 top 超最弱 X 分则换（此处固定不设 X，X=None 即不做）
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    # 期末清仓（不计费，仅补 trades）
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--naive", action="store_true", help="仅跑原 close→close 口径（对比用）")
    ap.add_argument("--exec", action="store_true", help="仅跑可执行 open→open 口径")
    args = ap.parse_args()
    # 默认双口径都跑；--exec / --naive 只跑指定的一种
    do_exec = not args.naive
    do_naive = not args.exec

    dates, per_day, market_daily, open_map = build_per_day()
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 持仓{TOP} | 止损{STOP:.0%}/止盈{TP:.0%}")
    print("[2/3] 含成本模拟（持有期 × 滑点 × 口径）...")
    rows = []
    for N in N_LIST:
        for slip in SLIPS:
            if do_naive:
                trades, daily_ret, _ = simulate(dates, per_day, N, slip, open_map, exec_ok=False)
                s = stats(trades, daily_ret, market_daily)
                rows.append({"N": N, "slip": slip, "口径": "close→close(原)", **s})
                print(f"    N={N} 滑点{slip:.1%} [原口径]: 超额{s['daily_excess']:.3%} 胜率{s['win_rate']:.1%} "
                      f"盈亏比{s['profit_loss_ratio']:.2f} 回撤{s['max_drawdown']:.1%} 交易{s['n_trades']}")
            if do_exec:
                trades, daily_ret, n_skip = simulate(dates, per_day, N, slip, open_map, exec_ok=True)
                s = stats(trades, daily_ret, market_daily)
                rows.append({"N": N, "slip": slip, "口径": "open→open(可执行)", **s})
                print(f"    N={N} 滑点{slip:.1%} [可执行]: 超额{s['daily_excess']:.3%} 胜率{s['win_rate']:.1%} "
                      f"盈亏比{s['profit_loss_ratio']:.2f} 回撤{s['max_drawdown']:.1%} 交易{s['n_trades']} 跳过{n_skip}")
    res = pd.DataFrame(rows)
    print("[3/3] 保存报告 ...")
    lines = [
        "# 含真实交易成本的轮动回测 —— 真实评分卡版（F2/F5 真实数据）",
        "",
        f"> 测试期 {START} ~ {END} | 前10池+红线{THRESHOLD}+top{TOP} | 成本：佣金万2(双边,最低5元) + 印花税万5(卖出) + 过户费万0.1(沪市) | 滑点敏感性 0/0.1%/0.2%",
        "",
        "> **评分差异**：F2 = RF.score_f2(真实主力净额, 量比)、F5 = RF.score_f5(真实行业当日涨幅)、F6 = RF.score_f6(PE,换手)；F1/F3/F4 沿用 DP 代理分。",
        "",
        "| 口径 | 持有期 | 滑点/边 | 交易数 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['口径']} | {r['N']}天 | {r['slip']:.1%} | {r['n_trades']} | {r['win_rate']:.1%} "
            f"| {r['profit_loss_ratio']:.2f} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
