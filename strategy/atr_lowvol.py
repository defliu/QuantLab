# coding: utf-8
"""ATR 低波动策略 —— QuantLab 框架原生版（target_weights 模式）。

把 atr_lowvol/backtest_atr_lowvol_v3.py 的逻辑用框架通用层重写：
  * 选股：ATR% 最低分位 + 换手率[1,8]% + 非ST + 上市≥60日 + 质量(ROE>0) + 动量门控(12-1月>0)
  * 调仓：月频/季频（config rebalance_freq）
  * 仓位/风险：全交给框架组合层（equal/vol_parity、vol_target、industry_cap、
               target_leverage 两融、max_positions、min_position_value）
  * 真实约束：框架 execution 自带涨跌停/停牌/滑点/整数手/ST±5%

策略本身只负责"选股 + 返回等权意愿"，组合层负责一切订单簿记与风控。
这正证明：任何策略只要输出 target_weights，就能即插即跑、且自动带真实约束。

config（strategy_params）示例：
  rebalance_freq: quarterly
  n_hold: 50
  atr_win: 14
  atr_pct_max: 0.06
  turnover_min: 1.0
  turnover_max: 8.0
  quality_gate: 1          # ROE>0
  momentum_gate: 1         # 12-1月动量>0（剔除近期输家）
  stop_loss: -0.08
  position_sizing: vol_parity
  target_leverage: 1.5     # 两融（需账户支持）
  vol_target: 0.10
  industry_cap: 0.15
"""
from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day
from factors.atr import atr_pct
from factors.roe import get_roe_asof

import pandas as pd
import numpy as np

ALLOWED_TRADING_MODELS = ["next_open"]


def _hold_decision(current_date, positions, stop_loss):
    """非调仓日：保持持仓；若触发止损则退出对应标的（一次性再平衡）。"""
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
        if cost > 0:
            pnl = (last - cost) / cost
        else:
            pnl = 0.0
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
        "target_weights": keep,  # 退出 stopped，其余保持
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {"warnings": ["stop_loss_exit:%d" % len(stopped)],
                        "candidate_total": 0, "candidate_passed": len(keep)},
        "logs": ["%s stop_loss exit %s" % (current_date, stopped)],
    }


def _market_ma_ok(market_window, current_date, ma_window, min_codes=100):
    """大盘门控：全市场等权收盘指数 > MA(ma_window)。

    P10 方向5 口径（run_variants.py）：代理指数 = 全市场等权 close，跌破 MA 即空仓。
    数据不足时 fail-open（返回 True，不挡交易），避免数据缺口误杀。
    """
    frames = []
    for c, df in (market_window or {}).items():
        if df is None or "close" not in df.columns or "date" not in df.columns:
            continue
        frames.append(pd.DataFrame({
            "d": df["date"].astype(str).values,
            "cl": df["close"].astype(float).values,
        }))
    if len(frames) < min_codes:
        return True  # 覆盖不足，fail-open
    big = pd.concat(frames, ignore_index=True)
    big = big[big["d"] <= current_date]
    g = big.groupby("d")["cl"].mean()  # 每日全市场等权收盘
    if len(g) < ma_window:
        return True
    ma = float(g.rolling(ma_window).mean().iloc[-1])
    if ma != ma:  # NaN
        return True
    return float(g.iloc[-1]) > ma


