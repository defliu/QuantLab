# coding=utf-8
"""补仓方案对比回测
方案一（基线）: 止损后空仓，等下次换仓日
方案二（<60%触发）: 持仓低于60%时补到80只
方案五（止损后立即补）: 每次止损后从候选池选1只补上
用法: python run_rebalance_comparison.py"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
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

# --------------- 补仓评分 ---------------
def replenish_score(d, dd_data, held_codes, n_needed, risk):
    """对候选池评分，返回top n_needed个不在held_codes中的标的"""
    cand = get_candidates(d, dd_data)
    if len(cand) < 10:
        return []
    score = scorer.score(d, cand, dd_data["pb"]).dropna()
    if len(score) == 0:
        return []
    result = []
    for code in score.sort_values(ascending=False).index:
        if len(result) >= n_needed:
            break
        if code not in held_codes and not risk.is_banned(code, d):
            result.append(code)
    return result

# --------------- 回测主循环 ---------------
def run_backtest(scheme="baseline"):
    """状态机口径回测
    scheme:
      'baseline'   = 方案一: 止损后不补仓，等下次换仓
      'threshold'  = 方案二: 持仓<60%触发补仓到80只
      'immediate'  = 方案五: 止损后立即补1只
    """
    rows = []
    prev_holdings = {}
    prev_cost = 0.0
    prev_turnover, prev_sells, prev_buys = 0.0, 0, 0
    nav = 1.0
    tx_cost = CFG["transaction"]["cost_rate"]
    limit_handling = CFG["transaction"]["limit_handling"]
    n_stocks = CFG["portfolio"]["n_stocks"]
    replenish_threshold = 0.6  # 方案二: 60%触发
    risk_state_file = os.path.join(os.path.dirname(__file__), "results",
                                   "risk_state_%s.json" % scheme)
    risk = RiskController(
        stop_loss=CFG["risk_control"]["stop_loss_pct"],
        max_drawdown=CFG["risk_control"]["max_drawdown_pct"],
        max_holding_days=CFG["risk_control"]["max_holding_days"],
        max_daily_turnover=CFG["risk_control"]["max_daily_turnover"],
        state_file=risk_state_file,
    )

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

        # ---- 补仓方案分叉 ----
        dd_data = panel.loc[d]
        n_risk_sold = len(risk_sell)

        if scheme == "baseline":
            # 方案一: 不补仓，等下次换仓日重建
            # 直接走到换仓选股（如果有的话），否则保持缩减组合
            pass

        elif scheme == "threshold":
            # 方案二: 持仓<60%时触发补仓到80只
            if len(prev_holdings) < n_stocks * replenish_threshold:
                n_needed = n_stocks - len(prev_holdings)
                replacements = replenish_score(d, dd_data, set(prev_holdings.keys()), n_needed, risk)
                for code in replacements:
                    xo = e_row["open"].get(code)
                    if xo is not None and xo > 0:
                        prev_holdings[code] = xo
                        risk.register_entry(code, xo, e_date)

        elif scheme == "immediate":
            # 方案五: 每次止损后立即补1只
            if n_risk_sold > 0 and len(prev_holdings) < n_stocks:
                # 逐个补仓
                for _ in range(n_risk_sold):
                    if len(prev_holdings) >= n_stocks:
                        break
                    replacements = replenish_score(d, dd_data, set(prev_holdings.keys()), 1, risk)
                    if replacements:
                        code = replacements[0]
                        xo = e_row["open"].get(code)
                        if xo is not None and xo > 0:
                            prev_holdings[code] = xo
                            risk.register_entry(code, xo, e_date)

        # 4. 选股 + 评分（换仓日重建目标组合）
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
            if code in target:
                new_holdings[code] = e_row["open"].get(code, base)
                risk.register_entry(code, new_holdings[code], e_date) if code not in risk.holdings else None
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


# --------------- 统计函数 ---------------
def calc_stats(result, base):
    """计算方案统计指标"""
    if len(result) == 0:
        return {}
    result = result.set_index("date") if "date" in result.columns else result
    cum = (1 + result["period_return"]).cumprod()
    total_ret = cum.iloc[-1] - 1.0
    years = (result.index[-1] - result.index[0]).days / 365.25
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    # 夏普（假设无风险利率2.5%）
    rf = 0.025
    mean_r = result["period_return"].mean()
    std_r = result["period_return"].std()
    # 2M调仓，年化因子
    periods_per_year = 6  # 12个月/2个月
    sharpe = (mean_r - rf / periods_per_year) / (std_r + 1e-9) * np.sqrt(periods_per_year)
    # 最大回撤
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = dd.min()
    # 超额
    if base is not None and len(base) > 0:
        seg, _ = nav_ret(result["period_return"], str(result.index[0].date()), str(result.index[-1].date()))
        br, _ = nav_ret(base["ret"], str(result.index[0].date()), str(result.index[-1].date()))
        excess = seg - br if seg is not None and br is not None else 0
    else:
        excess = 0
    # 平均持仓数和换手
    avg_n = result["n"].mean()
    avg_turn = result["turnover"].mean()
    return {
        "累计收益": total_ret,
        "年化收益": ann_ret,
        "夏普比率": sharpe,
        "最大回撤": max_dd,
        "超额收益": excess,
        "平均持仓数": avg_n,
        "平均换手率": avg_turn,
        "期数": len(result),
    }


# --------------- 主程序 ---------------
if __name__ == "__main__":
    log = []

    def p(*args):
        s = " ".join(str(a) for a in args)
        print(s)
        log.append(s)

    p("=" * 60)
    p("补仓方案对比回测")
    p("=" * 60)

    # 基准
    p("\n>>> 计算基准...")
    base = run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    b_years = (base.index[-1] - base.index[0]).days / 365.25
    b_ann = ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100
    p("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100, b_ann))

    schemes = [
        ("baseline", "方案一(不补仓/基线)"),
        ("threshold", "方案二(持仓<60%触发)"),
        ("immediate", "方案五(止损后立即补)"),
    ]

    results = {}
    for scheme_id, scheme_name in schemes:
        p("\n>>> 运行 %s ..." % scheme_name)
        t1 = time.time()
        result = run_backtest(scheme=scheme_id)
        elapsed = time.time() - t1
        p("  用时: %.0fs" % elapsed)

        if len(result) > 0:
            stats = calc_stats(result, base)
            results[scheme_id] = stats
            p("  累计=%7.1f%% 年化=%6.1f%% 夏普=%.2f 最大回撤=%6.1f%% 超额=%7.1f%% 平均持仓=%d 平均换手=%.2f" % (
                stats["累计收益"] * 100, stats["年化收益"] * 100, stats["夏普比率"],
                stats["最大回撤"] * 100, stats["超额收益"] * 100,
                stats["平均持仓数"], stats["平均换手率"]))
        else:
            results[scheme_id] = {}
            p("  无结果")

    # ---- 对比表 ----
    p("\n" + "=" * 80)
    p("对比总结")
    p("=" * 80)
    header = "%-28s %8s %8s %8s %8s %8s %8s" % (
        "方案", "累计收益", "年化收益", "夏普", "最大回撤", "超额收益", "平均换手")
    p(header)
    p("-" * 80)
    for scheme_id, scheme_name in schemes:
        s = results.get(scheme_id, {})
        if s:
            p("%-28s %+7.1f%% %+7.1f%% %8.2f %+7.1f%% %+7.1f%% %8.2f" % (
                scheme_name,
                s["累计收益"] * 100, s["年化收益"] * 100, s["夏普比率"],
                s["最大回撤"] * 100, s["超额收益"] * 100, s["平均换手率"]))
        else:
            p("%-28s  (无数据)" % scheme_name)

    # ---- 窗口超额 ----
    p("\n窗口超额对比:")
    windows = [("全期2018-2026", "2018-01-01", "2027-01-01"),
               ("2024+", "2024-01-01", "2027-01-01")]
    for label, ws, we in windows:
        p("  %s:" % label)
        for scheme_id, scheme_name in schemes:
            result = run_backtest(scheme=scheme_id) if scheme_id not in results else None
            # Use cached results
            break
        # Re-run once and cache properly
        break

    # 重新跑一遍获取窗口数据（复用上面的结果）
    scheme_results_cache = {}
    for scheme_id, scheme_name in schemes:
        result = run_backtest(scheme=scheme_id)
        if len(result) > 0:
            result = result.set_index("date") if "date" in result.columns else result
            scheme_results_cache[scheme_id] = result
        else:
            scheme_results_cache[scheme_id] = pd.DataFrame()

    for label, ws, we in windows:
        p("  %s:" % label)
        for scheme_id, scheme_name in schemes:
            sr = scheme_results_cache.get(scheme_id)
            if sr is not None and len(sr) > 0:
                s_ret, sn = nav_ret(sr["period_return"], ws, we)
                b_ret, bn = nav_ret(base["ret"], ws, we)
                if s_ret is not None and b_ret is not None:
                    p("    %-24s 策略=%+7.1f%%  基准=%+7.1f%%  超额=%+7.1f%%" % (
                        scheme_name, s_ret * 100, b_ret * 100, (s_ret - b_ret) * 100))

    # ---- 2026逐期明细（基线方案） ----
    p("\n2026逐期明细（方案一/基线）:")
    sr = scheme_results_cache.get("baseline")
    if sr is not None and len(sr) > 0:
        m26 = sr[sr.index >= "2026-01-01"]
        for d, r in m26.iterrows():
            p("  %s  收益=%+6.1f%%  nav=%6.2f  n=%3d  卖出=%3d 买入=%3d" % (
                d.strftime("%Y-%m-%d"), r["period_return"] * 100, r["nav"], r["n"], r["sells"], r["buys"]))

    p("\n总用时 %.0fs" % (time.time() - t0))

    # ---- 写入结果文件 ----
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)

    # 写文本日志
    out_path = os.path.join(results_dir, "rebalance_comparison.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    p("\n结果已写入:", out_path)

    # 写Markdown对比表
    md_path = os.path.join(results_dir, "rebalance_comparison.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 补仓方案对比回测\n\n")
        f.write("**回测时间**: 2019-01-01 ~ 2026-08-02\n")
        f.write("**调仓频率**: 双月 (2M)\n")
        f.write("**持仓数量**: 80只\n")
        f.write("**止损线**: 8%%\n")
        f.write("**最大回撤**: 15%%\n\n")

        f.write("## 方案说明\n\n")
        f.write("| 方案 | 描述 |\n")
        f.write("|------|------|\n")
        f.write("| 方案一(基线) | 止损后空仓，等下次换仓日(每2个月) |\n")
        f.write("| 方案二(<60%触发) | 持仓低于60%时触发补仓，补到80只 |\n")
        f.write("| 方案五(止损后立即补) | 每次止损后从候选池选1只补上 |\n\n")

        f.write("## 对比结果\n\n")
        f.write("| 指标 | 方案一(基线) | 方案二(<60%触发) | 方案五(止损后立即补) |\n")
        f.write("|------|-------------|-----------------|-------------------|\n")

        for metric, label, fmt in [
            ("累计收益", "累计收益", "%+.1f%%"),
            ("年化收益", "年化收益", "%+.1f%%"),
            ("夏普比率", "夏普比率", "%.2f"),
            ("最大回撤", "最大回撤", "%+.1f%%"),
            ("超额收益", "超额收益(全期)", "%+.1f%%"),
            ("平均持仓数", "平均持仓数", "%d"),
            ("平均换手率", "平均换手率", "%.2f"),
        ]:
            vals = []
            for sid in ["baseline", "threshold", "immediate"]:
                s = results.get(sid, {})
                v = s.get(metric, 0)
                if metric in ["累计收益", "年化收益", "最大回撤", "超额收益"]:
                    vals.append(fmt % (v * 100))
                elif metric == "平均持仓数":
                    vals.append(fmt % v)
                else:
                    vals.append(fmt % v)
            f.write("| %s | %s | %s | %s |\n" % (label, vals[0], vals[1], vals[2]))

        f.write("\n## 分析结论\n\n")
        f.write("（待回测完成后填写）\n")

    p("Markdown已写入:", md_path)
