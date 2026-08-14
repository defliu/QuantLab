# coding: utf-8
"""RPS 主升浪策略 —— QuantLab 框架原生版（target_weights 模式）。

核心逻辑（基于业界验证 + 诚哥知识库证据）：
  * 选股：板块 RPS 前 N + 个股 RPS > 阈值（双强过滤）
  * 入场：突破 20 日新高 + 放量确认（可选）
  * 调仓：周频/双周频（config rebalance_freq）
  * 仓位：等权（框架组合层）
  * 风控：大盘门控（000300>MA60）+ 个股止损 + 移动止盈

证据来源：
  - IMA 文章：RPS>90 年化 28.7%（需 PIT 验证）
  - 北京炒家：4 层风控体系（分仓/止损/账户/大盘）
  - 诚哥通宵研究：趋势选股 IC 负，但趋势择时门控有效（回撤砍半）
  - 板块 RPS 前 5：业界验证有效（亏损→盈利）

config（strategy_params）示例：
  rebalance_freq: weekly        # weekly / biweekly / monthly
  n_hold: 20                    # 持仓数量
  rps_window_short: 20          # 短周期 RPS 窗口（日）
  rps_window_long: 120          # 长周期 RPS 窗口（日）
  rps_threshold: 90             # 个股 RPS 阈值（0-100）
  sector_top_n: 5               # 板块 RPS 前 N
  sector_rps_window: 20         # 板块 RPS 窗口（日）
  breakout_window: 20           # 突破窗口（日）
  volume_confirm: 1             # 放量确认（1=需放量，0=不需）
  volume_ratio_min: 1.5         # 放量倍数阈值
  market_gate: 1                # 大盘门控（1=启用 000300>MA60，0=禁用）
  benchmark_code: "000300.SH"   # 大盘基准
  ma_window: 60                 # 大盘均线窗口
  stop_loss: -0.08              # 个股止损
  trailing_stop: -0.12          # 移动止盈（最高价回撤）
  max_holding_days: 60          # 最长持有天数
"""
import logging

from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day

log = logging.getLogger(__name__)

ALLOWED_TRADING_MODELS = ["next_open"]

# 板块 RPS 模块（懒加载）
_sector_rps = None


def _get_sector_rps():
    """懒加载板块 RPS 模块（避免 import 依赖 pyarrow 拖慢）。"""
    global _sector_rps
    if _sector_rps is None:
        try:
            from strategy.sector_rps import SectorRPS
            _sector_rps = SectorRPS()
        except Exception as e:
            log.warning("sector_rps 模块加载失败: %s; 板块过滤禁用", e)
            _sector_rps = False  # 失败标记
    return _sector_rps if _sector_rps else None


def _calc_rps(df, window):
    """计算个股的 RPS（相对强度排名）。

    RPS = 过去 window 日涨幅在全市场的百分位（0-100）。
    这里返回的是个股涨幅，需要在外层做截面排名。

    Args:
        df: 个股日线 DataFrame（需含 close 列）
        window: 计算窗口（交易日数）

    Returns:
        float: 过去 window 日涨幅（小数，如 0.15 表示 +15%）
               如果数据不足返回 None
    """
    if df is None or len(df) < window + 1:
        return None
    close = df["close"]
    if close is None or len(close) < window + 1:
        return None
    # 只取最后 window+1 个值，避免整列 astype 复制
    last = close.iloc[-1]
    start = close.iloc[-window - 1]
    try:
        end_price = float(last)
        start_price = float(start)
    except Exception:
        return None
    if start_price <= 0:
        return None
    return (end_price / start_price) - 1.0


def _check_breakout(df, window):
    """检查是否突破 N 日新高。

    Args:
        df: 个股日线 DataFrame（需含 high/close 列）
        window: 突破窗口（交易日数）

    Returns:
        bool: 当日收盘是否突破过去 window 日最高价
    """
    if df is None or len(df) < window + 1:
        return False
    high = df["high"]
    close = df["close"]
    if high is None or close is None or len(high) < window + 1 or len(close) < 1:
        return False
    try:
        recent_high = max(float(v) for v in high.iloc[-window - 1:-1])  # 不含当日
        return float(close.iloc[-1]) > recent_high
    except Exception:
        return False


