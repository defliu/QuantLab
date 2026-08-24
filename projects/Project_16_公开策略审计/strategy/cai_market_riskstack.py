# coding: utf-8
"""菜场大妈六步选股 × P10 式风控栈 —— T-20260820-001 消融变体。

六步选股与 cai_market_audit 完全一致（逐行同款），叠加可配置风控项：
  stop_loss_pct   日频个股止损：持仓 last/cost - 1 <= -pct 即卖出。
                  非调仓日走 sell_decisions 只卖不加（止损资金留到下个调仓日，
                  不当日回补，保持与基线相同的敞口演化口径）；跌停未成交则
                  次日重新触发 = 自动重试（引擎 limit_down_at_open 拒单语义）。
  market_gate     大盘门控（P10 方向5 口径：全市场等权收盘 vs MA(ma_window)，
                  序列由 aux_data["mkt_ew_close"] 预计算传入，数据不足 fail-open）。
                  gate_mode: exit=跌破清仓到现金 / hold=跌破只挡新买（冻结组合）。
  ever_st_exclude 退市排雷增强：剔除窗口（warmup≈500自然日）内任一日 is_st 的票
                  （"曾戴帽即排除"；基础六步只剔当前 is_st）。
n_hold / max_positions 等沿用基线参数，n_hold>10 时 max_positions 需同步放大。

风控优先级：止损 > 大盘门控 > 周度调仓。target_weights 为相对权重，
position_sizing=equal 时引擎自动等权归一。
"""
from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day

import numpy as np

ALLOWED_TRADING_MODELS = ["next_open"]


def _asof_row(aux_wide, date, code):
    """aux_wide: DataFrame(index=str日期, columns=ts_code)。取 <=date 的最新可用值。"""
    try:
        if date in aux_wide.index:
            v = aux_wide.loc[date, code]
        else:
            prev = aux_wide.index[aux_wide.index <= date]
            if len(prev) == 0:
                return np.nan
            v = aux_wide.loc[prev[-1], code]
        if isinstance(v, (np.ndarray,)):
            return np.nan
        return float(v)
    except Exception:
        return np.nan


def _market_ma_ok(aux_data, current_date, ma_window):
    """大盘门控：全市场等权收盘指数（raw close 均值，PIT 同日截面）> MA(ma_window)。

    与 P10 方向5 同口径；序列在 build_aux 内预计算为 mkt_ew_close。
    数据不足 fail-open（返回 True 不挡交易）。
    """
    s = (aux_data or {}).get("mkt_ew_close")
    if s is None or len(s) == 0:
        return True
    try:
        vals = s[s.index <= current_date]
    except Exception:
        return True
    if len(vals) < ma_window:
        return True
    ma = float(np.mean(vals.iloc[-ma_window:].values.astype(float)))
    if ma != ma or ma <= 0:
        return True
    return float(vals.iloc[-1]) > ma


def _stop_breached(positions, stop_loss_pct):
    """返回当日触发止损的持仓代码集合（基于 T 日收盘 mark-to-market 价）。"""
    breached = set()
    if stop_loss_pct <= 0:
        return breached
    for p in (positions or []):
        try:
            cost = float(p.get("cost_price", 0) or 0)
            last = float(p.get("last_price", 0) or 0)
        except Exception:
            continue
        if cost > 0 and last > 0 and (last / cost - 1.0) <= -stop_loss_pct:
            breached.add(p["code"])
    return breached


def _hold(current_date, extra_log=""):
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {"warnings": [], "candidate_total": 0,
                        "candidate_passed": 0},
        "logs": ["%s hold%s" % (current_date, (" " + extra_log) if extra_log else "")],
    }


