import pandas as pd
import numpy as np
import os
import yaml
import time
from pathlib import Path
from typing import List, Dict, Optional
from tqdm import tqdm
import gc
import warnings
warnings.filterwarnings('ignore')

from strategy.signals.base import SignalEvent, ScanContext, load_config, get_cost_model
from strategy.signals.f1_auction import create_f1_signal
from strategy.signals.f2_momentum_reversal import create_f2_signal
from strategy.signals.f3_vwap_dev import create_f3_signal
from strategy.signals.f4_volume_structure import create_f4_signal
from strategy.signals.f5_gap_fill import create_f5_signal
from strategy.signals.f6_pulse_regression import create_f6_signal


class BatchStats(object):
    """单批次的统计信息（preload / scan 分相耗时 + 内存峰值）"""
    def __init__(self, batch_idx=0, n_stocks=0, preload_sec=0.0,
                 scan_sec=0.0, n_events=0, peak_mem_mb=0.0, total_sec=0.0):
        self.batch_idx = batch_idx
        self.n_stocks = n_stocks
        self.preload_sec = preload_sec
        self.scan_sec = scan_sec
        self.n_events = n_events
        self.peak_mem_mb = peak_mem_mb
        self.total_sec = total_sec


class SignalScanner:
    """主扫描器：按股票分批预加载，逐批次遍历日期，输出结果

    【内存模型】
    - 一次性 preload_all_stocks（3055 只 × ~22MB）= 67GB，直接 OOM
    - 本方案：pool 按 BATCH_SIZE 切片，每批只 preload 该批股票
      （日期范围限制到 [start-lookback, end+lookback] 以降低内存）
    - 循环顺序：外层 batch → 内层 date → 最内 per-stock signal
    - 每批结束 del + gc.collect 释放

    【二次缓存】auction_o / auction_c 加 ts_code 预分组缓存，
    避免每 stock-day 都跑 (df['ts_code']==code) & (df['trade_date']==int)
    7.3M 次的布尔扫描，改成 O(1) dict 查表。
    """

    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self.criteria = self.config['midvol_criteria']
        self.cost_base = self.config['cost_model']
        self.eval_cfg = self.config['evaluation']

        # 六族信号计算器
        self.signals = {
            'F1': create_f1_signal(self.criteria.get('f1_params', {})),
            'F2': create_f2_signal(self.criteria.get('f2_params', {})),
            'F3': create_f3_signal(self.criteria.get('f3_params', {})),
            'F4': create_f4_signal(self.criteria.get('f4_params', {})),
            'F5': create_f5_signal(self.criteria.get('f5_params', {})),
            'F6': create_f6_signal(self.criteria.get('f6_params', {})),
        }

        # 数据路径
        self.data_1min_dir = Path("d:/astock/min_1min")
        self.data_5min_dir = Path("d:/astock/min_5min")
        self.auction_o_file = Path("d:/astock/auction/stock_auction_o_daily.parquet")
        self.auction_c_file = Path("d:/astock/auction/stock_auction_c_daily.parquet")
        self.basic_file = Path("d:/astock/basic/stock_basic.parquet")

        # 参考数据缓存
        self._auction_o_cache = None
        self._auction_c_cache = None
        self._auction_o_by_code = None   # 二次缓存：{ts_code: df}
        self._auction_c_by_code = None
        self._stock_basic_cache = None

        # 按批股票数据缓存：{ts_code: {'1min': df, '5min': df}}
        self._data_cache = {}

        # 中波股票池
        self.midvol_pool = []

        # 扫描日期范围
        self._scan_start = None
        self._scan_end = None

        # 批次参数（生产 300 × 2400 天 ~ 14GB/批，可用 18.5GB 上限内；
        # 若内存紧张可下调到 100）
        self.BATCH_SIZE = 300
        self.PRELOAD_LOOKBACK_DAYS = 35
        self.PRELOAD_FORWARD_DAYS = 5
        self.F6_HIST_BARS = 60   # F6 lookback(48) 的前置 5min 尾窗根数
        self._batch_stats = BatchStats()

        # 错误可见性：杜绝 FAIL-QUIET（吞异常导致 0 事件还"扫描完成"）
        self._ctx_errors = {}      # {异常串: 次数}
        self._sig_errors = {}      # {family: {异常串: 次数}}
        self._err_print_budget = 20  # 每类异常最多打印条数，避免刷屏

    # ------------------------------------------------------------------
    # 加载 & 建池
    # ------------------------------------------------------------------

    def load_reference_data(self):
        """加载参考数据（集合竞价、股票基础信息）"""
        print("加载参考数据...")

        if self.auction_o_file.exists():
            self._auction_o_cache = pd.read_parquet(self.auction_o_file)
            if isinstance(self._auction_o_cache.index, pd.MultiIndex):
                self._auction_o_cache = self._auction_o_cache.reset_index()
            if 'trade_date' in self._auction_o_cache.columns:
                # 转 int YYYYMMDD（F1/F5 信号按 int 比较，如 int('20190102')）
                self._auction_o_cache['trade_date'] = pd.to_datetime(
                    self._auction_o_cache['trade_date']).dt.strftime('%Y%m%d').astype(int)
            print("  开盘集合竞价: {} 条 (cols={})".format(
                len(self._auction_o_cache),
                self._auction_o_cache.columns.tolist()[:5]))
        else:
            print("  警告: {} 不存在".format(self.auction_o_file))
            self._auction_o_cache = pd.DataFrame()

        if self.auction_c_file.exists():
            self._auction_c_cache = pd.read_parquet(self.auction_c_file)
            if isinstance(self._auction_c_cache.index, pd.MultiIndex):
                self._auction_c_cache = self._auction_c_cache.reset_index()
            if 'trade_date' in self._auction_c_cache.columns:
                self._auction_c_cache['trade_date'] = pd.to_datetime(
                    self._auction_c_cache['trade_date']).dt.strftime('%Y%m%d').astype(int)
            print("  收盘集合竞价: {} 条 (cols={})".format(
                len(self._auction_c_cache),
                self._auction_c_cache.columns.tolist()[:5]))
        else:
            self._auction_c_cache = pd.DataFrame()

        if self.basic_file.exists():
            self._stock_basic_cache = pd.read_parquet(self.basic_file).set_index('ts_code')
            print("  股票基础信息: {} 只".format(len(self._stock_basic_cache)))
        else:
            print("  警告: {} 不存在".format(self.basic_file))
            self._stock_basic_cache = pd.DataFrame()

        self._build_auction_cache()

    def build_midvol_pool(self, trade_date: str = None) -> List[str]:
        """构建中波股票池"""
        if self.midvol_pool:
            return self.midvol_pool

        print("构建中波股票池...")

        detail_file = Path("d:/QuantLab/projects/Project_21_日内做T/results/data_health_check_detail.parquet")
        if not detail_file.exists():
            raise FileNotFoundError("请先运行 data_health_check.py 生成体检明细")

        df_detail = pd.read_parquet(detail_file)

        amp_min = self.criteria['amplitude']['min_pct']
        amp_max = self.criteria['amplitude']['max_pct']
        min_amt = self.criteria['liquidity']['min_daily_amount_cny'] / 1e8
        min_vol = self.criteria['liquidity']['min_daily_vol_hands'] / 1e4
        min_days = self.criteria['listing']['min_listing_days']

        pool = df_detail[
            (df_detail['amplitude_median'] >= amp_min) &
            (df_detail['amplitude_median'] <= amp_max) &
            (df_detail['turnover_median'] >= min_amt) &
            (df_detail['vol_median'] >= min_vol) &
            (df_detail['n_days'] >= min_days)
        ].copy()

        if 'name' in pool.columns:
            pool = pool[~pool['name'].str.contains('ST|退', na=False)]

        if 'market' in pool.columns:
            pool = pool[pool['market'] != '北交所']

        self.midvol_pool = pool['ts_code'].tolist()
        print("  中波股票池: {} 只".format(len(self.midvol_pool)))

        return self.midvol_pool

    # ------------------------------------------------------------------
    # Auction 二次缓存：按 ts_code 预分组，避免 7.3M 次布尔扫描
    # ------------------------------------------------------------------

    def _build_auction_cache(self):
        """预分组 auction_o / auction_c 为 {ts_code: df}，加速 per-stock-day 查询"""
        t0 = time.time()
        if self._auction_o_cache is not None and len(self._auction_o_cache) > 0 and 'ts_code' in self._auction_o_cache.columns:
            self._auction_o_by_code = dict(list(self._auction_o_cache.groupby('ts_code')))
        else:
            self._auction_o_by_code = {}
        if self._auction_c_cache is not None and len(self._auction_c_cache) > 0 and 'ts_code' in self._auction_c_cache.columns:
            self._auction_c_by_code = dict(list(self._auction_c_cache.groupby('ts_code')))
        else:
            self._auction_c_by_code = {}
        print("  auction 二次缓存: O={} 只, C={} 只 ({}s)".format(
            len(self._auction_o_by_code),
            len(self._auction_c_by_code),
            "{:.1f}".format(time.time() - t0)))

    # ------------------------------------------------------------------
    # 内部小工具
    # ------------------------------------------------------------------

    def _load_1min(self, ts_code, start_date, end_date):
        f = self.data_1min_dir / ("{}.parquet".format(ts_code))
        if not f.exists():
            return None
        df = pd.read_parquet(f)
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()
        if 'trade_date' not in df.columns:
            return None
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        # 先过滤再排序：避免对全量 1.3M 行排序（filter 后仅剩 30 天 ~ 1.5 万行）
        if start_date is not None and end_date is not None:
            df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)].copy()
        if len(df) > 1:
            df = df.sort_values(['trade_date', 'trade_time']).reset_index(drop=True)
        return df

    @staticmethod
    def _empty_df():
        return pd.DataFrame()

    @staticmethod
    def _filter_auction_by_date(auction_df, trade_date_int, date_col):
        """按 trade_date 过滤 auction 子 df，兼容 int / datetime 列"""
        if auction_df is None or len(auction_df) == 0 or date_col not in auction_df.columns:
            return pd.DataFrame()
        dtype = auction_df[date_col].dtype
        try:
            if np.issubdtype(dtype, np.integer):
                return auction_df[auction_df[date_col] == trade_date_int]
            elif np.issubdtype(dtype, np.datetime64):
                return auction_df[auction_df[date_col] ==
                                  pd.to_datetime(str(trade_date_int), format='%Y%m%d')]
            else:
                return auction_df[auction_df[date_col] == trade_date_int]
        except Exception:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # 批次预加载 & 清空
    # ------------------------------------------------------------------

    def _preload_batch(self, batch_stocks, start_date, end_date):
        """预加载一个批次的股票到内存，返回 (n_rows_loaded, n_stocks_cached)"""
        t0 = time.time()
        n_loaded = 0
        n_cached_before = len(self._data_cache)
        for ts_code in batch_stocks:
            if ts_code in self._data_cache:
                continue
            df_1min = self._load_1min(ts_code, start_date, end_date)
            entry = {'1min': df_1min, '5min': None}
            # 5min
            f5 = self.data_5min_dir / ("{}.parquet".format(ts_code))
            if f5.exists():
                try:
                    df5 = pd.read_parquet(f5)
                    if isinstance(df5.index, pd.MultiIndex):
                        df5 = df5.reset_index()
                    if 'trade_date' in df5.columns:
                        df5['trade_date'] = pd.to_datetime(df5['trade_date'])
                    df5 = df5.sort_values(['trade_date', 'trade_time']).reset_index(drop=True)
                    if start_date is not None and end_date is not None:
                        df5 = df5[(df5['trade_date'] >= start_date) & (df5['trade_date'] <= end_date)].copy()
                    entry['5min'] = df5
                    n_loaded += len(df5)
                except Exception:
                    pass
            if df_1min is not None:
                n_loaded += len(df_1min)
                # 预计算逐日成交量的前向 20 日滚动均值（原实现每个 stock-day 都对
                # 整段历史做 unique+isin+groupby，是 get_context_for 的主要耗时）
                if len(df_1min) > 0 and 'vol' in df_1min.columns:
                    grp = df_1min.groupby('trade_date', sort=True)['vol'].sum()
                    hv = grp.shift(1).rolling(20, min_periods=1).mean()
                    entry['hv_dates'] = grp.index.values
                    entry['hv_vals'] = hv.values
            self._data_cache[ts_code] = entry
        self._batch_stats.preload_sec = time.time() - t0
        print("  [preload] {} 只 | {}s | {} 行 | cache +{} -> {}".format(
            len(batch_stocks),
            "{:.1f}".format(self._batch_stats.preload_sec),
            n_loaded,
            n_cached_before,
            len(self._data_cache)))
        return self._batch_stats.preload_sec, n_loaded

    def _clear_data_cache(self):
        self._data_cache.clear()
        gc.collect()

    # ------------------------------------------------------------------
    # per-stock-day 数据加载（使用排序 + searchsorted）
    # ------------------------------------------------------------------

    @staticmethod
    def _to_ts(trade_date):
        """YYYYMMDD (str/int) → Timestamp。

        历史坑：pd.to_datetime('20190102.0') 会抛 DateParseError
        (year 20190102 is out of range)，导致 get_context_for 全量返回 None、
        扫描 0 事件却"正常完成"。必须显式指定 format。
        """
        if isinstance(trade_date, str):
            return pd.to_datetime(trade_date, format='%Y%m%d')
        if isinstance(trade_date, (int, np.integer)):
            return pd.to_datetime(str(int(trade_date)), format='%Y%m%d')
        return pd.to_datetime(trade_date)

    def get_context_for(self, ts_code, trade_date):
        """为一个 stock-day 构造 ScanContext。数据来自 self._data_cache（本批次内已 preload）"""
        try:
            entry = self._data_cache.get(ts_code)
            if entry is None or entry['1min'] is None:
                return None
            df_1min_all = entry['1min']
            if df_1min_all is None or len(df_1min_all) == 0:
                return None

            target = self._to_ts(trade_date)
            td_arr = df_1min_all['trade_date'].values
            # searchsorted on datetime64[ns] values
            left = np.searchsorted(td_arr, np.datetime64(target.to_datetime64()), side='left')
            right = np.searchsorted(td_arr, np.datetime64(target.to_datetime64()), side='right')
            day_1min = df_1min_all.iloc[left:right].reset_index(drop=True)

            if len(day_1min) < 30:
                return None

            day_5min = None
            hist_5min = pd.DataFrame()
            df_5min_all = entry.get('5min')
            if df_5min_all is not None and len(df_5min_all) > 0:
                td5_arr = df_5min_all['trade_date'].values
                l5 = np.searchsorted(td5_arr, np.datetime64(target.to_datetime64()), side='left')
                r5 = np.searchsorted(td5_arr, np.datetime64(target.to_datetime64()), side='right')
                day_5min = df_5min_all.iloc[l5:r5].reset_index(drop=True)
                if l5 > 0:
                    hist_5min = df_5min_all.iloc[max(0, int(l5) - self.F6_HIST_BARS):l5].reset_index(drop=True)

            trade_date_int = int(trade_date)
            # Auction 数据：优先走 ts_code 二次缓存
            auction_o_all = self._auction_o_by_code.get(ts_code, pd.DataFrame())
            auction_c_all = self._auction_c_by_code.get(ts_code, pd.DataFrame())
            date_col_o = 'trade_date' if 'trade_date' in auction_o_all.columns else ('tradedate' if 'tradedate' in auction_o_all.columns else None)
            date_col_c = 'trade_date' if 'trade_date' in auction_c_all.columns else ('tradedate' if 'tradedate' in auction_c_all.columns else None)
            day_auction_o = self._filter_auction_by_date(auction_o_all, trade_date_int, date_col_o)
            day_auction_c = self._filter_auction_by_date(auction_c_all, trade_date_int, date_col_c)

            prev_close = self._get_prev_close_fast(ts_code, target, df_1min_all, td_arr)
            hist_daily_vol = self._lookup_hist_vol(entry, target)

            cost_model = get_cost_model(self.config, trade_date)

            ctx = ScanContext(
                ts_code=ts_code,
                trade_date=trade_date,
                df_1min=day_1min,
                df_5min=day_5min,
                df_auction_o=day_auction_o,
                df_auction_c=day_auction_c,
                prev_close=prev_close,
                params={'hist_daily_vol': hist_daily_vol},
                cost_model=cost_model,
                df_auction_o_hist=auction_o_all,
                df_5min_hist=hist_5min,
            )
            return ctx
        except Exception as ex:
            self._record_ctx_error(ts_code, trade_date, ex)
            return None

    def _record_ctx_error(self, ts_code, trade_date, ex):
        key = "{}: {}".format(type(ex).__name__, str(ex)[:120])
        n = self._ctx_errors.get(key, 0) + 1
        self._ctx_errors[key] = n
        if n <= self._err_print_budget:
            print("  [ctx-ERR] {} {} -> {}".format(ts_code, trade_date, key))

    def _record_sig_error(self, family, ex):
        bucket = self._sig_errors.setdefault(family, {})
        key = "{}: {}".format(type(ex).__name__, str(ex)[:120])
        n = bucket.get(key, 0) + 1
        bucket[key] = n
        if n <= self._err_print_budget:
            print("  [sig-ERR][{}] {}".format(family, key))

    def _print_error_summary(self, n_events):
        if not self._ctx_errors and not self._sig_errors:
            print("错误计数: 无")
            return
        print("错误计数（ctx 异常 {} 种 / 信号异常族 {} 个）:".format(
            len(self._ctx_errors), len(self._sig_errors)))
        for key, n in sorted(self._ctx_errors.items(), key=lambda kv: -kv[1])[:10]:
            print("  [ctx] {:>8} x  {}".format(n, key))
        for fam, bucket in self._sig_errors.items():
            for key, n in sorted(bucket.items(), key=lambda kv: -kv[1])[:5]:
                print("  [sig][{}] {:>8} x  {}".format(fam, n, key))
        if n_events == 0:
            print("  [FAIL-LOUD] 总事件数 = 0：本次扫描不可用于裁决，请先修上面的异常")

    @staticmethod
    def _get_prev_close_fast(ts_code, target_date, df_1min_all, td_arr=None):
        if td_arr is None:
            td_arr = df_1min_all['trade_date'].values
        if len(td_arr) == 0:
            return np.nan
        left = np.searchsorted(td_arr, np.datetime64(target_date.to_datetime64()), side='left')
        if left == 0:
            return np.nan
        prev_idx = left - 1
        return float(df_1min_all.iloc[prev_idx]['close'])

    @staticmethod
    def _lookup_hist_vol(entry, target_date):
        """O(log n) 取该股截至 target 前一交易日的 20 日均量（_preload_batch 预计算）"""
        dates = entry.get('hv_dates')
        vals = entry.get('hv_vals')
        if dates is None or vals is None or len(dates) == 0:
            return 0.0
        idx = int(np.searchsorted(dates, np.datetime64(target_date.to_datetime64()),
                                  side='right')) - 1
        if idx < 0:
            return 0.0
        v = vals[idx]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 0.0
        return float(v)

    @staticmethod
    def _get_hist_daily_vol_fast(ts_code, target_date, df_1min_all, td_arr=None):
        if td_arr is None:
            td_arr = df_1min_all['trade_date'].values
        if len(td_arr) == 0:
            return 0.0
        left = np.searchsorted(td_arr, np.datetime64(target_date.to_datetime64()), side='left')
        if left == 0:
            return 0.0
        hist = df_1min_all.iloc[:left]
        unique_dates = hist['trade_date'].unique()
        if len(unique_dates) == 0:
            return 0.0
        recent = np.sort(unique_dates)[-20:]
        mask = np.isin(hist['trade_date'].values, recent)
        subset = hist[mask]
        if len(subset) == 0:
            return 0.0
        grp = subset.groupby('trade_date', sort=False)['vol'].sum()
        return float(grp.mean())

    def scan_one_day_for_stocks(self, trade_date, stocks, verbose=False):
        """对指定 stocks 在指定 trade_date 运行 6 族信号。返回 events。"""
        events = []
        for ts_code in stocks:
            ctx = self.get_context_for(ts_code, trade_date)
            if ctx is None:
                continue
            for fam, sig in self.signals.items():
                try:
                    evs = sig.compute_signals(ctx)
                    if evs:
                        events.extend(evs)
                except Exception as ex:
                    self._record_sig_error(fam, ex)
        return events

    def scan_one_day(self, trade_date, stocks=None):
        """旧接口兼容：stocks 为 None 时扫整池"""
        if stocks is None:
            stocks = self.build_midvol_pool()
        return self.scan_one_day_for_stocks(trade_date, stocks)

    # ------------------------------------------------------------------
    # 主扫描入口
    # ------------------------------------------------------------------

    def scan_period(self, start_date: str, end_date: str) -> pd.DataFrame:
        """按股票分批预加载，每批遍历所有交易日，最后累加输出。"""
        print("\n" + "=" * 66)
        print("扫描时期: {} - {}  |  BATCH_SIZE={}".format(start_date, end_date, self.BATCH_SIZE))
        print("=" * 66)

        pool = self.build_midvol_pool()
        calendar = self._get_trading_calendar(start_date, end_date)
        print("交易日数: {} | 股票池: {} 只".format(len(calendar), len(pool)))

        n_batches = (len(pool) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        # 统一格式化为 YYYYMMDD 字符串（兼容 numpy.datetime64 / pandas.Timestamp）
        date_strs = []
        for d in calendar:
            if isinstance(d, str):
                date_strs.append(d)
            else:
                date_strs.append(pd.to_datetime(d).strftime('%Y%m%d'))
        preload_start = pd.Timestamp(start_date) - pd.Timedelta(days=self.PRELOAD_LOOKBACK_DAYS)
        preload_end = pd.Timestamp(end_date) + pd.Timedelta(days=self.PRELOAD_FORWARD_DAYS)

        total_stock_days = 0
        all_events = []
        all_batch_stats = []
        t_start = time.time()

        for bi in range(n_batches):
            s = bi * self.BATCH_SIZE
            e = min(s + self.BATCH_SIZE, len(pool))
            batch_stocks = pool[s:e]
            batch_stock_days = len(batch_stocks) * len(calendar)
            total_stock_days += batch_stock_days

            batch_id = "B{:02d}".format(bi + 1)
            print("\n" + "-" * 66)
            print("[{}] {}/{} | 股票 {}-{} ({} 只) | 计划 {} 天 × {} 股 = {:,} stock-day".format(
                batch_id, bi + 1, n_batches, s + 1, e,
                len(batch_stocks), len(calendar), len(batch_stocks), batch_stock_days))
            print("-" * 66)

            preload_t, n_rows_loaded = self._preload_batch(batch_stocks, preload_start, preload_end)

            # 扫描
            t_scan = time.time()
            for di, dstr in enumerate(tqdm(date_strs, desc="  " + batch_id, leave=False)):
                try:
                    evs = self.scan_one_day_for_stocks(dstr, batch_stocks)
                    all_events.extend(evs)
                except Exception as ex:
                    print("  {} 扫描异常: {}".format(dstr, ex))
            scan_t = time.time() - t_scan
            batch_events = len(all_events)
            print("  [scan] {}s | 平均 {:.3f} ms/stock-day | 累计 events={:,}".format(
                "{:.1f}".format(scan_t),
                scan_t * 1000.0 / max(batch_stock_days, 1),
                batch_events))

            batch_total = preload_t + scan_t
            # 内存
            mem_mb = self._memory_check()

            # ETA 估算：stock-day 速度 = 累计 stock-day / 累计耗时
            elapsed_so_far = time.time() - t_start
            speed = total_stock_days / max(elapsed_so_far, 0.001)   # stock-day/s
            ms_per_day = (1000.0 / max(speed, 0.000001)) if speed > 0 else -1.0
            remaining_stock_days = (len(pool) - e) * len(calendar)
            remaining_time = (remaining_stock_days / speed) if speed > 0 else -1.0
            remaining_batches = n_batches - 1 - bi

            self._print_batch_summary(
                batch_id, len(batch_stocks), preload_t, scan_t,
                batch_total, batch_stock_days, mem_mb,
                ms_per_day, remaining_batches, remaining_time)

            all_batch_stats.append(BatchStats(
                batch_idx=bi + 1, n_stocks=len(batch_stocks),
                preload_sec=preload_t, scan_sec=scan_t,
                n_events=batch_events, peak_mem_mb=mem_mb,
                total_sec=batch_total))

            # 清空缓存
            self._clear_data_cache()

            # 断点保险：每批落一次中间产物（多小时任务，崩了不归零）
            # 文件名带切片日期，多进程并行扫描时互不覆盖
            if all_events:
                try:
                    partial = pd.DataFrame([ev.to_dict() for ev in all_events])
                    partial_path = Path("d:/QuantLab/projects/Project_21_日内做T/results/"
                                        "signal_scan_partial_{}_{}.parquet".format(
                                            start_date, end_date))
                    partial.to_parquet(partial_path, index=False)
                    print("  [partial] 已落盘 {} 条事件".format(len(partial)))
                except Exception as ex:
                    print("  [partial] 落盘失败: {}".format(ex))

        # 汇总
        gc.collect()
        if all_events:
            df = pd.DataFrame([e.to_dict() for e in all_events])
        else:
            df = pd.DataFrame()

        total_elapsed = time.time() - t_start
        print("\n" + "=" * 66)
        print("扫描完成")
        print("=" * 66)
        print("总耗时: {} | 平均每 stock-day: {:.3f} ms".format(
            self._human_time(total_elapsed),
            total_elapsed * 1000.0 / max(total_stock_days, 1)))
        print("总 stock-day: {:,} | 总信号: {:,}".format(total_stock_days, len(all_events)))
        self._print_batch_table(all_batch_stats)
        self._print_error_summary(len(all_events))
        return df

    def _print_batch_summary(self, batch_id, n_stocks, preload_t, scan_t,
                             total, n_stock_days, peak_mem_mb,
                             ms_per_day, remaining_batches, remaining_time):
        print("  [{} summary] {} stocks | preload={:.1f}s + scan={:.1f}s = {:.1f}s total".format(
            batch_id, n_stocks, preload_t, scan_t, total))
        print("  [ETA] 平均 {:.3f} ms/stock-day | 剩余 {} 批 | 预计还需 {}".format(
            ms_per_day,
            remaining_batches,
            self._human_time(remaining_time) if remaining_time > 0 else "N/A"))

    def _print_batch_table(self, stats_list):
        if not stats_list:
            return
        print("\n批次汇总:")
        print("  {:<4} | {:>4} | {:>8} | {:>8} | {:>8} | {:>10}".format(
            "B", "n", "preload", "scan", "total", "peak_Mem"))
        for b in stats_list:
            print("  {:<4} | {:>4} | {:>8.1f} | {:>8.1f} | {:>8.1f} | {:>10.1f}".format(
                "B{:02d}".format(b.batch_idx), b.n_stocks,
                b.preload_sec, b.scan_sec, b.total_sec, b.peak_mem_mb))

    # ------------------------------------------------------------------
    # 交易日历 & 内存探测
    # ------------------------------------------------------------------

    def _get_trading_calendar(self, start_date, end_date):
        """从第一只股票的 1min 数据推导交易日历"""
        if not self.midvol_pool:
            return []
        sample = self.data_1min_dir / ("{}.parquet".format(self.midvol_pool[0]))
        if not sample.exists():
            return pd.date_range(start_date, end_date, freq='B').tolist()
        df = pd.read_parquet(sample)
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date'])
        dates = sorted(pd.unique(df['trade_date']))
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        return [d for d in dates if start <= d <= end]

    @staticmethod
    def _memory_check():
        """返回当前进程 RSS (MB)；无 psutil 时尝试 resource，仍失败返回 0.0"""
        try:
            import psutil
            import os
            mem = psutil.Process(os.getpid()).memory_info()
            return mem.rss / 1024.0 / 1024.0
        except Exception:
            try:
                import resource
                r = resource.getrusage(resource.RUSAGE_SELF)
                # Linux ru_maxrss 单位是 KB
                return r.ru_maxrss / 1024.0
            except Exception:
                return 0.0

    @staticmethod
    def _human_time(seconds):
        if seconds < 60:
            return "{:.1f}s".format(seconds)
        m = int(seconds // 60)
        s = seconds - m * 60
        if m < 60:
            return "{}m{:.0f}s".format(m, s)
        h = m // 60
        m = m % 60
        return "{}h{:.0f}m{:.0f}s".format(h, m, s)


def main(argv=None):
    """生产模式：默认 2019-2024 全周期扫描；--start/--end 可切片多进程并行"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20190101')
    ap.add_argument('--end', default='20241231')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)

    config_path = "d:/QuantLab/projects/Project_21_日内做T/config/midvol_criteria.yaml"
    scanner = SignalScanner(config_path)
    scanner.load_reference_data()
    df_results = scanner.scan_period(args.start, args.end)

    output_dir = Path("d:/QuantLab/projects/Project_21_日内做T/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = Path(args.out) if args.out else (
        output_dir / "signal_scan_{}_{}.parquet".format(args.start, args.end))
    df_results.to_parquet(output_file, index=False)
    print("\n扫描完成，结果保存: {}".format(output_file))
    print("总信号数: {}".format(len(df_results)))
    if len(df_results) > 0:
        print("覆盖交易日: {}".format(df_results['trade_date'].nunique()))
        print("覆盖股票数: {}".format(df_results['ts_code'].nunique()))
        print("信号族分布:")
        print(df_results['family'].value_counts())
        print("信号类型分布:")
        print(df_results['signal_type'].value_counts())
        print("方向分布:")
        print(df_results['direction'].value_counts())
    return 0 if len(df_results) > 0 else 3


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # 快速测试：5 个交易日
        config_path = "d:/QuantLab/projects/Project_21_日内做T/config/midvol_criteria.yaml"
        scanner = SignalScanner(config_path)
        scanner.load_reference_data()

        t0 = time.time()
        df_results = scanner.scan_period("20190102", "20190108")
        elapsed = time.time() - t0

        print("\n[测试模式] 5 日扫描完成 | 总耗时 {}".format(scanner._human_time(elapsed)))
        print("总信号数: {}".format(len(df_results)))
        if len(df_results) > 0:
            print("覆盖交易日: {}".format(df_results['trade_date'].nunique()))
            print("覆盖股票数: {}".format(df_results['ts_code'].nunique()))
        print("测试通过")
    else:
        sys.exit(main(sys.argv[1:]) or 0)
