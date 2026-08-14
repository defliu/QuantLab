# coding: utf-8
"""DailyBacktestEngine -- main loop wiring reader -> strategy_core ->
execution -> portfolio -> metrics.

QuantLab port of the QMT factory's daily_engine.py. Differences from the
upstream QMT version are limited to import paths (flattened package layout):

  backtest.strategies              -> strategy.registry
  backtest.engine.{execution,...}  -> backtest.{execution,...}
  backtest.data_tools.benchmark_reader -> data.benchmark_reader

Trading model: next_open (T close signal -> T+1 open fill). The engine writes
ONLY an in-memory result struct; file IO is report.py. No xtquant / passorder.
"""
import os
import datetime as _dt
import hashlib
import logging

from strategy.registry import get_strategy, list_strategies
import importlib as _importlib
from backtest.execution import fill_buy, fill_sell
from backtest.portfolio import Portfolio
from backtest.analyzer import compute_metrics
from backtest.rebalance import target_weights_to_decision

log = logging.getLogger(__name__)

_STRATEGY_CORE_VERSION = "0.2.0"
_SUMMARY_SCHEMA_VERSION = "0.2"

DEFAULT_BENCHMARK_DB = "F:/backtest_workspace/data/duckdb/benchmark_index.duckdb"

# 让策略侧 evaluate_day 拿到的 bench 序列足以算 MA60 / MA120 等长窗口指标。
_BENCHMARK_LEAD_IN_DAYS = 120


def _load_benchmark_series(benchmark_code, calendar, benchmark_db_path):
    """Load benchmark closes aligned to the run calendar (forward-fill on gaps).

    Returns (closes_by_date, note). closes_by_date is dict {date: close}
    covering every day in `calendar` (forward-filled from the latest prior
    benchmark close). Returns (None, note) if benchmark cannot be used.
    """
    if not benchmark_code:
        return None, ""
    if not benchmark_db_path:
        return None, u"benchmark_db_path 未配置"
    if not os.path.isfile(benchmark_db_path):
        return None, u"benchmark_index.duckdb 不存在: %s" % benchmark_db_path
    import datetime as _dt2
    try:
        _cal0 = _dt2.datetime.strptime(calendar[0], "%Y-%m-%d").date()
    except Exception:
        _cal0 = None
    if _cal0 is not None:
        _bench_start = (_cal0 - _dt2.timedelta(days=_BENCHMARK_LEAD_IN_DAYS)).strftime("%Y-%m-%d")
    else:
        _bench_start = calendar[0]
    try:
        from data.benchmark_reader import BenchmarkIndexReader
        br = BenchmarkIndexReader(benchmark_db_path)
        try:
            rows = br.load_series(benchmark_code, _bench_start, calendar[-1])
        finally:
            br.close()
    except Exception as e:
        return None, u"benchmark 加载失败: %s" % e
    if not rows:
        return None, u"benchmark 在窗口内无数据 code=%s" % benchmark_code
    bm_map = {d: c for d, c in rows if c is not None}
    if not bm_map:
        return None, u"benchmark close 全为空 code=%s" % benchmark_code
    bm_dates_sorted = sorted(bm_map.keys())
    closes = {}
    for bd in bm_dates_sorted:
        closes[bd] = bm_map[bd]
    last = None
    bi = 0
    for d in calendar:
        while bi < len(bm_dates_sorted) and bm_dates_sorted[bi] <= d:
            last = bm_map[bm_dates_sorted[bi]]
            bi += 1
        if last is not None and d not in closes:
            closes[d] = last
    if not closes:
        return None, (u"benchmark 起点晚于回测窗口 (首条=%s)" % bm_dates_sorted[0])
    missing_head = [d for d in calendar if d not in closes]
    if missing_head:
        head_gap_tolerance_days = 14
        if len(missing_head) <= head_gap_tolerance_days:
            first_bm_close = bm_map[bm_dates_sorted[0]]
            for d in missing_head:
                closes[d] = first_bm_close
        else:
            return None, (u"benchmark 在窗口前期缺失 %d 天 (首=%s, 首条 bm=%s)"
                          % (len(missing_head), missing_head[0],
                         bm_dates_sorted[0]))
    return closes, ""


