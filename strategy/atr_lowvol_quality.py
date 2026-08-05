# coding: utf-8
"""ATR 低波动升级版 —— 红利低波 + 质量（target_weights 模式）。

在 atr_lowvol 的基础上，把选股域从「全A量价低波」收敛到「红利低波 + 自由现金流为正
+ ROE稳定」——对应研究结论：纯量价低波已拥挤失效，而红利股（银行/能源/公用）内的
基本面因子拥挤度低、仍有 alpha。

新增合格域门槛（config 驱动，可单独开关）：
  * dividend_min  : dv_ttm（股息率TTM, %）>= dividend_min  → 红利门槛
  * fcf_gate      : 0=关, 1=要求 ocfps>0（经营现金流为正）, 2=要求 fcff>0（自由现金流为正）
  * roe_stable_n  : 要求最近 n 个季报 ROE 全部 > 0（盈利连续稳定）

保留 atr_lowvol 的全部基础门槛：ATR%<=max、换手率[1,8]%、非ST、上市>=60日、
ROE>0、12-1月动量>0。组合层仍交给框架（equal/vol_parity、vol_target、杠杆等）。

为做「纯合格域」对比，升级版与原版应使用完全相同的组合层参数，
唯一差异就是这里的合格域约束——这样能干净地隔离因子升级的增量贡献。
"""
from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day
from factors.atr import atr_pct
from factors.roe import get_roe_asof
from factors.fina import get_fina_asof, is_roe_stable

ALLOWED_TRADING_MODELS = ["next_open"]


def _hold_decision(current_date, positions, stop_loss):
    """非调仓日：保持持仓；若触发止损则退出对应标的。"""
    if stop_loss is None or stop_loss >= 0:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }
    stopped = []
    keep = {}
    for p in positions:
        cost = float(p.get("cost_price", 0)) or 0.0
        last = float(p.get("last_price", 0)) or 0.0
        pnl = (last - cost) / cost if cost > 0 else 0.0
        if pnl <= stop_loss:
            stopped.append(p["code"])
        else:
            keep[p["code"]] = 1.0
    if not stopped:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": keep,
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {"warnings": ["stop_loss_exit:%d" % len(stopped)],
                        "candidate_total": 0, "candidate_passed": len(keep)},
        "logs": ["%s stop_loss exit %s" % (current_date, stopped)],
    }


@register_strategy("atr_lowvol_quality")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "monthly")
    n_hold = int(cfg.get("n_hold", 100))
    atr_win = int(cfg.get("atr_win", 14))
    atr_pct_max = float(cfg.get("atr_pct_max", 0.06))
    turnover_min = float(cfg.get("turnover_min", 1.0))
    turnover_max = float(cfg.get("turnover_max", 8.0))
    quality_gate = int(cfg.get("quality_gate", 1))
    momentum_gate = int(cfg.get("momentum_gate", 1))
    stop_loss = cfg.get("stop_loss", None)
    if stop_loss is not None:
        stop_loss = float(stop_loss)
    min_history = int(cfg.get("min_history", 252))

    # --- 升级版新增门槛（可单独关闭）---
    dividend_min = float(cfg.get("dividend_min", 2.0))   # dv_ttm(%) 门槛
    fcf_gate = int(cfg.get("fcf_gate", 2))               # 0/1/2
    roe_stable_n = int(cfg.get("roe_stable_n", 8))       # 0=关

    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        return _hold_decision(current_date, positions, stop_loss)

    valid = [c for c in universe
             if c in market_window and len(market_window[c]) >= min_history]
    if not valid:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_valid_universe"],
                            "candidate_total": 0, "candidate_passed": 0},
            "logs": ["%s no valid universe" % current_date],
        }

    blocked = {"low_atr": 0, "turnover": 0, "st": 0, "roe": 0,
               "momentum": 0, "dividend": 0, "fcf": 0, "roe_stable": 0}
    eligible = []
    for c in valid:
        df = market_window[c]
        last = df.iloc[-1]
        # 换手率过滤
        to = last.get("turnover_rate")
        if to is None or not (turnover_min <= float(to) <= turnover_max):
            blocked["turnover"] += 1
            continue
        # 非 ST
        if bool(last.get("is_st", False)):
            blocked["st"] += 1
            continue
        # ATR% 过滤（低波动）
        ap = atr_pct(df, atr_win)
        if ap <= 0 or ap > atr_pct_max:
            blocked["low_atr"] += 1
            continue
        # 质量门控 ROE>0
        if quality_gate:
            roe = get_roe_asof(c, current_date)
            if roe is None or roe <= 0:
                blocked["roe"] += 1
                continue
        # 动量门控：12-1 月收益 > 0
        if momentum_gate:
            close = df["close"].astype(float).values
            if len(close) >= 252:
                ret_12_1 = (close[-21] / close[-252] - 1.0
                            if close[-252] > 0 else 0.0)
                if ret_12_1 <= 0:
                    blocked["momentum"] += 1
                    continue
        # 红利门槛：dv_ttm（股息率TTM, %）>= dividend_min
        if dividend_min > 0:
            dv = last.get("dv_ttm")
            if dv is None or not (float(dv) >= dividend_min):
                blocked["dividend"] += 1
                continue
        # 自由现金流为正
        if fcf_gate:
            field = "fcff" if fcf_gate == 2 else "ocfps"
            fv = get_fina_asof(c, current_date, field)
            if fv is None or fv <= 0:
                blocked["fcf"] += 1
                continue
        # ROE 稳定性：最近 n 个季报 ROE 全部 > 0
        if roe_stable_n and roe_stable_n > 0:
            if not is_roe_stable(c, current_date, roe_stable_n):
                blocked["roe_stable"] += 1
                continue
        eligible.append((c, ap))

    eligible.sort(key=lambda x: x[1])
    selected = [c for c, _ in eligible[:n_hold]]

    if not selected:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_selection"],
                            "candidate_total": len(valid), "candidate_passed": 0,
                            "blocked_breakdown": blocked},
            "logs": ["%s no selection from %d (%s)"
                     % (current_date, len(valid), blocked)],
        }

    target_weights = {c: 1.0 for c in selected}
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": target_weights,
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(valid),
            "candidate_passed": len(selected),
            "blocked_breakdown": blocked,
        },
        "logs": ["%s rebalance: %d selected (ATR%%<=%.3f, dv_ttm>=%.1f) from %d"
                 % (current_date, len(selected), atr_pct_max,
                    dividend_min, len(valid))],
    }