def _check_pullback_entry(df, pullback_ma_window=20, pullback_max=0.15, pullback_min=0.02,
                          ma_tolerance=0.07):
    """回调买入判断：RPS 高，但当前价从近期高点回调，且仍在均线附近企稳。

    业界 RPS 打法核心：RPS 高 ≠ 追突破买入，而是等回调到均线企稳再买。
    避免在情绪最高点（突破日）追入，胜率更高。

    Args:
        df: 个股日线 DataFrame
        pullback_ma_window: 回调参照均线窗口（日）
        pullback_max: 距近期高点最大回调幅度（0.15 = 15%）
        pullback_min: 最小回调幅度（过滤仍在加速上涨的票）
        ma_tolerance: 允许跌破均线的幅度（0.07 = 允许跌破 7%，洗盘容忍）

    Returns:
        bool: 是否满足回调买入条件
    """
    if df is None or len(df) < pullback_ma_window + 1:
        return False
    try:
        high = df["high"]
        close = df["close"]
        if high is None or close is None or len(high) < pullback_ma_window + 1:
            return False

        # 近期高点（不含当日，过去 pullback_ma_window 日）
        recent_high = max(float(v) for v in high.iloc[-pullback_ma_window - 1:-1])
        if recent_high <= 0:
            return False

        last_close = float(close.iloc[-1])

        # 从高点回调幅度
        pullback = (recent_high - last_close) / recent_high

        # 均线附近企稳（允许跌破均线 ma_tolerance 以内，洗盘容忍）
        ma_val = float(close.iloc[-pullback_ma_window:].mean())
        if ma_val <= 0:
            return False
        above_ma = last_close >= ma_val * (1.0 - ma_tolerance)

        # 回调幅度在 [pullback_min, pullback_max] 之间
        in_pullback_zone = pullback_min <= pullback <= pullback_max

        return above_ma and in_pullback_zone
    except Exception:
        return False


def _check_volume_confirm(df, ratio_min):
    """检查放量确认。

    Args:
        df: 个股日线 DataFrame（需含 vol 列，astock parquet 字段名）
        ratio_min: 放量倍数阈值（当日量 / 5 日均量）

    Returns:
        bool: 当日成交量是否 >= 5 日均量 * ratio_min
    """
    if df is None or len(df) < 6:
        return False
    # astock parquet 字段名是 vol 不是 volume
    vol_col = "vol" if "vol" in df.columns else "volume"
    if vol_col not in df.columns:
        return False
    volume = df[vol_col]
    if volume is None or len(volume) < 6:
        return False
    try:
        vol_today = float(volume.iloc[-1])
        vol_ma5 = sum(float(v) for v in volume.iloc[-6:-1]) / 5.0  # 不含当日
    except Exception:
        return False
    if vol_ma5 <= 0:
        return False
    return (vol_today / vol_ma5) >= ratio_min


def _check_market_gate(benchmark_closes, current_date, ma_window):
    """大盘门控：000300 > MA60。

    Args:
        benchmark_closes: dict {date_str: close} 大盘基准收盘价
        current_date: 当前日期（str）
        ma_window: 均线窗口（交易日数）

    Returns:
        bool: 大盘是否在 MA60 上方（True=允许开仓，False=禁止开仓）
    """
    if not benchmark_closes or current_date not in benchmark_closes:
        return True  # 无数据时 fail-open（不阻止交易）

    # 取过去 ma_window 日的收盘价
    dates_sorted = sorted(benchmark_closes.keys())
    if current_date not in dates_sorted:
        return True

    idx = dates_sorted.index(current_date)
    if idx < ma_window:
        return True  # 数据不足，fail-open

    closes = [benchmark_closes[d] for d in dates_sorted[idx - ma_window + 1:idx + 1]]
    if len(closes) < ma_window:
        return True

    ma_value = sum(closes) / len(closes)
    current_close = benchmark_closes[current_date]
    return current_close > ma_value