def _make_run_id(now=None):
    """YYYYMMDD_HHMMSS_<short_hash>."""
    t = now or _dt.datetime.now()
    stamp = t.strftime("%Y%m%d_%H%M%S")
    h = hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:6]
    return stamp + "_" + h


def _slice_window_up_to(market_data, today):
    """For each code, return a DataFrame view containing rows where date <= today."""
    out = {}
    fd = str(today)
    for code, df in market_data.items():
        sub = df[df["date"].astype(str) <= fd]
        if len(sub) > 0:
            out[code] = sub.reset_index(drop=True)
    return out


def _build_cut_index(market_data, calendar):
    """Precompute, per code, the row-count with date <= each calendar day.

    One-time O(codes * days) searchsorted so the daily slice is an O(1) iloc view.
    """
    import numpy as np
    cal_arr = np.asarray(calendar, dtype=object)
    cut = {}
    for code, df in market_data.items():
        dates = df["date"].values
        if len(dates) == 0:
            cut[code] = np.zeros(len(calendar), dtype=np.int64)
            continue
        idx = np.searchsorted(dates, cal_arr, side="right") - 1
        cut[code] = idx.astype(np.int64)
    return cut


def _slice_window_fast(market_data, day_index, cut):
    """O(1)-per-code daily window using precomputed cut indices (no leak)."""
    out = {}
    for code, df in market_data.items():
        c = int(cut[code][day_index])
        if c > 0:
            out[code] = df.iloc[:c]
    return out


def _unique_warnings(daily_warnings):
    seen = []
    seen_set = set()
    for w_list in daily_warnings:
        for w in (w_list or []):
            if w not in seen_set:
                seen_set.add(w)
                seen.append(w)
    return seen


def _aggregate_strategy_specific(daily_ss, n_days):
    """通用 strategy_specific 聚合 —— 按值类型分两类：
      - dict[str, number]   -> 求和后除以 n_days，输出 key+"_avg_per_day"
      - dict[str, dict]     -> 不聚合，保留 union（按 code 索引的结构）
    """
    if n_days <= 0:
        return {}
    by_strat = {}
    for ss in daily_ss:
        if not isinstance(ss, dict):
            continue
        for sname, sdict in ss.items():
            if not isinstance(sdict, dict):
                continue
            sub_map = by_strat.setdefault(sname, {})
            for sub_key, sub_val in sdict.items():
                sub_map.setdefault(sub_key, sub_val)

    out = {}
    for sname, sub_map in by_strat.items():
        sname_out = {}
        for sub_key, sample_val in sub_map.items():
            if isinstance(sample_val, dict) and sample_val and all(
                isinstance(v, (int, float)) for v in sample_val.values()
            ):
                agg = {}
                inner_keys = set()
                for ss in daily_ss:
                    sd = (ss or {}).get(sname, {}) or {}
                    sk = sd.get(sub_key, {}) or {}
                    if isinstance(sk, dict):
                        inner_keys.update(sk.keys())
                for ik in inner_keys:
                    total = 0.0
                    for ss in daily_ss:
                        sd = (ss or {}).get(sname, {}) or {}
                        sk = sd.get(sub_key, {}) or {}
                        if isinstance(sk, dict):
                            total += float(sk.get(ik, 0) or 0)
                    agg[ik] = round(total / float(n_days), 6)
                sname_out[sub_key + "_avg_per_day"] = agg
            else:
                sname_out[sub_key + "_present"] = True
        out[sname] = sname_out
    return out


