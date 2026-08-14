# coding=utf-8
"""Project 10: 价值小盘 V2 微调版 — 回测入口
状态机口径回测，含风控模块
用法: python runner.py"""
import sys, os, time
# 2026-08-06 迁移修正: E:\QuantLab -> D:\QuantLab。
# 注意: D:\QuantLab\strategy 是空占位包, 会抢占 `strategy.*` 解析,
# 故 Project_10 目录必须最后插入以保持 sys.path[0] 最高优先。
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # Project_10 优先

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
# 注意: trade_date 层为 datetime64[ns], 必须用 Timestamp 比较 (新版 pandas 不支持 date 对象)
start_ts = pd.Timestamp(START_DATE)
codes_all = set(
    idx.get_level_values("ts_code")[idx.get_level_values("trade_date") >= start_ts].unique()
)
daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
idx = daily.index
daily = daily.loc[
    (idx.get_level_values("trade_date") >= start_ts)
    & (idx.get_level_values("trade_date") <= pd.Timestamp(END_DATE))
].copy()
idx = daily.index

prev_close = daily["close"].groupby(level=1).shift(1)
panel = pd.DataFrame({
    "close": daily["close"].values, "open": daily["open"].values,
    "high": daily["high"].values, "low": daily["low"].values,
    "pe_ttm": daily["pe_ttm"].values, "pb": daily["pb"].values,
    "circ_mv": daily["circ_mv"].values, "amount": daily["amount"].values,
    "total_mv": daily["total_mv"].values,
    "prev_close": prev_close.values,
}, index=idx)

# 排除ST和停牌
is_st = daily["is_st"].astype(bool)
suspend = daily["suspend_type"].fillna("N")
panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]

# 行业映射
basic = pd.read_parquet(r"E:/astock/basic/stock_basic.parquet")
ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))

# 退市日期映射 (v2.3 退市排雷, 讨论室组件A)
_delist_map = {}
if "delist_date" in basic.columns:
    for _c, _v in zip(basic["ts_code"], basic["delist_date"]):
        if _v is None or (isinstance(_v, float) and np.isnan(_v)):
            continue
        _s = str(_v).strip().split(" ")[0]
        if _s in ("", "nan", "None", "NaT"):
            continue
        try:
            _delist_map[_c] = pd.to_datetime(_s, format="%Y%m%d")
        except Exception:
            pass

# v2.3 退市排雷参数 (讨论室组件A: 市值红线缓冲区 + 退市临近)
DELIST_MV_MAIN = 75000.0     # 主板总市值红线缓冲: 5亿 x 1.5 (万元)
DELIST_MV_GEMSTAR = 45000.0  # 创业板/科创板: 3亿 x 1.5 (万元)
DELIST_NEAR_DAYS = 30        # 距退市日 <= 30 天剔除 (北交所不适用市值红线)

