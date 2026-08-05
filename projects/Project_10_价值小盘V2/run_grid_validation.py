# coding=utf-8
"""Project_10 因子层+风控层 网格验证 (P0-1 / P0-2 / P1-1, 2026-08-04)

依据:
  - specs/CodexQT-2026-0803_P0-1_因子层重构.md   (hp 取舍 + EP 替代)
  - specs/CodexQT-2026-0803_P0-2_因子层扩展.md   (ATR%/换手率 叠加)
  - specs/CodexQT-2026-0803_P1-1_风控组合重构.md (ATR止损 + 分层降仓 + 信号补仓)

评分网格 (风控=基线):
  V1  基线: BP_z 0.8 + hp 0.2
  V2a 纯BP z 1.0 (audit17 最优)      V2b 纯EP z (Wharton)
  V2c BP 0.5 + EP 0.5                V2d BP 0.7 + EP 0.3
  V3a BP 0.6 + ATR 0.3 + 换手 0.1    V3b BP 0.5 + ATR 0.3 + 换手 0.2
  V3c BP 0.6 + ATR 0.4               V3d BP 0.7 + ATR 0.2 + 换手 0.1
风控网格 (评分=V1 基线):
  R0  基线: 8%固定止损 + 15%一刀清仓 + 双月全量
  R1  ATR×2 自适应止损               R2  分层降仓
  R3  ATR + 分层 + signal<60%补仓    R4  ATR + 分层 + 双月全量

用法: python run_grid_validation.py [--quick 只跑V1/R0]
"""
import sys, os, time
# 插入顺序注意: Project_01 必须在 Project_10 之前插入,
# 保证 sys.path[0]=Project_10, `strategy.*` 解析到本项目模块
sys.path.insert(0, r"E:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"E:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml
import pandas as pd
import numpy as np
from research.multi_factor_ic.config import DAILY_PATH, START_DATE, END_DATE
from research.multi_factor_ic.data_loader import get_rebalance_dates

from strategy.scoring import V2Scorer
from strategy.risk import RiskController
from strategy.rebalance import SignalRebalancer
from strategy.factors import ATRFactor, TurnoverFactor, build_factor_frames

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config", "strategy.yaml")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

QUICK = "--quick" in sys.argv

# --------------- 数据加载 ---------------
t0 = time.time()
daily = pd.read_parquet(DAILY_PATH)
idx = daily.index
# 注意: trade_date 层为 datetime64[ns], 必须用 Timestamp 比较 (新版 pandas 不支持 date 对象)
start_ts = pd.Timestamp(START_DATE)
end_ts = pd.Timestamp(END_DATE)
codes_all = set(
    idx.get_level_values("ts_code")[idx.get_level_values("trade_date") >= start_ts].unique()
)
daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
idx = daily.index
daily = daily.loc[
    (idx.get_level_values("trade_date") >= start_ts)
    & (idx.get_level_values("trade_date") <= end_ts)
].copy()
idx = daily.index

prev_close = daily["close"].groupby(level=1).shift(1)
panel = pd.DataFrame({
    "close": daily["close"].values, "open": daily["open"].values,
    "high": daily["high"].values, "low": daily["low"].values,
    "pe_ttm": daily["pe_ttm"].values, "pb": daily["pb"].values,
    "circ_mv": daily["circ_mv"].values, "amount": daily["amount"].values,
    "turnover_rate": daily["turnover_rate"].values,
    "prev_close": prev_close.values,
}, index=idx)

is_st = daily["is_st"].astype(bool)
suspend = daily["suspend_type"].fillna("N")
panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]

basic = pd.read_parquet(r"E:/astock/basic/stock_basic.parquet")
ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))

fin = pd.read_parquet(r"E:/astock/finance/fina_indicator.parquet")
fin = fin[["ts_code", "end_date", "ann_date", "bps", "roe", "profit_dedt", "debt_to_assets"]].copy()
fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
fin = fin.dropna(subset=["ann_date"])
fin = fin[fin["ts_code"].isin(codes_all)]
fin = fin.sort_values(["ts_code", "ann_date"])
fin_by_code = {c: g for c, g in fin.groupby("ts_code")}
print("panel:", panel.shape, " 用时: %.1fs" % (time.time() - t0))

