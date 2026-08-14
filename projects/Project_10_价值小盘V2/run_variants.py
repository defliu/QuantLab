# coding=utf-8
"""Project_10 优化方向综合实验 (2026-08-14)

统一状态机口径 (与归档 V2a/buffer160 可比), 复用 runner 引擎的共享数据。
一次加载 panel, 跑多个方向变体。

用法: python run_variants.py
输出: results/directions_variants.txt
"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import runner as R

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
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
get_candidates = R.get_candidates


def reset_risk():
    risk._state = {"holdings": {}, "nav_peak": 1.0, "禁入列表": {}, "dd_tier": 0}


# ============ 基础状态机回测 (带变体参数) ============
def run_variant(tag, sector_cap=None, market_timing=False, weight_mode="equal",
                impact_sqrt=False, cap_size=None, soft_holding=False,
                rolling_weight=False, atr_wide=None, ind_map=None, amount_adv_pct=0.01):
    """统一变体引擎。状态机口径, buffer_keep=160。
    - sector_cap: 单行业持仓上限比例 (0.15)
    - market_timing: 全市场等权指数 MA200 门控, 空头时空仓
    - weight_mode: "equal" / "atr_inv" (1/ATR 反权) / "rank_inv" (排名反权)
    - impact_sqrt: 平方根冲击成本
    - cap_size: 资金规模(元), 触发容量约束
    - soft_holding: 60天持有期软化(评分落出buffer才卖)
    - rolling_weight: 滚动窗口因子权重 (方向2, 简化: 用历史IC加权)
    """
    reset_risk()
    rows = []
    prev_holdings = {}
    _weights = {}   # code -> 持仓权重 (等权时全 1.0)
    prev_cost = 0.0
    prev_turnover, prev_sells, prev_buys = 0.0, 0, 0
    nav = 1.0
    limit_handling_ = limit_handling
    n = n_stocks

    # 大盘代理指数 (全市场等权 close) + MA200
    idx_close = None
    if market_timing:
        idx_series = panel["close"].groupby(level=0).mean()
        idx_series = idx_series.sort_index()
        idx_ma = idx_series.rolling(200).mean()
        idx_close = pd.DataFrame({"close": idx_series, "ma": idx_ma})

    def timing_ok(tdate):
        """大盘门控: close > MA200 才允许持仓"""
        if idx_close is None:
            return True
        row = idx_close.loc[idx_close.index <= pd.Timestamp(tdate)].tail(1)
        if len(row) == 0:
            return True
        c = row["close"].iloc[0]
        m = row["ma"].iloc[0]
        return not np.isnan(m) and c > m

    for i in range(len(rebal) - 1):
        d = rebal[i]
        e_idx = ti[pd.Timestamp(d)] + 1
        if e_idx >= len(trade_dates):
            break
        e_date = trade_dates[e_idx]
        e_row = panel.loc[e_date]

        # 0. 大盘门控: 空头时空仓 (不持仓, 等待转多)
        if market_timing and not timing_ok(e_date):
            if prev_holdings:
                for code in list(prev_holdings.keys()):
                    risk.register_exit(code)
                prev_holdings = {}
                prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
            continue

        # 1. 结算上期持仓收益 (支持权重, 权重在 _weights dict 独立维护)
        if prev_holdings:
            rets, wts = [], []
            for code, base in prev_holdings.items():
                xo = e_row["open"].get(code)
                if xo is not None and xo > 0 and base is not None and base > 0:
                    rets.append(xo / base - 1.0)
                    wts.append(_weights.get(code, 1.0))
            if len(rets) > 0:
                period_ret = np.average(rets, weights=wts) - prev_cost
                nav *= (1 + period_ret)
                rows.append({"date": pd.Timestamp(e_date), "period_return": period_ret,
                             "nav": nav, "n": len(prev_holdings), "turnover": prev_turnover,
                             "sells": prev_sells, "buys": prev_buys})

        # 2. 回撤检查
        dd_trig, dd = risk.check_drawdown(nav)
        if dd_trig and prev_holdings:
            for code in list(prev_holdings.keys()):
                risk.register_exit(code)
            prev_holdings = {}
            prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
            continue

        # 3. 止损 + 持有期
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
        score = R.scorer.score(d, cand, dd_data["pb"]).dropna()
        if len(score) == 0:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue

        order = score.sort_values(ascending=False)
        order_nb = [c for c in order.index if not risk.is_banned(c, e_date)]
        rank_nb = {c: r + 1 for r, c in enumerate(order_nb)}
        buffer_keep = int((CFG.get("rebalance", {}) or {}).get("buffer_keep", 0) or 0)
        keep_max = buffer_keep if buffer_keep > 0 else n
        buy_zone = order_nb[:n]

        # 4.5 行业 cap: 限制单行业最多 floor(sector_cap*n) 只
        sector_limit = None
        if sector_cap and ind_map is not None:
            sector_limit = max(1, int(sector_cap * n))

        # 5. 卖出 + 权重计算
        sells, buys = 0, 0
        new_holdings = {}
        # 行业计数器
        sector_count = {}
        if sector_cap and ind_map is not None:
            for code in prev_holdings:
                ind = ind_map.get(code, "其他")
                sector_count[ind] = sector_count.get(ind, 0) + 1

        for code, base in prev_holdings.items():
            rk = rank_nb.get(code)
            keep = (rk is not None and rk <= keep_max)
            # 持有期软化: 超过60天但仍在buffer内则保留
            if keep:
                # 行业cap检查 (超限则卖)
                if sector_limit is not None:
                    ind = ind_map.get(code, "其他")
                    if sector_count.get(ind, 0) > sector_limit:
                        sector_count[ind] -= 1
                        sells += 1
                        risk.register_exit(code)
                        continue
                new_holdings[code] = e_row["open"].get(code, base)
                if code not in risk.holdings:
                    risk.register_entry(code, new_holdings[code], e_date)
                continue
            xo, xpc = e_row["open"].get(code), e_row["prev_close"].get(code)
            xl = e_row["low"].get(code)
            stuck = (limit_handling_ and xo is not None and xpc is not None and xpc > 0
                     and xl is not None and xo <= xpc * (1 - limit_pct(code) + 0.005)
                     and xo <= xl * 1.001)
            if stuck:
                new_holdings[code] = xo if xo is not None else base
                if sector_limit is not None:
                    ind = ind_map.get(code, "其他")
                    sector_count[ind] = sector_count.get(ind, 0) + 1
            else:
                sells += 1
                risk.register_exit(code)

        # 6. 买入 (含行业cap)
        for code in buy_zone:
            if len(new_holdings) >= n:
                break
            if code in new_holdings:
                continue
            if sector_limit is not None:
                ind = ind_map.get(code, "其他")
                if sector_count.get(ind, 0) >= sector_limit:
                    continue
            eo, epc = e_row["open"].get(code), e_row["prev_close"].get(code)
            eh = e_row["high"].get(code)
            buyable = (not limit_handling_ or eo is None or epc is None or epc <= 0 or eh is None
                       or not (eo >= epc * (1 + limit_pct(code) - 0.005) and eo >= eh * 0.999))
            if buyable and eo is not None and eo > 0:
                new_holdings[code] = eo
                risk.register_entry(code, eo, e_date)
                buys += 1
                if sector_limit is not None:
                    ind = ind_map.get(code, "其他")
                    sector_count[ind] = sector_count.get(ind, 0) + 1

        # 7. 容量约束 (方向11): 检查每只股票成交额能否容纳 target 金额
        if cap_size is not None and new_holdings:
            per_stock = cap_size / max(1, len(new_holdings))  # 元
            keep = {}
            for code, base in new_holdings.items():
                amt = e_row["amount"].get(code)
                if amt is not None and amt > 0:
                    # amount 单位: 千元 (E:/astock daily), 转元 = amt*1000
                    # 容量约束: 单票金额 <= 当日成交额 * amount_adv_pct
                    if per_stock > amount_adv_pct * amt * 1000:
                        risk.register_exit(code)
                        continue
                keep[code] = base
            new_holdings = keep

        # 8. 冲击成本 (方向10): 调仓额外成本 = k*sqrt(成交额/日成交额)
        impact = 0.0
        if impact_sqrt and new_holdings:
            for code in new_holdings:
                amt = e_row["amount"].get(code)
                if amt is not None and amt > 0:
                    turnover_ratio = (sells + buys) / max(1, len(new_holdings))
                    impact += 0.001 * np.sqrt(turnover_ratio) * (1.0 / len(new_holdings))

        # 8.5 权重计算 (方向4: ATR 反权; 默认等权)
        if new_holdings:
            if weight_mode == "atr_inv" and atr_wide is not None:
                try:
                    if e_date in atr_wide.index:
                        arow = atr_wide.loc[e_date]
                        vals = {}
                        for code in new_holdings:
                            v = arow.get(code)
                            if v is not None and v > 0:
                                vals[code] = 1.0 / v
                        s = sum(vals.values())
                        if s > 0:
                            _weights = {c: v / s for c, v in vals.items()}
                        else:
                            _weights = {c: 1.0 for c in new_holdings}
                    else:
                        _weights = {c: 1.0 for c in new_holdings}
                except Exception:
                    _weights = {c: 1.0 for c in new_holdings}
            elif weight_mode == "rank_inv":
                # 排名反权: 权重随排名线性递减
                nw = len(new_holdings)
                _weights = {c: (nw - rk) / (nw * (nw + 1) / 2)
                            for c, rk in rank_nb.items() if c in new_holdings}
            else:
                _weights = {c: 1.0 for c in new_holdings}

        prev_holdings = new_holdings
        prev_turnover = (sells + buys) / float(n)
        prev_cost = prev_turnover * tx_cost + impact
        prev_sells, prev_buys = sells, buys

    risk.save_state()
    return pd.DataFrame(rows)


def summarize(result, base):
    if len(result) == 0:
        return "无结果"
    result = result.set_index("date")
    cum = (1 + result["period_return"]).cumprod()
    total = cum.iloc[-1] - 1.0
    years = (result.index[-1] - result.index[0]).days / 365.25
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else 0
    lines = ["累计=%7.1f%% 年化=%6.1f%% 期数=%d 换手=%.2f" % (
        total * 100, ann * 100, len(result), result["turnover"].mean())]
    for label, s, e in [("全期", "2018-01-01", "2027-01-01"),
                        ("2024+", "2024-01-01", "2027-01-01"),
                        ("2026", "2026-01-01", "2027-01-01")]:
        sr, _ = R.nav_ret(result["period_return"], s, e)
        br, _ = R.nav_ret(base["ret"], s, e)
        if sr is not None and br is not None:
            lines.append("  超额%-6s=%+7.1f%%" % (label, (sr - br) * 100))
    return "\n".join(lines)


if __name__ == "__main__":
    base = R.run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    p("基准: 累计=%6.1f%%" % (b_cum.iloc[-1] * 100))

    basic = pd.read_parquet(r"E:/astock/basic/stock_basic.parquet")
    ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))

    # ATR% 宽表 (方向4)
    from strategy.factors import ATRFactor
    atr_wide = None
    try:
        atr_wide = ATRFactor().atr_pct_wide(panel)
    except Exception as e:
        p("ATR 宽表计算失败:", e)

    variants = [
        ("基线(buffer160)", dict()),
        ("方向4 ATR反权", dict(weight_mode="atr_inv", atr_wide=atr_wide)),
        ("方向5 大盘MA200门控", dict(market_timing=True)),
        ("方向7 行业cap15%", dict(sector_cap=0.15, ind_map=ind_map)),
        ("方向10 平方根冲击成本", dict(impact_sqrt=True)),
        ("方向11 容量1000万", dict(cap_size=10000000, amount_adv_pct=0.02)),
        ("方向11 容量5000万", dict(cap_size=50000000, amount_adv_pct=0.02)),
        ("方向11 容量1亿", dict(cap_size=100000000, amount_adv_pct=0.02)),
        ("方向11 容量5亿", dict(cap_size=500000000, amount_adv_pct=0.02)),
    ]

    p("\n============ 方向变体 ============")
    results = {}
    for name, kw in variants:
        t1 = time.time()
        try:
            res = run_variant(name, **kw)
            st = summarize(res, base)
            p("\n[%s]  (%.0fs)\n%s" % (name, time.time() - t1, st))
            results[name] = st
        except Exception as e:
            import traceback
            p("[%s] 失败: %s" % (name, e))
            p(traceback.format_exc()[-2000:])

    out = os.path.join(RES, "directions_variants.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    p("\n结果已写入:", out)
