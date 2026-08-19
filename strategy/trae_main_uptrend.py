# coding: utf-8
"""Trae 主升浪启动前/中段选股策略 —— QuantLab 框架原生版（事件驱动增量模式）。

选股：每日扫描全 A，命中"启动前·起爆前夜"或"主升浪中段"任一模式即入选，
      池内按 20 日 ATR% 升序取前 n_hold 只（实证：低波排序在低信号密度策略中
      有效，对齐 ATR 部署 max_exclude_pct=0.20 的精神）。信号由
      gen_trae_uptrend_signal_table.py 预生成，经 aux_data["trae_uptrend_signals"]
      注入。
持仓：事件驱动滚动——信号日买入（增量 buy_candidates），持有至止损/到期卖出
      （增量 sell_decisions），不使用 target_weights diff。
风控：个股止损 stop_loss（默认 -12%）；最长持有 max_holding_days（默认 60 天）；
      MA200 大盘门控 market_gate（0=关，1=开；exit=跌破清仓，hold=只挡新买）。

config（strategy_params）示例：
  n_hold: 8
  max_holding_days: 60
  stop_loss: -0.12
  market_gate: 0
  ma_window: 200
  gate_mode: exit
  signal_window: 1          # 候选信号有效期（交易日数）：1=只当日信号；
                            # >1=含最近 N 个信号日的信号，用于门控恢复日快速回补
  max_single_pct: 0.125     # 单票仓位上限
  trailing_stop: None       # 移动止损（None=关；0.15=收盘 ≤ 持有期最高收盘×0.85 触发）
"""
from strategy.registry import register_strategy

ALLOWED_TRADING_MODELS = ["next_open"]


def _noop_decision(date, logs, diag):
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": diag,
        "logs": logs,
    }


def _peak_close_since_entry(market_window, code, entry_date, last):
    """计算入场后（含入场日）的历史最高收盘价，用于移动止损（PIT 安全）。

    引擎语义：决策日 T 的 market_window 只含至 T-1 的数据（决策基于 T-1 收盘，
    次一交易日开盘执行），而 position.last_price 已被引擎 mark 为 T 收盘。
    因此「持有期最高收盘」= max(窗口内 entry_date 起的收盘, 今日收盘 last)；
    若 entry_date 未达（当日刚买入，窗口尚无入场日 bar），仅用 last。
    """
    peak = float(last)
    mw = (market_window or {}).get(code)
    if mw is None or len(mw) == 0:
        return peak
    try:
        close = mw["close"]
        if len(close) == 0:
            return peak
        if entry_date is not None:
            dates = mw["date"].astype(str).values
            idx = None
            for i, d in enumerate(dates):
                if d >= entry_date:
                    idx = i
                    break
            if idx is None:
                return peak
            sub = close.iloc[idx:]
            if len(sub) == 0:
                return peak
            return max(peak, float(sub.max()))
        return max(peak, float(close.max()))
    except Exception:
        return peak


def _collect_signals(signals, current_date, signal_window, universe, market_window):
    """收集候选信号：当日 + 往前 signal_window-1 个信号日（去重保序，近期优先）。"""
    dates = sorted(signals.keys())
    try:
        idx = dates.index(current_date)
    except ValueError:
        return [c for c in (signals.get(current_date) or [])
                if c in universe and c in market_window]
    window_dates = dates[max(0, idx - signal_window + 1): idx + 1]
    cand = []
    for d in reversed(window_dates):  # 近期优先
        for c in signals.get(d, []):
            if c not in cand:
                cand.append(c)
    return [c for c in cand if c in universe and c in market_window]