# --------------- 工具 ---------------
trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}
# 统一转 Timestamp, 避免 date 对象与 datetime64 索引不兼容
rebal = [pd.Timestamp(x) for x in get_rebalance_dates(panel, freq=CFG["portfolio"]["rebalance_freq"])]

pb_wide = panel["pb"].unstack("ts_code")
pb_wide.index = pd.DatetimeIndex(pb_wide.index)

# ATR% 原始宽表 (风控 ATR 止损用)
print("计算 ATR% 宽表...", flush=True)
atr_raw = ATRFactor().atr_pct_wide(panel)

# 因子得分帧 (P0-2)
print("计算因子得分帧...", flush=True)
t1 = time.time()
factor_frames = build_factor_frames(panel)
print("因子帧完成: %.1fs" % (time.time() - t1), flush=True)

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

def limit_pct(code):
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10

# --------------- 评分器 / 风控工厂 ---------------
def make_scorer(method, z_w, hp_w, ep_w=0.0, factor_weights=None):
    s = V2Scorer(
        ind_map=ind_map, z_weight=z_w, hp_weight=hp_w,
        hp_window=CFG["scoring"]["hp_window_months"],
        hp_min=CFG["scoring"]["hp_min_months"],
        method=method, ep_weight=ep_w, factor_weights=factor_weights,
    )
    s.compute_bp_monthly(pb_wide)
    for name, frame in factor_frames.items():
        s.attach_factor(name, frame)
    return s

def make_risk(tag, atr_stop=False, tiered=False):
    state_file = os.path.join(HERE, "results", "grid_state_%s.json" % tag)
    if os.path.exists(state_file):
        os.remove(state_file)  # 干净状态, 避免旧 nav_peak 污染
    rcfg = CFG["risk_control"]
    acfg = rcfg["atr_stop"]
    tcfg = rcfg["tiered_drawdown"]
    return RiskController(
        stop_loss=rcfg["stop_loss_pct"],
        max_drawdown=rcfg["max_drawdown_pct"],
        max_holding_days=rcfg["max_holding_days"],
        max_daily_turnover=rcfg["max_daily_turnover"],
        state_file=state_file,
        atr_stop=atr_stop,
        atr_multiplier=acfg["multiplier"],
        atr_stop_cap=acfg["stop_cap"],
        tiered_drawdown=tiered,
        tiered_thresholds=tuple(tcfg["thresholds"]),
        tiered_targets=tuple(tcfg["targets"]),
    )

rb = SignalRebalancer(n_target=CFG["portfolio"]["n_stocks"],
                      threshold=CFG.get("rebalance", {}).get("signal_threshold", 0.6))

