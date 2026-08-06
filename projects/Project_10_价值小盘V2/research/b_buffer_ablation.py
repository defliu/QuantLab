# coding=utf-8
"""组件 B: buffer 降换手消融 —— V2a(80只) 全量重建 vs buffer 保留
讨论室 Round 1 裁决执行 (2026-08-06, 诚哥批准 C->A->B)

设计要点 (与 SPEC v2.2 5.2 的衔接):
  原 buffer 30/40 是为 20 只组合设计 (持有<=1.5x n, 卖出>2x n)。V2a 为 80 只,
  直接套 30/40 会因 keep_max<n 反而加换手, 故按组合规模等比缩放:
    全量重建 (基线)   keep_max = 1.0 x n = 80   (等价现状: 只保留 rank<=80)
    buffer 1.5x      keep_max = 120
    buffer 2.0x      keep_max = 160
    buffer 2.5x      keep_max = 200
  语义: 现持仓按当期全候选 score 排名, rank<=keep_max 保留, rank>keep_max
  或落选候选才卖; 空位用排名最高的未持有股补到 80 只。

口径 (与 V2a 一致):
  评分 = V2a 纯BP z1.0;  风控 = R0 断路器(15%一刀清仓)+8%止损+60天持有;
  其余参数冻结 (80只/双月/成本千一/涨跌停保护)。

否决性指标 (讨论室设计): 2024+ 净超额若劣于基线, 即使全期占优也判不通过。

输出: results/b_buffer_ablation.txt
"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_grid_validation as rgv
import pandas as pd
import numpy as np

RES = os.path.join(rgv.HERE, "results")
os.makedirs(RES, exist_ok=True)

CFG = rgv.CFG
panel, ti, trade_dates, rebal = rgv.panel, rgv.ti, rgv.trade_dates, rgv.rebal
tx_cost = CFG["transaction"]["cost_rate"]
limit_handling = CFG["transaction"]["limit_handling"]
n_stocks = CFG["portfolio"]["n_stocks"]
limit_pct = rgv.limit_pct

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)


def run_buffer(scorer, risk, keep_mult):
    """buffer 回测。keep_mult: 保留排名上限 = keep_mult x n_stocks。
    keep_mult=1.0 时等价全量重建(基线)。"""
    keep_max = int(round(keep_mult * n_stocks))
    rows = []
    prev_holdings = {}
    prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
    nav = 1.0

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
                rows.append({"date": pd.Timestamp(e_date), "period_return": period_ret,
                             "nav": nav, "n": len(prev_holdings),
                             "turnover": prev_turnover, "sells": prev_sells, "buys": prev_buys})

        # 2. 断路器 (R0 一刀清仓)
        triggered, dd = risk.check_drawdown(nav)
        if triggered and prev_holdings:
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

        # 4. 评分 (全候选排名)
        dd_data = panel.loc[d]
        cand = rgv.get_candidates(d, dd_data)
        if len(cand) < 10:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue
        score = scorer.score(d, cand, dd_data["pb"], pe_series=dd_data["pe_ttm"])
        score = score.dropna() if score is not None else None
        if score is None or len(score) == 0:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue
        order = score.sort_values(ascending=False)
        # 排名按"剔除当期禁入股"后的候选计算 (与 V2a target 构造一致),
        # 否则禁入股挤位会使 keep_max=1.0x 基线偏离 V2a 存档
        order_nb = [c for c in order.index if not risk.is_banned(c, e_date)]
        rank_nb = {c: r + 1 for r, c in enumerate(order_nb)}

        # 5. buffer 保留: rank_nb<=keep_max -> 保留; 否则卖
        sells, buys = 0, 0
        new_holdings = {}
        for code, base in prev_holdings.items():
            keep = (rank_nb.get(code) is not None and rank_nb[code] <= keep_max)
            if keep:
                new_holdings[code] = e_row["open"].get(code, base)
                if code not in risk.holdings:
                    risk.register_entry(code, new_holdings[code], e_date)
                continue
            xo, xpc = e_row["open"].get(code), e_row["prev_close"].get(code)
            xl = e_row["low"].get(code)
            stuck = (limit_handling and xo is not None and xpc is not None and xpc > 0
                     and xl is not None and xo <= xpc * (1 - limit_pct(code) + 0.005)
                     and xo <= xl * 1.001)
            if stuck:
                new_holdings[code] = xo if xo is not None else base
            else:
                sells += 1
                risk.register_exit(code)

        # 6. 空位补到 n_stocks: 仅在买入区(top n 非禁入)内选最高分未持有股;
        #    买入区内涨停买不进则留空位(不向 rank>n 补买), 与 V2a target 构造一致
        for code in order_nb[:n_stocks]:
            if len(new_holdings) >= n_stocks:
                break
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


p("============ 组件B: buffer 降换手消融 (V2a, 风控=R0断路器) ============")
p("运行时间:", time.strftime("%Y-%m-%d %H:%M:%S"))
base = rgv.run_base()
scorer = rgv.make_scorer("bp", 1.0, 0.0)  # V2a 纯BP

variants = [
    ("全量重建(基线)", 1.0),
    ("buffer 1.5x(120)", 1.5),
    ("buffer 2.0x(160)", 2.0),
    ("buffer 2.5x(200)", 2.5),
]

p("\nvariant              年化    回撤     换手   超额[全期     2024+     2026]   (用时)")
summary = {}
for name, km in variants:
    t1 = time.time()
    risk = rgv.make_risk("b_km%3.1f" % km, atr_stop=False, tiered=False)
    res = run_buffer(scorer, risk, km)
    res = res.set_index("date") if "date" in res.columns else res
    st = rgv.summarize(res, base)
    if st is None:
        p("%-20s 无结果" % name)
        continue
    summary[name] = st
    p("%-20s %+6.1f%% %+7.1f%%  %.2f   %+8.1f%% %+7.1f%% %+6.1f%%   (%.0fs)" % (
        name, st["年化"] * 100, st["最大回撤"] * 100, st["平均换手"],
        (st["超额全期"] or 0) * 100, (st["超额2024+"] or 0) * 100,
        (st["超额2026"] or 0) * 100, time.time() - t1))

# 自检: 基线应复现 grid_validation.txt V2a (年化16.2%/回撤-29.7%/超额全期200.9%/换手0.91)
p("\n自检判据: 全量重建基线应≈ V2a存档 (年化+16.2% 回撤-29.7% 超额全期+200.9% 换手0.91)")

# 否决性判据: 2024+ 劣于基线即不通过
b0 = summary.get("全量重建(基线)", {}).get("超额2024+")
if b0 is not None:
    p("\n否决性判据 (2024+ 不得劣于基线 %+.1f%%):" % (b0 * 100))
    for name, st in summary.items():
        if name == "全量重建(基线)":
            continue
        e24 = st["超额2024+"]
        verdict = "通过" if (e24 is not None and e24 >= b0 - 1e-9) else "不通过(2024+劣化)"
        p("  %-20s 2024+=%+.1f%%  -> %s" % (name, (e24 or 0) * 100, verdict))

out_path = os.path.join(RES, "b_buffer_ablation.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("结果已写入:", out_path)

for fn in os.listdir(RES):
    if fn.startswith("grid_state_b_") and fn.endswith(".json"):
        os.remove(os.path.join(RES, fn))
print("临时状态文件已清理")
