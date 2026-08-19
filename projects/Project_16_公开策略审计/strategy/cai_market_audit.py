# coding: utf-8
"""菜场大妈六步选股 —— A4 干净口径（框架引擎 target_weights 模式）。

Project_16 内实现，注册名 `cai_market_audit`。只读调用引擎/数据，不改任何既有文件。

六步规则（任务书 §2.2，与模拟器 A3 同款，只改撮合/成本由引擎设施承担）：
  1 基础池: 剔ST / 上市>=250自然日 / 剔科创688、北交.BJ
  2 股息率前25%: 自建TTM(aux div_ttm) >0 降序前25%
  3 盈利: pe_ttm>0 且 净利同比>0 (aux yoy_pit)
  4 PEG: 0 < pe_ttm/(yoy*100) < 3
  5 价格: 2 <= 不复权 close <= 9 (不复权 close = hfq close / adj_factor)
  6 总市值升序前10, 等权

撮合/成本/涨跌停/停牌/整手 全部由引擎 execution 层承担（T-1 信号 -> T+1 开盘成交）。
引擎 `_slice_window_fast` 不含当日 bar = 保守方向（任务书 §2.5 已知口径）。
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


@register_strategy("cai_market_audit")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "weekly")
    n_hold = int(cfg.get("n_hold", 10))
    min_listed_days = int(cfg.get("min_listed_days", 250))
    div_top_pct = float(cfg.get("div_top_pct", 0.25))
    price_lo = float(cfg.get("price_lo", 2.0))
    price_hi = float(cfg.get("price_hi", 9.0))

    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }

    aux = aux_data or {}
    div_wide = aux.get("div_ttm")
    yoy_wide = aux.get("yoy_pit")
    mv_wide = aux.get("total_mv")
    list_date_map = aux.get("list_date")  # {code: list_date(Timestamp)}
    if list_date_map is None:
        list_date_map = {}

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

    # 步骤1: 基础池（先只过滤 step1，保住步骤2的"当期池内前25%"语义）
    base = []
    for c in universe:
        df = market_window.get(c)
        if df is None or len(df) == 0:
            continue
        last = df.iloc[-1]
        is_st_val = last.get("is_st", 0)
        if pd.notna(is_st_val) and bool(is_st_val):
            continue
        if c.startswith("688") or c.endswith(".BJ"):
            continue
        ldt = list_date_map.get(c)
        if ldt is not None and pd.notna(ldt):
            listed_days = max(0, int((cur_ts - pd.Timestamp(ldt)).days))
            if listed_days < min_listed_days:
                continue
        base.append((c, str(last["date"])))
    if not base:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_base_pool"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s no base pool" % current_date],
        }

    # 步骤2: 股息率 >0 降序前25%（自建TTM，按窗口最后bar日期取=无前视）
    dy_rows = []
    for c, asof in base:
        dy = _get(div_wide, c, asof)
        if dy != dy or dy <= 0:
            continue
        dy_rows.append([c, dy, asof])
    if not dy_rows:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_dividend"], "candidate_total": len(base),
                            "candidate_passed": 0},
            "logs": ["%s no dividend-yield candidates" % current_date],
        }
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
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_final"], "candidate_total": len(base),
                            "candidate_passed": 0},
            "logs": ["%s no final candidates" % current_date],
        }

    # 步骤6: 总市值升序取前 n_hold
    rows.sort(key=lambda r: r[1])
    selected = [r[0] for r in rows[:n_hold]]

    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": {c: 1.0 for c in selected},
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(base),
            "candidate_passed": len(selected),
            "strategy_specific": {
                "cai_market_audit": {"selected_count": len(selected)},
            },
        },
        "logs": ["%s cai_market rebalance: %d selected from %d"
                 % (current_date, len(selected), len(rows))],
    }