# coding=utf-8
"""方向1 (P0): 回测风控每日检查对齐实盘 —— 严格逐日全口径

与 runner.py 状态机口径对比。本实现:
  - 每日按 close 结算持仓收益更新 nav (实盘口径)
  - 每日检查止损/持有期: 触发则次日 open 卖出 (次优, 实盘是当日成交)
  - 换仓日: 按 open 调仓, buffer 160 保留
  - 回撤检查: 每日基于当日 nav

用法: python research/dir1_daily_risk.py
"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import runner as R

RES = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
os.makedirs(RES, exist_ok=True)

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)

panel, ti, trade_dates = R.panel, R.ti, R.trade_dates
rebal = R.rebal
risk = R.risk
CFG = R.CFG
n_stocks = CFG["portfolio"]["n_stocks"]
tx_cost = CFG["transaction"]["cost_rate"]
limit_handling = CFG["transaction"]["limit_handling"]
limit_pct = R.limit_pct

# 换仓日: d 用 rebal[i] 的选股数据, e_date 用次日 open 执行
rebal_set = set(pd.Timestamp(x) for x in rebal)


def reset_risk():
    risk._state = {"holdings": {}, "nav_peak": 1.0, "禁入列表": {}, "dd_tier": 0}


def run_daily(risk_check_daily=True):
    """逐日全口径回测: 每日 close 结算 nav + 风控 + 换仓日 open 调仓
    risk_check_daily=False 时风控仅换仓日执行 (对照口径)"""
    reset_risk()
    cash = 1.0          # 资金净值, 先按全仓等权近似
    prev_holdings = {}  # code -> 持仓权重份额 (等权 1/n)
    base_px = {}        # code -> 成本价
    pending_sell = set()   # 次日开盘要卖的 (止损触发)
    nav = 1.0
    prev_cost = 0.0
    rows = []

    for di in range(len(trade_dates)):
        tdate = trade_dates[di]
        tday = pd.Timestamp(tdate)
        row = panel.loc[tdate]

        # 1. 盘前: 处理昨日触发止损的卖出 (按今日 open)
        if pending_sell:
            for code in list(pending_sell):
                xo = row["open"].get(code)
                if xo is not None and xo > 0:
                    pass  # 卖出已从持仓剔除, 收益通过 nav 结算体现
                pending_sell.discard(code)
                risk.register_exit(code)

        # 2. 换仓日调仓 (按 open)
        if tday in rebal_set:
            d = tday
            dd_data = panel.loc[d]
            cand = R.get_candidates(d, dd_data)
            if len(cand) < 10:
                prev_holdings, base_px = {}, {}
                prev_cost = 0.0
                continue
            score = R.scorer.score(d, cand, dd_data["pb"]).dropna()
            if len(score) == 0:
                prev_holdings, base_px = {}, {}
                prev_cost = 0.0
                continue
            order = score.sort_values(ascending=False)
            order_nb = [c for c in order.index if not risk.is_banned(c, d)]
            rank_nb = {c: r + 1 for r, c in enumerate(order_nb)}
            buffer_keep = int((CFG.get("rebalance", {}) or {}).get("buffer_keep", 0) or 0)
            keep_max = buffer_keep if buffer_keep > 0 else n_stocks
            buy_zone = order_nb[:n_stocks]

            new_holdings, new_base = {}, {}
            sells = buys = 0
            # 卖出: 不在 buffer 保留界内
            for code in prev_holdings:
                rk = rank_nb.get(code)
                keep = (rk is not None and rk <= keep_max)
                if keep:
                    new_holdings[code] = 1.0 / n_stocks
                    new_base[code] = row["open"].get(code, base_px.get(code, 0))
                    continue
                sells += 1
                risk.register_exit(code)
            # 买入: 补到 n_stocks
            for code in buy_zone:
                if len(new_holdings) >= n_stocks:
                    break
                if code in new_holdings:
                    continue
                eo, epc = row["open"].get(code), row["prev_close"].get(code)
                eh = row["high"].get(code)
                buyable = (not limit_handling or eo is None or epc is None or epc <= 0 or eh is None
                           or not (eo >= epc * (1 + limit_pct(code) - 0.005) and eo >= eh * 0.999))
                if buyable and eo is not None and eo > 0:
                    new_holdings[code] = 1.0 / n_stocks
                    new_base[code] = eo
                    buys += 1
            prev_holdings = new_holdings
            base_px = new_base
            prev_cost = ((sells + buys) / float(n_stocks)) * tx_cost

        # 3. 每日收益结算 (按当日 close 相对前一日 close, 实盘口径) + 记录
        if prev_holdings:
            rets = []
            for code, w in prev_holdings.items():
                xc = row["close"].get(code)
                pc = row["prev_close"].get(code)
                if xc is not None and xc > 0 and pc is not None and pc > 0:
                    rets.append(xc / pc - 1.0)
            if rets:
                daily_ret = np.mean(rets) - prev_cost
                nav *= (1 + daily_ret)
                prev_cost = 0.0  # 成本只扣一次
            else:
                daily_ret = 0.0
            # 记录每日
            rows.append({"date": tday, "period_return": daily_ret,
                         "nav": nav, "n": len(prev_holdings)})

        # 4. 每日风控: 止损 + 持有期 (用当日 close)
        if risk_check_daily and prev_holdings:
            current_prices = {}
            for code in prev_holdings:
                xc = row["close"].get(code)
                if xc is not None and xc > 0:
                    current_prices[code] = xc
            risk_sell = risk.update_holdings(prev_holdings, current_prices, tday)
            for code in risk_sell:
                if code in prev_holdings:
                    del prev_holdings[code]
                    del base_px[code]
                    pending_sell.add(code)
                    # 收益在次日 open 卖出时结算 (简化: 此处直接按 close 记一笔)
                    # 为对齐实盘"当日触发", 直接在此剔除, 不再持有
                    risk.register_exit(code)

        # 5. 回撤检查 (每日 or 仅换仓日)
        if risk_check_daily or tday in rebal_set:
            drawdown_triggered, dd = risk.check_drawdown(nav)
            if drawdown_triggered and prev_holdings:
                for code in list(prev_holdings.keys()):
                    risk.register_exit(code)
                prev_holdings, base_px = {}, {}
                pending_sell = set()
                prev_cost = 0.0

    risk.save_state()
    return pd.DataFrame(rows)


def summarize(result, base):
    result = result.set_index("date")
    years = (result.index[-1] - result.index[0]).days / 365.25
    cum = result["nav"].iloc[-1] - 1.0
    ann = result["nav"].iloc[-1] ** (1 / years) - 1
    lines = ["累计=%7.1f%% 年化=%6.1f%% 期数=%d" % (cum * 100, ann * 100, len(result))]
    for label, s, e in [("全期2018-2026", "2018-01-01", "2027-01-01"),
                        ("2026至今", "2026-01-01", "2027-01-01"),
                        ("2024+", "2024-01-01", "2027-01-01")]:
        segs = result[result.index >= pd.Timestamp(s)]
        segs = segs[segs.index < pd.Timestamp(e)]
        if len(segs) == 0:
            continue
        sr = segs["nav"].iloc[-1] / segs["nav"].iloc[0] - 1
        br, bn = R.nav_ret(base["ret"], s, e)
        if br is not None:
            lines.append("  %-16s 策略=%+7.1f%%  基准=%+7.1f%%  超额=%+7.1f%%  (n=%d/%d)" % (
                label, sr * 100, br * 100, (sr - br) * 100, len(segs), bn))
    return "\n".join(lines)


if __name__ == "__main__":
    base = R.run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    b_years = (base.index[-1] - base.index[0]).days / 365.25
    p("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100, ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100))

    p("\n============ 方向1: 逐日全口径回测 ============")
    p("\n--- 对照A: 风控仅换仓日 (应≈归档18%状态机口径, 验证结算引擎无偏) ---")
    rA = run_daily(risk_check_daily=False)
    p(summarize(rA, base))

    p("\n--- 对照B: 风控每日执行 (实盘对齐) ---")
    rB = run_daily(risk_check_daily=True)
    p(summarize(rB, base))

    p("\n总用时 %.0fs" % (time.time() - R.t0))
    out = os.path.join(RES, "dir1_daily_risk.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    p("结果已写入:", out)