def resolve_strategy(strategy_name, trading_model):
    """Strategy Registry: 取策略 evaluate_fn + 校验 trading_model。

    QuantLab 扁平布局：策略模块位于 strategy/<name>.py，注册名即扁平名
    （如 'atr_lowvol'）。传入带 category 前缀的 'research/atr_lowvol' 也会
    自动取最后一段 'atr_lowvol' 去匹配。
    """
    name = strategy_name or "atr_lowvol"
    fn = get_strategy(name)

    # 扁平模块：strategy/<last_segment>.py（无 production/research 子包，
    # 也无 .strategy 子模块）。
    mod_seg = name.split("/")[-1]
    mod_name = "strategy." + mod_seg
    try:
        mod = _importlib.import_module(mod_name)
        allowed = list(getattr(mod, "ALLOWED_TRADING_MODELS", ["next_open"]))
    except ImportError:
        allowed = ["next_open"]

    tm = trading_model or "next_open"
    if tm not in allowed:
        raise ValueError(
            "trading_model=" + str(tm) +
            " not in strategy.ALLOWED_TRADING_MODELS=" + str(allowed) +
            " (strategy=" + name + ")"
        )
    return fn, tm


def run_backtest(
    reader,                 # reader instance (or compatible duck-typed)
    universe,               # list of code strings
    start_date,             # "YYYY-MM-DD"
    end_date,               # "YYYY-MM-DD"
    strategy_config,        # dict
    execution_cfg,          # dict: price/slippage/commission_rate/tax_rate
    initial_cash,           # float
    aux_data=None,
    benchmark_code=None,
    benchmark_db_path=DEFAULT_BENCHMARK_DB,
    config_name="baseline",
    config_hash="",
    universe_hash="",
    run_id=None,
    now=None,
    universe_by_date=None,  # PIT mode: {as_of_date_str: [codes]}
    strategy_name=None,     # 扁平注册名，默认 'atr_lowvol'
    trading_model=None,     # 默认 'next_open'
    fundamentals_reader=None,
    industry_map=None,      # {code: industry} for industry_cap overlay
):
    """Run the full backtest. Returns an in-memory result struct."""
    _evaluate_day, _trading_model = resolve_strategy(strategy_name, trading_model)

    started_at = now or _dt.datetime.now()
    run_id = run_id or _make_run_id(started_at)

    cov = reader.coverage(codes=universe, start_date=start_date, end_date=end_date)
    calendar = reader.trading_calendar(start_date, end_date)
    if not calendar:
        raise ValueError("empty trading_calendar for [%s, %s]" % (start_date, end_date))
    actual_min = calendar[0]
    actual_max = calendar[-1]

    warmup_cal_days = int((strategy_config or {}).get("_warmup_calendar_days", 500))
    try:
        warmup_start = (_dt.datetime.strptime(str(actual_min), "%Y-%m-%d")
                        - _dt.timedelta(days=warmup_cal_days)).strftime("%Y-%m-%d")
    except Exception:
        warmup_start = actual_min
    market_data = reader.load_window(universe, warmup_start, actual_max)

    cut_index = _build_cut_index(market_data, calendar)

    benchmark_closes, benchmark_note = _load_benchmark_series(
        benchmark_code, calendar, benchmark_db_path)
    benchmark_available = benchmark_closes is not None
    if benchmark_code and not benchmark_available:
        log.warning("benchmark disabled: %s", benchmark_note)

    pf = Portfolio(initial_cash=initial_cash)
    aux_for_eval = aux_data if aux_data is not None else {}
    if "trading_calendar" not in aux_for_eval or not aux_for_eval.get("trading_calendar"):
        aux_for_eval = dict(aux_for_eval)
        aux_for_eval["trading_calendar"] = calendar
    if "benchmark_closes" not in aux_for_eval:
        aux_for_eval = dict(aux_for_eval)
        aux_for_eval["benchmark_closes"] = benchmark_closes
        aux_for_eval["benchmark_code"] = benchmark_code

    if universe_by_date:
        snap_dates = sorted(universe_by_date.keys())
        per_day_universe = {}
        cur = []
        snap_i = 0
        for today in calendar:
            while snap_i < len(snap_dates) and snap_dates[snap_i] <= today:
                cur = list(universe_by_date[snap_dates[snap_i]])
                snap_i += 1
            per_day_universe[today] = list(cur)
    else:
        per_day_universe = None

    trades = []
    equity_rows = []
    positions_rows = []
    daily_logs = []
    daily_warnings = []
    daily_candidate_total = []
    daily_candidate_passed = []
    daily_strategy_specific = []
    unfilled_order_count = 0

    pending = None
    n_days = len(calendar)

    for i, today in enumerate(calendar):
        if i > 0:
            pf.advance_holding_days()

        if pending is not None:
            for sell_dec in pending.get("sell_decisions", []):
                code = sell_dec["code"]
                pos = pf.positions.get(code)
                if pos is None:
                    daily_logs.append("[ERROR] %s unfilled_order code=%s reason=position_gone"
                                      % (today, code))
                    unfilled_order_count += 1
                    continue
                pos_arg = {
                    "code":             code,
                    "volume":           int(pos["volume"]),
                    "available_volume": int(pos["available_volume"]),
                    "cost_price":       float(pos["cost_price"]),
                    "entry_date":       pos["entry_date"],
                    "holding_days":     int(pos["holding_days"]),
                    "last_price":       float(pos["last_price"]),
                    "unrealized_pnl":   float(pos["unrealized_pnl"]),
                }
                trade, unfilled = fill_sell(sell_dec, pos_arg, market_data,
                                            today, execution_cfg, run_id)
                if trade is not None:
                    pf.apply_trade(trade)
                    trades.append(trade)
                    daily_logs.append("[INFO]  %s fill sell %s vol=%d price=%.4f amt=%.2f"
                                      % (today, code, trade["volume"],
                                         trade["price"], trade["amount"]))
                else:
                    daily_logs.append("[ERROR] %s unfilled_order code=%s reason=%s"
                                      % (today, code, unfilled))
                    unfilled_order_count += 1
            for cand in pending.get("buy_candidates", []):
                trade, unfilled = fill_buy(cand, market_data, today,
                                           execution_cfg, run_id)
                if trade is not None:
                    pf.apply_trade(trade)
                    trades.append(trade)
                    daily_logs.append("[INFO]  %s fill buy %s vol=%d price=%.4f amt=%.2f"
                                      % (today, trade["code"], trade["volume"],
                                         trade["price"], trade["amount"]))
                else:
                    daily_logs.append("[ERROR] %s unfilled_order code=%s reason=%s"
                                      % (today, cand["code"], unfilled))
                    unfilled_order_count += 1

        pf.mark_to_market(market_data, today)

        # 两融利息计提（仅当启用杠杆且现金为负）。
        _lev = float((strategy_config or {}).get("target_leverage", 1.0) or 1.0)
        if _lev > 1.0 and pf.cash < 0:
            _rate = float((strategy_config or {}).get("margin_interest_rate", 0.06) or 0.06)
            pf.cash -= (-pf.cash) * (_rate / 252.0)

        equity_rows.append(pf.equity_row(run_id, today))
        positions_rows.extend(pf.positions_rows(run_id, today))

        if i < n_days - 1:
            window = _slice_window_fast(market_data, i, cut_index)
            account_state = {
                "current_date":         today,
                "trading_day_index":    i,
                "total_asset":          pf.total_asset(),
                "market_value":         pf.market_value(),
                "is_last_trading_day":  False,
                "max_positions":        int(strategy_config.get("max_positions", 5)),
            }

            aux_data_for_eval = aux_for_eval
            if fundamentals_reader is not None:
                try:
                    universe_today = (per_day_universe[today] if per_day_universe is not None
                                      else universe)
                    aux_today = dict(aux_for_eval)
                    aux_today["fundamentals"] = fundamentals_reader.get_fundamentals_for_scoring(
                        universe_today, today)
                    aux_data_for_eval = aux_today
                except Exception as e:
                    log.warning("fundamentals reader failed for %s: %s; fallback", today, e)
                    aux_data_for_eval = aux_for_eval

            decision = _evaluate_day(
                current_date=today,
                market_window=window,
                positions=pf.position_list(),
                cash=pf.cash,
                universe=(per_day_universe[today] if per_day_universe is not None
                          else universe),
                account_state=account_state,
                strategy_config=strategy_config,
                aux_data=aux_data_for_eval,
            )
            if "target_weights" in decision:
                tw = decision["target_weights"]
                # 保留策略侧 strategy_specific 诊断：target_weights_to_decision
                # 会整体重建 decision，策略侧塞入的诊断需在转换前取出、转换后回填。
                _strat_specific = (decision.get("diagnostics", {}) or {}).get(
                    "strategy_specific", {})
                try:
                    decision = target_weights_to_decision(
                        tw, pf, today, strategy_config, window, industry_map)
                    if _strat_specific:
                        decision.setdefault("diagnostics", {})[
                            "strategy_specific"] = _strat_specific
                except Exception as e:
                    log.warning("target_weights rebalance failed %s: %s", today, e)
                    decision = {
                        "sell_decisions": [], "buy_candidates": [],
                        "target_positions": [], "blocked_candidates": [],
                        "diagnostics": {"warnings": ["rebalance_failed:%s" % e],
                                        "candidate_total": 0, "candidate_passed": 0},
                        "logs": [],
                    }
            diag = decision.get("diagnostics", {})
            daily_warnings.append(diag.get("warnings", []))
            daily_candidate_total.append(int(diag.get("candidate_total", 0)))
            daily_candidate_passed.append(int(diag.get("candidate_passed", 0)))
            ss_today = diag.get("strategy_specific", {}) or {}
            daily_strategy_specific.append(ss_today)
            for line in decision.get("logs", []):
                daily_logs.append("[INFO]  " + line)
            pending = decision
        else:
            pending = None

    # Benchmark fill on equity_rows (post-loop, in-place)
    benchmark_returns = None
    benchmark_total_return = None
    if benchmark_available:
        prev_close = None
        bm_series = []
        for row in equity_rows:
            d = row["date"]
            close = benchmark_closes.get(d)
            if close is None:
                row["benchmark_close"] = ""
                row["benchmark_return"] = ""
                bm_series.append(None)
                prev_close = None
                continue
            if prev_close is None or prev_close == 0:
                bm_ret = 0.0
            else:
                bm_ret = (close / prev_close) - 1.0
            row["benchmark_close"] = round(float(close), 6)
            row["benchmark_return"] = round(float(bm_ret), 8)
            bm_series.append(close)
            prev_close = close
        benchmark_returns = []
        valid_pair = True
        for i in range(1, len(bm_series)):
            a, b = bm_series[i - 1], bm_series[i]
            if a is None or b is None or a == 0:
                valid_pair = False
                break
            benchmark_returns.append((b / a) - 1.0)
        if not valid_pair:
            benchmark_available = False
            benchmark_note = u"benchmark 数据存在断点，禁用 IR/excess"
            benchmark_returns = None
        else:
            first_close = bm_series[0]
            last_close = bm_series[-1]
            if first_close and first_close != 0:
                benchmark_total_return = (last_close / first_close) - 1.0
            else:
                benchmark_available = False
                benchmark_returns = None
                benchmark_note = u"benchmark 起点价格为 0，禁用 IR/excess"

    _n_days = max(1, n_days)
    _ct_avg = sum(daily_candidate_total)  / float(_n_days)
    _cp_avg = sum(daily_candidate_passed) / float(_n_days)
    diagnostics_aggregate = {
        "warnings_unique":              _unique_warnings(daily_warnings),
        "candidate_total_avg_per_day":  _ct_avg,
        "candidate_passed_avg_per_day": _cp_avg,
        "unfilled_order_count":         int(unfilled_order_count),
        "strategy_specific": _aggregate_strategy_specific(
            daily_strategy_specific, _n_days
        ),
    }

    performance = compute_metrics(
        equity_rows=equity_rows,
        trades=trades,
        trading_calendar=calendar,
        initial_cash=initial_cash,
        benchmark_available=benchmark_available,
        benchmark_returns=benchmark_returns,
        benchmark_total_return=benchmark_total_return,
        open_positions=pf.position_list(),
    )

    is_short_sample = (n_days < 252) or (not benchmark_available)
    months = round(n_days / 21.0, 1)
    sample_warning = {
        "is_short_sample":  bool(is_short_sample),
        "requested_range":  [start_date, end_date],
        "actual_range":     [actual_min, actual_max],
        "trading_days":     n_days,
        "warning":          (u"样本期约 %s 个月，仅用于 MVP 管线验证，不可作为策略最终定论"
                             % months) if is_short_sample else "",
    }

    from backtest.hashing import compute_data_hash
    data_hash = compute_data_hash(
        db_path=reader.db_path,
        db_mtime=cov.get("db_mtime", ""),
        adjustment=getattr(reader, "adjustment", "hfq"),
        requested_start=start_date,
        requested_end=end_date,
        actual_min=actual_min,
        actual_max=actual_max,
        n_codes=cov.get("n_codes", 0),
        n_rows_after_dedup=cov.get("n_rows_after_dedup", 0),
        dedup_count=cov.get("dedup_count", 0),
        universe_hash=universe_hash,
    )

    end_total = pf.total_asset()
    end_cash = pf.cash
    end_mv = pf.market_value()

    runtime = (_dt.datetime.now() - started_at).total_seconds()

    summary = {
        "summary_schema_version": _SUMMARY_SCHEMA_VERSION,
        "run_id":                 run_id,
        "run_started_at":         started_at.isoformat(timespec="seconds"),
        "runtime_seconds":        round(runtime, 3),
        "config_name":            config_name,
        "results_dir":            "",
        "strategy_core_version":  _STRATEGY_CORE_VERSION,

        "config_hash":     config_hash,
        "data_hash":       data_hash,
        "universe_hash":   universe_hash,

        "data_source":     getattr(reader, "data_source", "astock"),
        "data_path":       reader.db_path,
        "data_mtime":      cov.get("db_mtime", ""),
        "data_adjustment": getattr(reader, "adjustment", "hfq"),
        "data_coverage_actual": {
            "min_date":           cov.get("min_date", ""),
            "max_date":           cov.get("max_date", ""),
            "n_codes":            cov.get("n_codes", 0),
            "n_rows_after_dedup": cov.get("n_rows_after_dedup", 0),
            "dedup_count":        cov.get("dedup_count", 0),
            "universe_coverage":  cov.get("universe_coverage", {
                "universe_size":   len(universe),
                "codes_with_data": len(market_data),
                "codes_missing":   [c for c in universe if c not in market_data],
                "missing_count":   len([c for c in universe if c not in market_data]),
            }),
        },
        "data_dedup_applied":           bool(cov.get("dedup_count", 0) > 0),
        "data_concurrent_sync_warning": bool(getattr(reader, "wal_detected", False)),
        "data_wal_detected":            bool(getattr(reader, "wal_detected", False)),
        "data_wal_warning_message":     getattr(reader, "wal_warning_message", ""),

        "benchmark_code":      benchmark_code,
        "benchmark_available": benchmark_available,
        "benchmark_note":      (benchmark_note if not benchmark_available else ""),

        "sector_heat_available": False,
        "sector_heat_mode":      strategy_config.get("sector_heat_mode", "zero"),
        "sector_heat_warning":   "historical sector heat unavailable; sector score set to 0",

        "sample_period_warning": sample_warning,

        "execution":   dict(execution_cfg),
        "performance": performance,
        "portfolio_end": {
            "total_asset":  round(end_total, 6),
            "cash":         round(end_cash, 6),
            "market_value": round(end_mv, 6),
            "n_positions":  len(pf.positions),
        },
        "diagnostics_aggregate": diagnostics_aggregate,

        "pit_universe": (
            {
                "enabled":      True,
                "n_snapshots":  len(universe_by_date),
                "snapshot_dates": sorted(universe_by_date.keys()),
                "union_size":   len(universe),
            } if universe_by_date else
            {"enabled": False}
        ),
    }

    return {
        "summary":        summary,
        "trades":         trades,
        "equity_rows":    equity_rows,
        "positions_rows": positions_rows,
        "logs":           daily_logs,
        "trading_calendar": calendar,
    }