@register_strategy("trae_main_uptrend")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    n_hold = int(cfg.get("n_hold", 8))
    max_holding_days = int(cfg.get("max_holding_days", 60))
    stop_loss = cfg.get("stop_loss", -0.12)
    if stop_loss is not None:
        stop_loss = float(stop_loss)
    market_gate = int(cfg.get("market_gate", 0) or 0)
    ma_window = int(cfg.get("ma_window", 200))
    gate_mode = str(cfg.get("gate_mode", "exit")).lower()
    max_single_pct = float(cfg.get("max_single_pct", 1.0 / max(1, n_hold)))
    signal_window = int(cfg.get("signal_window", 1))
    trailing_stop = cfg.get("trailing_stop", None)
    if trailing_stop is not None:
        trailing_stop = float(trailing_stop)

    signals = (aux_data or {}).get("trae_uptrend_signals") or {}
    today_sigs = _collect_signals(signals, current_date, signal_window,
                                  universe, market_window)

    held = {p["code"]: p for p in (positions or [])}
    sell_decisions = []
    buy_candidates = []

    # 1) 持仓管理：止损 / 移动止损 / 到期 → 清仓卖出
    for code, p in held.items():
        cost = float(p.get("cost_price", 0)) or 0.0
        last = float(p.get("last_price", 0)) or 0.0
        hold_days = int(p.get("holding_days", 0) or 0)
        entry_date = p.get("entry_date", None)
        stopped = False
        reason = None
        if stop_loss is not None and cost > 0:
            pnl = (last - cost) / cost
            if pnl <= stop_loss:
                stopped = True
                reason = "stop_loss"
        if not stopped and trailing_stop is not None:
            peak = _peak_close_since_entry(market_window, code, entry_date, last)
            if peak > 0 and (last - peak) / peak <= -trailing_stop:
                stopped = True
                reason = "trailing_stop"
        expired = hold_days >= max_holding_days
        if stopped or expired:
            sell_decisions.append({
                "code": code,
                "reason": reason if stopped else "holding_expired",
                "layer": "confirm",
            })

    # 2) 大盘门控（查预计算表 O(1)，PIT 安全：表只用当日及之前数据）
    if market_gate:
        market_ok_map = (aux_data or {}).get("trae_market_ma200") or {}
        gate_ok = market_ok_map.get(current_date, True)  # 缺省 fail-open
        if not gate_ok:
            logs = ["%s market gate closed (MA%d) -> %s"
                    % (current_date, ma_window,
                       "exit all" if gate_mode == "exit" else "no new buys")]
            diag = {"warnings": ["market_gate_closed_%s" % gate_mode],
                    "candidate_total": len(today_sigs), "candidate_passed": 0}
            if gate_mode == "exit":
                # 全部清仓
                sell_decisions = [{"code": c, "reason": "market_gate",
                                   "layer": "confirm"} for c in held]
            return {
                "sell_decisions": sell_decisions, "buy_candidates": [],
                "target_positions": [], "blocked_candidates": [],
                "diagnostics": diag, "logs": logs,
            }

    # 3) 当日新信号：增量买入（现金分配，单票上限 max_single_pct）
    new_buy = [c for c in today_sigs if c not in held]
    new_buy = new_buy[:max(0, n_hold - len(held) + len([s for s in sell_decisions if s["code"] in today_sigs]))]
    if new_buy:
        total_asset = float(account_state.get("total_asset", 0) or cash or 0.0)
        per_cap = max_single_pct * total_asset
        cash_avail = float(cash or 0.0)
        per = min(per_cap, cash_avail / len(new_buy)) if len(new_buy) > 0 else 0.0
        if per > 0:
            for c in new_buy:
                buy_candidates.append({
                    "code": c,
                    "target_cash": round(per, 2),
                    "reason": "trae_uptrend_signal",
                    "layer": "confirm",
                    "rank": 0,
                })

    # 4) 组装
    has_action = bool(sell_decisions or buy_candidates)
    if has_action:
        logs = []
        if sell_decisions:
            logs.append("%s sell: %s" % (current_date, ",".join(
                "%s(%s)" % (s["code"], s["reason"]) for s in sell_decisions)))
        if buy_candidates:
            logs.append("%s buy: %s (per=%.0f)" % (current_date, ",".join(
                b["code"] for b in buy_candidates), buy_candidates[0]["target_cash"]))
        diag = {
            "warnings": [],
            "candidate_total": len(today_sigs),
            "candidate_passed": len(buy_candidates),
            "strategy_specific": {
                "trae_uptrend": {"new_signals": {"count": len(today_sigs)}},
            },
        }
        return {
            "sell_decisions": sell_decisions, "buy_candidates": buy_candidates,
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": diag, "logs": logs,
        }

    logs = ["%s hold" % current_date]
    diag = {"warnings": ["hold"], "candidate_total": len(today_sigs),
            "candidate_passed": 0}
    return _noop_decision(current_date, logs, diag)