# --------------- 通用回测循环 ---------------
def run_variant(scorer, risk, signal=False):
    """状态机口径回测。signal=True 时持仓<阈值触发补仓 (与方案二同口径)"""
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
                    "date": pd.Timestamp(e_date), "period_return": period_ret,
                    "nav": nav, "n": len(prev_holdings),
                    "turnover": prev_turnover, "sells": prev_sells, "buys": prev_buys,
                })

        dd_data = panel.loc[d]
        _score_holder = {}
        def get_score():
            if "s" not in _score_holder:
                cand = get_candidates(d, dd_data)
                if len(cand) < 10:
                    _score_holder["s"] = None
                else:
                    s = scorer.score(d, cand, dd_data["pb"], pe_series=dd_data["pe_ttm"])
                    _score_holder["s"] = s.dropna() if s is not None else None
            return _score_holder["s"]

        # 2. 组合回撤检查 (分层 或 一刀切)
        tier_sells = 0
        if risk.tiered_drawdown:
            action, dd = risk.check_drawdown_tiered(nav)
            if action == "clear" and prev_holdings:
                for code in list(prev_holdings.keys()):
                    risk.register_exit(code)
                prev_holdings = {}
                prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
                continue
            if action == "reduce" and prev_holdings:
                target_n = int(n_stocks * (risk.last_tier_target or 0.0))
                score = get_score()
                def _sc(c):
                    if score is None:
                        return np.nan
                    return score.get(c, np.nan)
                order = sorted(prev_holdings.keys(), key=lambda c: (np.nan_to_num(_sc(c), nan=-1e9)))
                n_sell = max(0, len(prev_holdings) - target_n)
                for code in order[:n_sell]:
                    del prev_holdings[code]
                    risk.register_exit(code)
                    tier_sells += 1
        else:
            triggered, dd = risk.check_drawdown(nav)
            if triggered and prev_holdings:
                for code in list(prev_holdings.keys()):
                    risk.register_exit(code)
                prev_holdings = {}
                prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
                continue

        # 3. 风控: 止损(ATR自适应/固定) + 持有期
        current_prices = {}
        for code in prev_holdings:
            xo = e_row["open"].get(code)
            if xo is not None and xo > 0:
                current_prices[code] = xo
        atr_map = None
        if risk.atr_stop and len(prev_holdings) > 0:
            if e_date in atr_raw.index:
                arow = atr_raw.loc[e_date]
                atr_map = {c: arow.get(c) for c in prev_holdings}
        risk_sell = risk.update_holdings(prev_holdings, current_prices, e_date, atr_pct=atr_map)
        for code in risk_sell:
            if code in prev_holdings:
                del prev_holdings[code]
                risk.register_exit(code)

        # 4. signal 模式: 持仓<阈值触发补仓 (同 run_rebalance_comparison 方案二口径)
        if signal and rb.should_replenish(len(prev_holdings)):
            score = get_score()
            if score is not None and len(score) > 0:
                picks = rb.pick(score, set(prev_holdings.keys()),
                                lambda c: risk.is_banned(c, e_date), e_date)
                for code in picks:
                    xo = e_row["open"].get(code)
                    if xo is not None and xo > 0:
                        prev_holdings[code] = xo
                        risk.register_entry(code, xo, e_date)

        # 5. 选股 + 评分 (换仓日重建目标组合)
        score = get_score()
        if score is None or len(score) == 0:
            prev_holdings, prev_cost, prev_turnover, prev_sells, prev_buys = {}, 0.0, 0.0, 0, 0
            continue

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
        prev_turnover = (sells + buys + tier_sells) / float(n_stocks)
        prev_cost = prev_turnover * tx_cost
        prev_sells, prev_buys = sells + tier_sells, buys

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

def nav_ret(s, start, end):
    seg = s[(s.index >= start) & (s.index < end)]
    if len(seg) == 0:
        return None, 0
    return (1 + seg).prod() - 1, len(seg)

def summarize(result, base):
    if len(result) == 0:
        return None
    result = result.set_index("date") if "date" in result.columns else result
    cum = (1 + result["period_return"]).cumprod()
    total = cum.iloc[-1] - 1.0
    years = (result.index[-1] - result.index[0]).days / 365.25
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else 0
    peak = cum.cummax()
    max_dd = ((cum - peak) / peak).min()
    rf = 0.025
    sharpe = ((result["period_return"].mean() - rf / 6)
              / (result["period_return"].std() + 1e-9) * np.sqrt(6))
    win = {}
    for label, s, e in [("全期", "2018-01-01", "2027-01-01"),
                        ("2024+", "2024-01-01", "2027-01-01"),
                        ("2026", "2026-01-01", "2027-01-01")]:
        sr, _ = nav_ret(result["period_return"], s, e)
        br, _ = nav_ret(base["ret"], s, e)
        win[label] = (sr - br) if (sr is not None and br is not None) else None
    return {
        "累计": total, "年化": ann, "夏普": sharpe, "最大回撤": max_dd,
        "超额全期": win["全期"], "超额2024+": win["2024+"], "超额2026": win["2026"],
        "平均换手": result["turnover"].mean(), "期数": len(result),
        "_result": result,
    }