def _peak_since_entry(market_window, code, entry_date, fallback):
    """计算入场后的历史最高价（用于移动止盈）。

    Args:
        market_window: {code: DataFrame}（含 date/high 列）
        code: 股票代码
        entry_date: 入场日期（str YYYY-MM-DD），可能为 None
        fallback: 无法计算时返回的兜底值（当前价）

    Returns:
        float: 入场后的最高价；无法计算时返回 fallback
    """
    mw = (market_window or {}).get(code)
    if mw is None or len(mw) == 0:
        return fallback
    try:
        high = mw["high"]
        if len(high) == 0:
            return fallback
        if entry_date is not None:
            dates = mw["date"].astype(str).values
            # 找 entry_date 之后的第一个索引
            idx = 0
            for i, d in enumerate(dates):
                if d >= entry_date:
                    idx = i
                    break
            sub = high.iloc[idx:]
            if len(sub) == 0:
                return fallback
            return float(sub.max())
        # 无 entry_date：用全部历史最高
        return float(high.max())
    except Exception:
        return fallback


def _hold_decision(current_date, positions, stop_loss, trailing_stop, max_holding_days, market_window):
    """非调仓日：保持持仓；若触发止损/移动止盈/时间止损则退出对应标的。

    重要：无触发时**不返回 target_weights**（引擎不会做 diff，保持持仓不动）。
    只有触发退出时才返回 target_weights（保留未触发的，退出触发的）。
    否则每次非调仓日都触发 rebalance diff，导致疯狂加仓 + 高换手。
    """
    if not positions:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold_empty"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold (empty)" % current_date],
        }

    stopped = []
    keep = {}
    for p in positions:
        code = p["code"]
        cost = float(p.get("cost_price", 0)) or 0.0
        last = float(p.get("last_price", 0)) or 0.0
        hold_days = int(p.get("holding_days", 0)) or 0
        entry_date = p.get("entry_date", None)

        # 个股止损
        if stop_loss is not None and cost > 0:
            pnl = (last - cost) / cost
            if pnl <= stop_loss:
                stopped.append((code, "stop_loss"))
                continue

        # 移动止盈（入场后历史最高价回撤）
        if trailing_stop is not None:
            highest = _peak_since_entry(market_window, code, entry_date, last)
            if highest > 0:
                drawdown = (last - highest) / highest
                if drawdown <= trailing_stop:
                    stopped.append((code, "trailing_stop"))
                    continue

        # 时间止损
        if max_holding_days is not None and hold_days >= max_holding_days:
            stopped.append((code, "time_stop"))
            continue

        keep[code] = 1.0

    if not stopped:
        # 无触发：不返回 target_weights（保持持仓不动）
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold %d positions (no change)" % (current_date, len(keep))],
        }

    stop_reasons = {}
    for code, reason in stopped:
        stop_reasons.setdefault(reason, []).append(code)

    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": keep,  # 退出 stopped，其余保持
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {"warnings": ["stopped:%d" % len(stopped)],
                        "candidate_total": 0, "candidate_passed": len(keep),
                        "stop_reasons": stop_reasons},
        "logs": ["%s stopped %s" % (current_date, stop_reasons)],
    }