def _delist_hit(code, d, total_mv):
    """退市排雷判断: True = 剔除"""
    dd = _delist_map.get(code)
    if dd is not None:
        try:
            if pd.Timestamp(d) >= dd - pd.Timedelta(days=DELIST_NEAR_DAYS):
                return True
        except Exception:
            pass
    if code.endswith(".BJ"):
        return False  # 北交所无对应市值退市标准, 不适用红线
    if total_mv is None or (isinstance(total_mv, float) and np.isnan(total_mv)) or total_mv <= 0:
        return False
    thr = DELIST_MV_GEMSTAR if (code.startswith("30") or code.startswith("688")) else DELIST_MV_MAIN
    return total_mv < thr

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
# 统一转 Timestamp, 避免 date 对象与 datetime64 索引不兼容 (新版 pandas)
rebal = [pd.Timestamp(x) for x in get_rebalance_dates(panel, freq=CFG["portfolio"]["rebalance_freq"])]

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
# 2026-08-14 修复: 回测必须非确定性零污染 —— 用独立 per-run state 文件,
# 且每次 run_backtest 前强制重置 nav_peak/持仓/禁入, 避免上次运行残留
# (曾因 risk_state.json 残留 nav_peak=12.70 导致年化虚高 18%->35.7%)。
state_dir = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(state_dir, exist_ok=True)
_risk_state_file = os.path.join(state_dir, "risk_state_run_%d.json" % os.getpid())
risk = RiskController(
    stop_loss=CFG["risk_control"]["stop_loss_pct"],
    max_drawdown=CFG["risk_control"]["max_drawdown_pct"],
    max_holding_days=CFG["risk_control"]["max_holding_days"],
    max_daily_turnover=CFG["risk_control"]["max_daily_turnover"],
    state_file=_risk_state_file,
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
    # v2.3 退市排雷 (讨论室组件A): 市值红线缓冲区 + 退市临近
    if ucfg.get("delist_screen", False):
        tmv = dd["total_mv"] if "total_mv" in dd else None
        mq = m.copy()
        for c in mq.index:
            if not mq[c]:
                continue
            v = tmv.get(c) if tmv is not None else None
            if _delist_hit(c, d, v):
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
def _reset_risk_state():
    """强制重置风控状态 (nav_peak=1.0, 清持仓/禁入), 保证回测确定性"""
    risk._state = {
        "holdings": {},
        "nav_peak": 1.0,
        "禁入列表": {},
        "dd_tier": 0,
    }

def run_backtest(force_full_turn=False, daily_risk=False):
    """状态机口径回测，含风控

    daily_risk=True: 每次换仓前, 用两换仓日之间的每个交易日 close 检查
    止损/持有期/回撤 (对齐实盘每 bar 检查), 触发则剔除。
    状态机收益结算仍按换仓日 open, 与实盘每 bar 口径有固有差异但可隔离风控频率贡献。

    ⚠️ 2026-08-14 已知缺陷（勿用于结论）: 每日止损剔除的股票直接移出
    prev_holdings, 换仓日结算时其收益不计入 → 止损亏损被抹掉, 该模式年化
    虚高 (17.8% 不可信)。正确逐日口径见 research/dir1_daily_risk.py
    (对照B = 逐日close结算+每日风控 = 7.5%, 实盘预期)。
    """
    _reset_risk_state()  # 2026-08-14: 每次回测强制重置, 消除运行历史污染
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

        # 1.5 每日风控检查 (方向1, 仅换仓日之间逐日检查, 用当日 close)
        if daily_risk and prev_holdings:
            nxt_rebal_idx = ti[pd.Timestamp(d)] + 1
            end_idx = ti[pd.Timestamp(rebal[i + 1])]
            if end_idx >= len(trade_dates):
                end_idx = len(trade_dates) - 1
            for k in range(e_idx, end_idx + 1):
                tdate = trade_dates[k]
                trow = panel.loc[tdate]
                current_prices = {}
                for code in prev_holdings:
                    xc = trow["close"].get(code)
                    if xc is not None and xc > 0:
                        current_prices[code] = xc
                if not current_prices:
                    continue
                risk_sell = risk.update_holdings(prev_holdings, current_prices, tdate)
                for code in risk_sell:
                    if code in prev_holdings:
                        del prev_holdings[code]
                        risk.register_exit(code)
                # 回撤检查: 用当日持仓 close 相对成本估算 nav
                if prev_holdings:
                    est_ret = np.mean([current_prices[c] / base - 1.0
                                       for c, base in prev_holdings.items() if c in current_prices]) \
                        if prev_holdings else 0.0
                    est_nav = nav * (1 + est_ret)
                    dd_trig, dd = risk.check_drawdown(est_nav)
                    if dd_trig:
                        for code in list(prev_holdings.keys()):
                            risk.register_exit(code)
                        prev_holdings = {}
                        prev_cost, prev_turnover, prev_sells, prev_buys = 0.0, 0.0, 0, 0
                        break

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

        # 5. buffer 排名（v2.3 讨论室组件B）
        #    排名按"剔除当期禁入股"后的候选计算（与 V2a target 构造一致）
        #    buffer_keep=0/缺省 => 全量重建（保留 rank<=n_stocks，即原行为，可复现存档）
        #    buffer_keep=160    => 保留 rank<=160 的持仓，降换手（已验证 +1.7pp 年化）
        order = score.sort_values(ascending=False)
        order_nb = [c for c in order.index if not risk.is_banned(c, e_date)]
        rank_nb = {c: r + 1 for r, c in enumerate(order_nb)}
        buffer_keep = int((CFG.get("rebalance", {}) or {}).get("buffer_keep", 0) or 0)
        keep_max = buffer_keep if buffer_keep > 0 else n_stocks
        buy_zone = order_nb[:n_stocks]

        # 6. 卖出超出 buffer 保留界的 + 涨跌停保护
        sells, buys = 0, 0
        new_holdings = {}
        for code, base in prev_holdings.items():
            rk = rank_nb.get(code)
            keep = (rk is not None and rk <= keep_max)
            if keep and not force_full_turn:
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

        # 7. 买入新标的（买入区 = top-n 非禁入，补到 n_stocks；涨停买不进留空位）
        for code in buy_zone:
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
    use_daily = "--daily-risk" in sys.argv
    if use_daily:
        p("[每日风控模式 daily_risk=True]")
    result = run_backtest(daily_risk=use_daily)
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