# --------------- 主程序 ---------------
if __name__ == "__main__":
    log = []
    def p(*args):
        s = " ".join(str(a) for a in args)
        print(s, flush=True)
        log.append(s)

    p("============ 基准 ============")
    base = run_base()
    b_cum = (1 + base["ret"]).cumprod() - 1
    b_years = (base.index[-1] - base.index[0]).days / 365.25
    p("基准: 累计=%6.1f%% 年化=%6.1f%%" % (b_cum.iloc[-1] * 100,
                                          ((1 + b_cum.iloc[-1]) ** (1 / b_years) - 1) * 100))

    # ---- 评分网格 (风控=基线) ----
    scoring_variants = [
        ("V1 基线 0.8z+0.2hp", make_scorer("bp", 0.8, 0.2), False),
        ("V2a 纯BPz1.0",       make_scorer("bp", 1.0, 0.0), False),
        ("V2b 纯EPz",          make_scorer("ep", 0.0, 0.0, ep_w=1.0), False),
        ("V2c BP0.5+EP0.5",    make_scorer("bp_ep", 0.5, 0.0, ep_w=0.5), False),
        ("V2d BP0.7+EP0.3",    make_scorer("bp_ep", 0.7, 0.0, ep_w=0.3), False),
        ("V3a BP.6+ATR.3+T.1", make_scorer("multi", 0.6, 0.0, factor_weights={"atr_rank": 0.3, "turnover_rank": 0.1}), False),
        ("V3b BP.5+ATR.3+T.2", make_scorer("multi", 0.5, 0.0, factor_weights={"atr_rank": 0.3, "turnover_rank": 0.2}), False),
        ("V3c BP.6+ATR.4",     make_scorer("multi", 0.6, 0.0, factor_weights={"atr_rank": 0.4}), False),
        ("V3d BP.7+ATR.2+T.1", make_scorer("multi", 0.7, 0.0, factor_weights={"atr_rank": 0.2, "turnover_rank": 0.1}), False),
    ]
    if QUICK:
        scoring_variants = scoring_variants[:1]

    all_stats = []
    p("\n============ 评分网格 (P0-1/P0-2, 风控=基线) ============")
    best_2026 = None
    for name, sc, sig in scoring_variants:
        t1 = time.time()
        res = run_variant(sc, make_risk("s_" + name[:3]), signal=sig)
        st = summarize(res, base)
        if st is None:
            p("%-22s 无结果" % name)
            continue
        p("%-22s 年化=%+6.1f%% 回撤=%+6.1f%% 超额[全期=%+7.1f%% 2024+=%+6.1f%% 2026=%+6.1f%%] 换手=%.2f (%.0fs)" % (
            name, st["年化"] * 100, st["最大回撤"] * 100,
            (st["超额全期"] or 0) * 100, (st["超额2024+"] or 0) * 100, (st["超额2026"] or 0) * 100,
            st["平均换手"], time.time() - t1))
        all_stats.append((name, st))
        if best_2026 is None or (st["超额2026"] or -9) > (best_2026[1]["超额2026"] or -9):
            best_2026 = (name, st)

    # ---- 风控网格 (评分=V1基线) ----
    risk_variants = [
        ("R0 基线",           False, False, False),
        ("R1 ATR止损",        True,  False, False),
        ("R2 分层降仓",       False, True,  False),
        ("R3 ATR+分层+信号",  True,  True,  True),
        ("R4 ATR+分层",       True,  True,  False),
    ]
    if QUICK:
        risk_variants = risk_variants[:1]

    p("\n============ 风控网格 (P1-1, 评分=V1基线) ============")
    v1_scorer = make_scorer("bp", 0.8, 0.2)
    for name, astop, tier, sig in risk_variants:
        t1 = time.time()
        res = run_variant(v1_scorer, make_risk("r_" + name[:3], atr_stop=astop, tiered=tier), signal=sig)
        st = summarize(res, base)
        if st is None:
            p("%-22s 无结果" % name)
            continue
        p("%-22s 年化=%+6.1f%% 回撤=%+6.1f%% 超额[全期=%+7.1f%% 2024+=%+6.1f%% 2026=%+6.1f%%] 换手=%.2f (%.0fs)" % (
            name, st["年化"] * 100, st["最大回撤"] * 100,
            (st["超额全期"] or 0) * 100, (st["超额2024+"] or 0) * 100, (st["超额2026"] or 0) * 100,
            st["平均换手"], time.time() - t1))
        all_stats.append((name, st))

    # ---- 2026 逐期明细: V1 基线 + 2026 超额最佳 ----
    for tag, (name, st) in [("基线", all_stats[0]), ("2026最佳", best_2026)] if not QUICK else []:
        p("\n============ 2026 逐期明细 [%s: %s] ============" % (tag, name))
        m26 = st["_result"][st["_result"].index >= "2026-01-01"]
        for d, r in m26.iterrows():
            p("  %s  收益=%+6.1f%%  nav=%6.2f  n=%3d" % (
                d.strftime("%Y-%m-%d"), r["period_return"] * 100, r["nav"], r["n"]))

    p("\n总用时 %.0fs" % (time.time() - t0))
    out_path = os.path.join(HERE, "results", "grid_validation.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("结果已写入:", out_path)
