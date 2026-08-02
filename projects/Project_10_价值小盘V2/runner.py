# coding=utf-8
"""Project 10: 价值小盘 V2 微调版 — 回测入口
状态机口径回测，含风控模块
用法: python runner.py"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))  # Project_10 优先
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")

import yaml
import pandas as pd
import numpy as np
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

from strategy.scoring import V2Scorer
from strategy.risk import RiskController

# --------------- 加载配置 ---------------
CFG_PATH = os.path.join(os.path.dirname(__file__), "config", "strategy.yaml")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

# --------------- 数据加载 ---------------
t0 = time.time()

daily = pd.read_parquet(DAILY_PATH)
idx = daily.index
start_ts = pd.Timestamp(START_DATE).date()
codes_all = set(
    idx.get_level_values("ts_code")[idx.get_level_values("trade_date") >= start_ts].unique()
)
daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
idx = daily.index
daily = daily.loc[
    (idx.get_level_values("trade_date") >= start_ts)
    & (idx.get_level_values("trade_date") <= pd.Timestamp(END_DATE).date())
].copy()
idx = daily.index

prev_close = daily["close"].groupby(level=1).shift(1)
panel = pd.DataFrame({
    "close": daily["close"].values, "open": daily["open"].values,
    "high": daily["high"].values, "low": daily["low"].values,
    "pe_ttm": daily["pe_ttm"].values, "pb": daily["pb"].values,
    "circ_mv": daily["circ_mv"].values, "amount": daily["amount"].values,
    "prev_close": prev_close.values,
}, index=idx)

# 排除ST和停牌
is_st = daily["is_st"].astype(bool)
suspend = daily["suspend_type"].fillna("N")
panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]

# 行业映射
basic = pd.read_parquet(r"E:/astock/basic/stock_basic.parquet")
ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))

# 财务数据
fin = pd.read_parquet(r"E:/astock/finance/fina_indicator.parquet")
fin = fin[["ts_code", "end_date", "ann_date", "bps", "roe", "profit_dedt", "debt_to_assets"]].copy()
fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
fin = fin.dropna(subset=["ann_date"])
fin = fin[fin["ts_code"].isin(codes_all)]
fin = fin.sort_values(["ts_code", "ann_date"])
fin_by_code = {c: g for c, g in fin.groupby("ts_code")}

print("panel:", panel.shape, " 用时: %.1fs" % (time.time() - t0))

# --------------- 工具函数 ---------------
trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
rebal = get_rebalance_dates(panel, freq=CFG["portfolio"]["rebalance_freq"])

pb_wide = panel["pb"].unstack("ts_code")
pb_wide.index = pd.DatetimeIndex(pb_wide.index)

# 初始化评分器
scorer = V2Scorer(
    ind_map=ind_map,
    z_weight=CFG["scoring"]["weights"]["z_score"],
    hp_weight=CFG["scoring"]["weights"]["hist_pct"],
    hp_window=CFG["scoring"]["hp_window_months"],
    hp_min=CFG["scoring"]["hp_min_months"],
)
scorer.compute_bp_monthly(pb_wide)

# 初始化风控
risk = RiskController(
    stop_loss=CFG["risk_control"]["stop_loss_pct"],
    max_drawdown=CFG["risk_control"]["max_drawdown_pct"],
    max_holding_days=CFG["risk_control"]["max_holding_days"],
    max_daily_turnover=CFG["risk_control"]["max_daily_turnover"],
    state_file=os.path.join(os.path.dirname(__file__), "results", "risk_state.json"),
)

# --------------- 候选股筛选 ---------------
_fin_cache = {}
def fin_snapshot(date):
    d = pd.Timestamp(date)
    if d in _fin_cache:
        return _fin_cache[d]
    rows = {}
    for c, g in fin_by_code.items():
        g = g[g["ann_date"] <= d]
        if len(g) == 0:
            continue
        last = g.iloc[-1]
        rows[c] = (last["bps"], last["roe"], last["profit_dedt"], last["debt_to_assets"])
    _fin_cache[d] = rows
    return rows

_cand_cache = {}
def get_candidates(d, dd):
    if d in _cand_cache:
        return _cand_cache[d]
    ucfg = CFG["universe"]
    m = ((dd["circ_mv"] > ucfg["market_cap_min"])
         & (dd["circ_mv"] < ucfg["market_cap_max"])
         & (dd["pe_ttm"] > ucfg["pe_ttm_min"])
         & (dd["pb"] > ucfg["pb_min"]))
    if CFG["quality_screen"]["enabled"]:
        fs = fin_snapshot(d)
        mq = m.copy()
        for c in mq.index:
            r = fs.get(c)
            if r is None or not (r[0] > 0 and r[2] > 0 and r[1] > 0):
                mq[c] = False
        m = mq
    r = m[m].index
    _cand_cache[d] = r
    return r

# --------------- 涨跌停判断 ---------------
def limit_pct(code):
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10

# --------------- 回测主循环 ---------------
def run_backtest(force_full_turn=False):
    """状态机口径回测，含风控"""
    rows = []
    prev_holdings = {}
    prev_cost = 0.0
    prev_turnover, prev_sells, prev_buys = 0.0, 0, 0
    nav = 1.0
    tx_cost = CFG["transaction"]["cost_rate"]
    limit_handling = CFG["transaction"]["limit_handling"]
    n_stocks = CFG["portfolio"]["n_stocks"]

    for i in range(len(rebal) - 1):
        d = rebal[i]
        e_idx = ti[pd.Timestamp(d)] + 1
        if e_idx >= len(trade_dates):
            break
        e_date = trade_dates[e_idx]
        e_row = panel.loc[e_date]

        # 1. 结算上期持仓收益
        if prev_holdings:
            rets = []
            for code, base in prev_holdings.items():
                xo = e_row["open"].get(code)
                if xo is not None and xo > 0 and base is not None and base > 0:
                    rets.append(xo / base - 1.0)
            if len(rets) > 0:
                period_ret = np.mean(rets) - prev_cost
                nav *= (1 + period_ret)
                rows.append({
                    "date": pd.Timestamp(e_date),
                    "period_return": period_ret,
                    "nav": nav,
                    "n": len(prev_holdings),
                    "turnover": prev_turnover,
                    "sells": prev_sells,
                    "buys": prev_buys,
                })

        # 2. 组合回撤检查
        drawdown_triggered, dd = risk.check_drawdown(nav)
        if drawdown_triggered and prev_holdings:
            # 清仓
            for code in list(prev_holdings.keys()):
                risk.register_exit(code)
            prev_holdings = {}
            prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
            continue

        # 3. 风控：止损 + 持有期
        current_prices = {}
        for code in prev_holdings:
            xo = e_row["open"].get(code)
            if xo is not None and xo > 0:
                current_prices[code] = xo
        risk_sell = risk.update_holdings(prev_holdings, current_prices, e_date)
        for code in risk_sell:
            if code in prev_holdings:
                del prev_holdings[code]
                risk.register_exit(code)

        # 4. 选股 + 评分
        dd_data = panel.loc[d]
        cand = get_candidates(d, dd_data)
        if len(cand) < 10:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue
        score = scorer.score(d, cand, dd_data["pb"]).dropna()
        if len(score) == 0:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue

        # 5. 目标组合（排除禁入股）
        target = set()
        for code in score.sort_values(ascending=False).index:
            if len(target) >= n_stocks:
                break
            if not risk.is_banned(code, e_date):
                target.add(code)

        # 6. 卖出不在目标中的 + 涨跌停保护
        sells, buys = 0, 0
        new_holdings = {}
        for code, base in prev_holdings.items():
            if code in target and not force_full_turn:
                new_holdings[code] = e_row["open"].get(code, base)
                risk.register_entry(code, new_holdings[code], e_date) if code not in risk.holdings else None
                continue
            xo, xpc = e_row["open"].get(code), e_row["prev_close"].get(code)
            xl = e_row["low"].get(code)
            stuck = (limit_handling and xo is not None and xpc is not None and xpc > 0
                     and xl is not None and xo <= xpc * (1 - limit_pct(code) + 0.005)
                     and xo <= xl * 1.001)
            if stuck and not force_full_turn:
                new_holdings[code] = xo if xo is not None else base
            else:
                sells += 1
                risk.register_exit(code)

        # 7. 买入新标的
        for code in target:
            if code in new_holdings:
                continue
            eo, epc = e_row["open"].get(code), e_row["prev_close"].get(code)
            eh = e_row["high"].get(code)
            buyable = (not limit_handling or eo is None or epc is None or epc <= 0 or eh is None
                       or not (eo >= epc * (1 + limit_pct(code) - 0.005) and eo >= eh * 0.999))
            if buyable and eo is not None and eo > 0:
                new_holdings[code] = eo
                risk.register_entry(code, eo, e_date)
                buys += 1

        prev_holdings = new_holdings
        prev_turnover = (sells + buys) / float(n_stocks)
        prev_cost = prev_turnover * tx_cost
        prev_sells, prev_buys = sells, buys

    risk.save_state()
    return pd.DataFrame(rows)

# --------------- 基准 ---------------
def run_base():
    rows = []
    for i in range(len(rebal) - 1):
        d = rebal[i]
        nxt = rebal[i + 1]
        e_idx = ti[pd.Timestamp(d)] + 1
        x_idx = ti[pd.Timestamp(nxt)] + 1
        if x_idx >= len(trade_dates):
            break
        dd = panel.loc[d]
        e_open = panel.loc[trade_dates[e_idx], "open"]
        x_open = panel.loc[trade_dates[x_idx], "open"]
        cand = get_candidates(d, dd)
        valid = [c for c in cand if e_open.get(c) is not None and x_open.get(c) is not None
                 and e_open.get(c) > 0 and x_open.get(c) > 0]
        if len(valid) >= 30:
            rows.append({"date": pd.Timestamp(trade_dates[x_idx]),
                          "ret": np.mean([x_open[c] / e_open[c] - 1.0 for c in valid])})
    return pd.DataFrame(rows).set_index("date")

# --------------- 窗口超额 ---------------
def nav_ret(per_ret_series, start, end):
    seg = per_ret_series[(per_ret_series.index >= start) & (per_ret_series.index < end)]
    if len(seg) == 0:
        return None, 0
    return (1 + seg).prod() - 1, len(seg)

# --------------- 主程序 ---------------
if __name__ == "__main__":
    log = []

    def p(*args):
        s = " ".join(str(a) for a in args)
        print(s)
        log.append(s)

    p("============ 1. 基准 ============")
    base = run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    b_years = (base.index[-1] - base.index[0]).days / 365.25
    p("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100, ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100))

    p("\n============ 2. 策略（状态机+风控） ============")
    result = run_backtest()
    if len(result) > 0:
        result = result.set_index("date")
        cum = (1 + result["period_return"]).cumprod() - 1
        years = (result.index[-1] - result.index[0]).days / 365.25
        ann = (1 + cum.iloc[-1]) ** (1 / years) - 1
        p("策略: 累计=%7.1f%% 年化=%6.1f%% 期数=%d 平均换手=%.2f" % (
            cum.iloc[-1] * 100, ann * 100, len(result), result["turnover"].mean()))

        p("\n============ 3. 窗口超额 ============")
        windows = [("全期2018-2026", "2018-01-01", "2027-01-01"),
                   ("2026至今", "2026-01-01", "2027-01-01"),
                   ("2024+", "2024-01-01", "2027-01-01")]
        for label, s, e in windows:
            sr, sn = nav_ret(result["period_return"], s, e)
            br, bn = nav_ret(base["ret"], s, e)
            if sr is not None and br is not None:
                p("  %-16s 策略=%+7.1f%%  基准=%+7.1f%%  超额=%+7.1f%%  (n=%d/%d)" % (
                    label, sr * 100, br * 100, (sr - br) * 100, sn, bn))

        p("\n============ 4. 2026 逐期明细 ============")
        m26 = result[result.index >= "2026-01-01"]
        for d, r in m26.iterrows():
            p("  %s  收益=%+6.1f%%  nav=%6.2f  n=%3d  卖出=%3d 买入=%3d" % (
                d.strftime("%Y-%m-%d"), r["period_return"] * 100, r["nav"], r["n"], r["sells"], r["buys"]))

    p("\n总用时 %.0fs" % (time.time() - t0))

    out_path = os.path.join(os.path.dirname(__file__), "results", "backtest_result.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    p("结果已写入:", out_path)