@register_strategy("rps_momentum")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    """RPS 主升浪策略主入口。

    选股流程：
      1. 大盘门控（可选）：000300 > MA60
      2. 计算所有个股的短/长周期 RPS（涨幅）
      3. 截面排名，过滤 RPS > 阈值
      4. 突破 20 日新高（可选）
      5. 放量确认（可选）
      6. 按 RPS 降序取前 n_hold
    """
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "weekly")
    n_hold = int(cfg.get("n_hold", 20))
    rps_window_short = int(cfg.get("rps_window_short", 20))
    rps_window_long = int(cfg.get("rps_window_long", 120))
    rps_threshold = float(cfg.get("rps_threshold", 90))
    breakout_window = int(cfg.get("breakout_window", 20))
    volume_confirm = int(cfg.get("volume_confirm", 1))
    volume_ratio_min = float(cfg.get("volume_ratio_min", 1.5))
    market_gate = int(cfg.get("market_gate", 1))
    ma_window = int(cfg.get("ma_window", 60))
    stop_loss = cfg.get("stop_loss", -0.08)
    if stop_loss is not None:
        stop_loss = float(stop_loss)
    trailing_stop = cfg.get("trailing_stop", -0.12)
    if trailing_stop is not None:
        trailing_stop = float(trailing_stop)
    max_holding_days = cfg.get("max_holding_days", 60)
    if max_holding_days is not None:
        max_holding_days = int(max_holding_days)
    min_history = int(cfg.get("min_history", 252))
    # 板块 RPS 参数（v1.1）
    sector_rps_enabled = int(cfg.get("sector_rps_enabled", 0))
    sector_top_n = int(cfg.get("sector_top_n", 5))
    sector_rps_window = int(cfg.get("sector_rps_window", 20))
    # V2 入场模式：breakout（追突破）/ pullback（回调买入）
    entry_mode = str(cfg.get("entry_mode", "breakout")).lower()
    pullback_ma_window = int(cfg.get("pullback_ma_window", 20))
    pullback_max = float(cfg.get("pullback_max", 0.15))
    pullback_min = float(cfg.get("pullback_min", 0.02))
    # V2 持仓保留：已持仓 RPS 仍高则保留（让利润奔跑，降低换手）
    keep_held = int(cfg.get("keep_held", 1))
    keep_threshold = float(cfg.get("keep_threshold", 60))

    # 非调仓日：持仓监控
    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        return _hold_decision(current_date, positions, stop_loss, trailing_stop,
                              max_holding_days, market_window)

    # 大盘门控
    if market_gate:
        benchmark_closes = (aux_data or {}).get("benchmark_closes")
        if not _check_market_gate(benchmark_closes, current_date, ma_window):
            return {
                "sell_decisions": [], "buy_candidates": [],
                "target_weights": {},  # 空仓
                "target_positions": [], "blocked_candidates": [],
                "diagnostics": {"warnings": ["market_gate_blocked"],
                                "candidate_total": 0, "candidate_passed": 0},
                "logs": ["%s market gate blocked (benchmark < MA%d)" % (current_date, ma_window)],
            }

    # 过滤有效 universe
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

    # 计算所有个股的短/长周期 RPS（涨幅）
    rps_short = {}
    rps_long = {}
    for c in valid:
        df = market_window[c]
        rps_s = _calc_rps(df, rps_window_short)
        rps_l = _calc_rps(df, rps_window_long)
        if rps_s is not None:
            rps_short[c] = rps_s
        if rps_l is not None:
            rps_long[c] = rps_l

    if not rps_short:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_rps_data"],
                            "candidate_total": len(valid), "candidate_passed": 0},
            "logs": ["%s no RPS data" % current_date],
        }

    # 截面排名（RPS 百分位）
    sorted_short = sorted(rps_short.items(), key=lambda x: x[1], reverse=True)
    n_total = len(sorted_short)
    rps_percentile = {}
    for i, (code, ret) in enumerate(sorted_short):
        # RPS = (1 - rank / n_total) * 100
        # rank 0 = 最强 -> RPS = 100
        # rank n_total-1 = 最弱 -> RPS ≈ 0
        rps_percentile[code] = (1.0 - float(i) / float(n_total)) * 100.0

    # 过滤：个股 RPS > 阈值
    eligible = []
    for code, rps_pct in rps_percentile.items():
        if rps_pct < rps_threshold:
            continue

        df = market_window[code]
        last = df.iloc[-1]

        # 非 ST（astock parquet 的 is_st 是 double：0.0=非ST，1.0=ST）
        is_st_val = last.get("is_st", 0.0)
        if float(is_st_val) > 0.5:  # 1.0 视为 ST
            continue

        # 入场模式（v2）：
        #   breakout = 追突破新高（v1，胜率低）
        #   pullback = 回调买入（v2，业界验证：RPS 高但等回调企稳再买）
        if entry_mode == "pullback":
            if not _check_pullback_entry(df, pullback_ma_window, pullback_max, pullback_min):
                continue
        elif breakout_window > 0:
            if not _check_breakout(df, breakout_window):
                continue

        # 放量确认（可选）
        if volume_confirm:
            if not _check_volume_confirm(df, volume_ratio_min):
                continue

        eligible.append((code, rps_pct))

    # 板块 RPS 过滤（v1.1，可选）：只保留板块 RPS 前 sector_top_n 的股票
    if sector_rps_enabled and eligible:
        sr = _get_sector_rps()
        if sr is not None:
            try:
                sector_rps = sr.compute_sector_rps(market_window, sector_rps_window)
                if sector_rps:
                    # 取板块 RPS 前 sector_top_n
                    top_sectors = sorted(sector_rps.items(),
                                         key=lambda x: x[1], reverse=True)[:sector_top_n]
                    top_sector_set = set(ind for ind, _ in top_sectors)
                    eligible = [(c, p) for c, p in eligible
                                if sr.code_to_industry(c) in top_sector_set]
            except Exception as e:
                log.warning("sector RPS 计算失败: %s; 跳过板块过滤", e)

    # 持仓保留（v2）：已持仓的票如果 RPS 仍高则保留，不因"不再满足入场条件"而强制卖出
    # 这允许利润奔跑——入场后不因"回调结束/突破信号消失"而换掉
    held_codes = set(p["code"] for p in positions)
    held_keep = []
    if keep_held:
        for p in positions:
            code = p["code"]
            rps_pct = rps_percentile.get(code, 0.0)
            # 已持仓且 RPS 仍 > 保留阈值（宽松，如 60），则保留
            if rps_pct >= keep_threshold and code not in [c for c, _ in eligible]:
                held_keep.append((code, rps_pct))

    # 合并：新选 + 已持仓保留，按 RPS 排序取前 n_hold
    all_selected = eligible + held_keep
    all_selected.sort(key=lambda x: x[1], reverse=True)
    selected = [c for c, _ in all_selected[:n_hold]]

    if not selected:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_selection"],
                            "candidate_total": len(valid), "candidate_passed": 0},
            "logs": ["%s no selection from %d" % (current_date, len(valid))],
        }

    # 权重构建（V4 修复）：用 custom sizing 传递精确权重，避免引擎加仓/单票超重。
    # 核心问题（V3 诊断）：等权模式下引擎 base = {c: 1/len(sel)} 无视传入权重，
    # 持仓因止损减少时会把资金全分给剩余票 -> 加仓到高位 -> 止损保护不了。
    # 修复：已持仓的票传"当前市值占比"（引擎不动它），新票分剩余资金。
    selected_set = set(selected)
    total_asset = 0.0
    held_values = {}
    for p in positions:
        code = p["code"]
        val = float(p.get("volume", 0)) * float(p.get("last_price", 0))
        held_values[code] = val
        total_asset += val
    total_asset += float(cash or 0.0)

    target_weights = {}
    # 1) 已持仓且仍被选中的票：保持当前市值占比（避免加仓）
    for code in selected:
        if code in held_values and total_asset > 0:
            target_weights[code] = held_values[code] / total_asset
    # 2) 新票：分剩余资金（等权）
    held_sel = set(target_weights.keys())
    new_codes = [c for c in selected if c not in held_sel]
    remaining = 1.0 - sum(target_weights.values())
    if new_codes and remaining > 0:
        per_new = remaining / len(new_codes)
        for c in new_codes:
            target_weights[c] = per_new
    elif new_codes:
        # 剩余为 0（理论不会）：等权兜底
        per_new = 1.0 / len(selected)
        target_weights = {c: per_new for c in selected}
    # 归一化兜底（浮点误差）
    tw_sum = sum(target_weights.values())
    if tw_sum > 0 and abs(tw_sum - 1.0) > 1e-6:
        target_weights = {c: w / tw_sum for c, w in target_weights.items()}

    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": target_weights,
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {"warnings": [],
                        "candidate_total": len(valid),
                        "candidate_passed": len(selected),
                        "rps_threshold": rps_threshold,
                        "n_eligible": len(eligible),
                        "n_held_keep": len(held_keep),
                        "n_new_buy": len(new_codes),
                        "entry_mode": entry_mode},
        "logs": ["%s selected %d (new=%d keep=%d from %d eligible, %d valid, mode=%s)" %
                 (current_date, len(selected), len(new_codes), len(held_keep),
                  len(eligible), len(valid), entry_mode)],
    }