@register_strategy("atr_lowvol")
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
    # 真实价上限（元）：0 关闭。用小资金（如 10 万）时排除高价股，
    # 保证每只够买整手、避免现金闲置。用"真实收盘价 = 复权价/复权因子"，
    # 不能用复权价直接比（复权价是合成数，与真实成交价差一个 adj_factor）。
    max_price = float(cfg.get("max_price", 0) or 0)
    # 大盘门控（P10 方向5 口径）：0=关；1=开。全市场等权收盘指数 vs MA(ma_window)。
    # gate_mode: exit=跌破空仓（清仓等待转多）；hold=只挡买入/重进，不强制清仓。
    market_gate = int(cfg.get("market_gate", 0) or 0)
    ma_window = int(cfg.get("ma_window", 200))
    gate_mode = str(cfg.get("gate_mode", "exit")).lower()
    # 域内排序方式（Lowvol+ 方向，默认纯 ATR%）：atr | momentum | momentum_value
    ranking = str(cfg.get("ranking", "atr")).lower()
    # MAX 彩票效应过滤：剔除 eligible 内 MAX5（近 20 日最大单日收益）最高的 max_exclude_pct 分位（0=关）
    max_exclude_pct = float(cfg.get("max_exclude_pct", 0) or 0)
    # 换手/持仓缓冲：调仓时保留仍在 top(n_hold+buffer) 内的现有持仓，减少换手（0=关）
    rebalance_buffer = int(cfg.get("rebalance_buffer", 0) or 0)

    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        return _hold_decision(current_date, positions, stop_loss)

    # 大盘门控：仅调仓日检查。跌破 MA 时不建仓/不重进（exit 连持仓一并清空，
    # hold 保留现有持仓但不再新买，直接等同"空仓/保持"语义，不做任何交易）。
    if market_gate and not _market_ma_ok(market_window, current_date, ma_window):
        diag = {"warnings": ["market_gate_closed_%s" % gate_mode],
                "candidate_total": 0, "candidate_passed": 0}
        if gate_mode == "exit":
            return {
                "sell_decisions": [], "buy_candidates": [],
                "target_weights": {},  # 空 target -> 引擎 full exit（清仓到现金）
                "target_positions": [], "blocked_candidates": [],
                "diagnostics": diag,
                "logs": ["%s market gate closed (MA%d) -> exit to cash" % (current_date, ma_window)],
            }
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": diag,
            "logs": ["%s market gate closed (MA%d) -> hold, no re-enter" % (current_date, ma_window)],
        }

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

    eligible = []  # [code, atr_pct, ret_12_1, bp]
    price_filtered = 0
    for c in valid:
        df = market_window[c]
        last = df.iloc[-1]
        # 真实价上限过滤：真实收盘价 = 复权价 / 复权因子（缺失 adj_factor 时跳过本过滤，向后兼容）
        if max_price > 0:
            af = last.get("adj_factor")
            if af is not None and float(af) > 0:
                real_close = float(last.get("close", 0)) / float(af)
                if real_close >= max_price:
                    price_filtered += 1
                    continue
        # 换手率过滤
        to = last.get("turnover_rate")
        if to is None or not (turnover_min <= float(to) <= turnover_max):
            continue
        # 非 ST
        if bool(last.get("is_st", False)):
            continue
        # ATR% 过滤（低波动）
        ap = atr_pct(df, atr_win)
        if ap <= 0 or ap > atr_pct_max:
            continue
        # 质量门控 ROE>0
        if quality_gate:
            roe = get_roe_asof(c, current_date)
            if roe is None or roe <= 0:
                continue
        # 动量 12-1 月收益（同时用于门控与 momentum 排序）
        close = df["close"].astype(float).values
        ret_12_1 = 0.0
        if len(close) >= 252:
            ret_12_1 = (close[-21] / close[-252] - 1.0) if close[-252] > 0 else 0.0
        if momentum_gate and ret_12_1 <= 0:
            continue
        # 价值 BP = 1/pb（缺失记 0，rank 时中性）
        pbv = last.get("pb")
        bp = (1.0 / float(pbv)) if (pbv is not None and float(pbv) > 0) else 0.0
        eligible.append([c, ap, ret_12_1, bp])

    # MAX5 彩票效应过滤：近 20 日最大单日收益（Bali 2011 近似口径），剔除最高 max_exclude_pct 分位
    if max_exclude_pct > 0 and len(eligible) > n_hold:
        for r in eligible:
            df = market_window[r[0]]
            rr = df["close"].astype(float).pct_change().dropna()
            r.append(float(rr.tail(20).max()) if len(rr) > 0 else 0.0)  # [c, ap, ret, bp, max5]
        maxs = sorted(r[4] for r in eligible)
        thr = maxs[max(0, int(len(maxs) * (1.0 - max_exclude_pct)) - 1)]
        eligible = [r for r in eligible if r[4] <= thr]

    # 域内排序（Lowvol+）：atr=纯ATR升序；momentum=12-1动量降序；momentum_value=动量+BP 标准化等权
    if ranking == "momentum":
        eligible.sort(key=lambda r: r[2], reverse=True)
    elif ranking == "momentum_value":
        moms = np.array([r[2] for r in eligible], dtype=float)
        bps = np.array([r[3] for r in eligible], dtype=float)
        z_m = (moms - moms.mean()) / (moms.std() + 1e-12)
        z_b = (bps - bps.mean()) / (bps.std() + 1e-12)
        for i, r in enumerate(eligible):
            r.append(z_m[i] + z_b[i])  # [c, ap, ret, bp, max5/score, score]
        eligible.sort(key=lambda r: r[-1], reverse=True)
    else:  # atr
        eligible.sort(key=lambda r: r[1])

    # 选择：缓冲模式下保留仍在 top(n_hold+buffer) 内的现有持仓，其余按排序补足到 n_hold
    pool = eligible[:n_hold + rebalance_buffer]
    if rebalance_buffer > 0 and pool:
        held = {p["code"] for p in positions}
        selected = [r[0] for r in pool if r[0] in held]
        for r in pool:
            if len(selected) >= n_hold:
                break
            if r[0] not in selected:
                selected.append(r[0])
    else:
        selected = [r[0] for r in pool[:n_hold]]

    if not selected:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_selection"],
                            "candidate_total": len(valid), "candidate_passed": 0},
            "logs": ["%s no selection from %d" % (current_date, len(valid))],
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
            "strategy_specific": {
                "atr_lowvol": {"price_filtered": {"count": price_filtered}},
            },
        },
        "logs": ["%s rebalance: %d selected (ATR%%<=%.3f) from %d"
                 % (current_date, len(selected), atr_pct_max, len(valid))],
    }