def _six_step_selected(current_date, market_window, universe, cfg, aux_data,
                       exclude_codes=(), ever_st_exclude=0):
    """六步选股，返回入选列表。与 cai_market_audit 逐规则一致，仅加两个可选排除。"""
    n_hold = int(cfg.get("n_hold", 10))
    min_listed_days = int(cfg.get("min_listed_days", 250))
    div_top_pct = float(cfg.get("div_top_pct", 0.25))
    price_lo = float(cfg.get("price_lo", 2.0))
    price_hi = float(cfg.get("price_hi", 9.0))

    aux = aux_data or {}
    div_wide = aux.get("div_ttm")
    yoy_wide = aux.get("yoy_pit")
    mv_wide = aux.get("total_mv")
    list_date_map = aux.get("list_date") or {}

    import pandas as pd
    try:
        cur_ts = pd.Timestamp(current_date)
    except Exception:
        cur_ts = pd.Timestamp("2019-01-01")

    def _get(wide, c, asof_date):
        if wide is None:
            return np.nan
        try:
            if hasattr(wide, "loc"):
                return _asof_row(wide, asof_date, c)
            if isinstance(wide, dict):
                return wide.get(c, np.nan)
        except Exception:
            return np.nan
        return np.nan

    # 步骤1: 基础池
    base = []
    for c in universe:
        if c in exclude_codes:
            continue
        df = market_window.get(c)
        if df is None or len(df) == 0:
            continue
        last = df.iloc[-1]
        is_st_val = last.get("is_st", 0)
        if pd.notna(is_st_val) and bool(is_st_val):
            continue
        if ever_st_exclude and ("is_st" in df.columns):
            try:
                if pd.notna(df["is_st"]) and bool(
                        pd.to_numeric(df["is_st"], errors="coerce").fillna(0).gt(0).any()):
                    continue
            except Exception:
                pass
        if c.startswith("688") or c.endswith(".BJ"):
            continue
        ldt = list_date_map.get(c)
        if ldt is not None and pd.notna(ldt):
            listed_days = max(0, int((cur_ts - pd.Timestamp(ldt)).days))
            if listed_days < min_listed_days:
                continue
        base.append((c, str(last["date"])))
    if not base:
        return []

    # 步骤2: 股息率 >0 降序前25%
    dy_rows = []
    for c, asof in base:
        dy = _get(div_wide, c, asof)
        if dy != dy or dy <= 0:
            continue
        dy_rows.append([c, dy, asof])
    if not dy_rows:
        return []
    dy_rows.sort(key=lambda r: r[1], reverse=True)
    n_top_div = max(1, int(len(dy_rows) * div_top_pct))
    div_pool = {r[0]: r[2] for r in dy_rows[:n_top_div]}

    # 步骤3/4/5: 盈利 + PEG + 价格
    rows = []
    for c, asof in div_pool.items():
        df = market_window.get(c)
        if df is None or len(df) == 0:
            continue
        last = df.iloc[-1]
        try:
            af = float(last.get("adj_factor", 1.0))
            raw_close = float(last.get("close", 0)) / af if af > 0 else float("nan")
        except Exception:
            raw_close = float("nan")
        if raw_close != raw_close or not (price_lo <= raw_close <= price_hi):
            continue
        pe = float(last.get("pe_ttm", 0)) or 0
        if pe <= 0:
            continue
        yoy = _get(yoy_wide, c, asof)
        if yoy != yoy or yoy <= 0:
            continue
        peg = pe / (yoy * 100.0)
        if not (0 < peg < 3):
            continue
        mv = _get(mv_wide, c, asof)
        if mv != mv or mv <= 0:
            continue
        rows.append([c, mv])

    if not rows:
        return []

    # 步骤6: 总市值升序取前 n_hold
    rows.sort(key=lambda r: r[1])
    return [r[0] for r in rows[:n_hold]]


@register_strategy("cai_market_riskstack")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "weekly")
    stop_loss_pct = float(cfg.get("stop_loss_pct", 0) or 0)
    market_gate = int(cfg.get("market_gate", 0) or 0)
    ma_window = int(cfg.get("ma_window", 200))
    gate_mode = str(cfg.get("gate_mode", "exit")).lower()
    ever_st_exclude = int(cfg.get("ever_st_exclude", 0) or 0)

    held_codes = set(p["code"] for p in (positions or []))
    breached = _stop_breached(positions, stop_loss_pct)

    rebal = is_rebalance_day(current_date, freq,
                             (aux_data or {}).get("trading_calendar"))

    # ---- 非调仓日：仅止损（只卖不加） ----
    if not rebal:
        if breached:
            sells = [{"code": c, "reason": "stop_loss", "layer": "risk"}
                     for c in sorted(breached)]
            return {
                "sell_decisions": sells, "buy_candidates": [],
                "target_positions": [], "blocked_candidates": [],
                "diagnostics": {"warnings": ["stop_loss_triggered"],
                                "candidate_total": 0,
                                "candidate_passed": len(sells)},
                "logs": ["%s stop_loss sell %s" % (current_date, sorted(breached))],
            }
        return _hold(current_date)

    # ---- 调仓日 ----
    gate_closed = bool(market_gate) and not _market_ma_ok(aux_data, current_date,
                                                          ma_window)

    if gate_closed and gate_mode == "exit":
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_weights": {},
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["market_gate_closed_exit"],
                            "candidate_total": 0, "candidate_passed": 0},
            "logs": ["%s market gate closed (MA%d) -> exit to cash"
                     % (current_date, ma_window)],
        }

    selected = _six_step_selected(current_date, market_window, universe, cfg,
                                  aux_data, exclude_codes=breached,
                                  ever_st_exclude=ever_st_exclude)

    if gate_closed and gate_mode == "hold":
        # 只挡新买：冻结现有组合（剔除当日触发止损的），不引入新票
        selected = [c for c in held_codes if c not in breached]

    tw = {c: 1.0 for c in selected}
    warn = []
    if breached:
        warn.append("stop_loss_on_rebalance")
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": tw,
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": warn,
            "candidate_total": len(tw),
            "candidate_passed": len(tw),
            "strategy_specific": {
                "cai_market_riskstack": {
                    "selected_count": len(selected),
                    "gate_closed": int(gate_closed),
                    "stop_count_today": len(breached),
                },
            },
        },
        "logs": ["%s riskstack rebalance: %d selected (gate_closed=%s stop=%d)"
                 % (current_date, len(selected), gate_closed, len(breached))],
    }
