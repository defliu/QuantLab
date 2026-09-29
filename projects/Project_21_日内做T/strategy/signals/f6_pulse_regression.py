import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .base import SignalBase, SignalEvent, ScanContext

class F6PulseRegressionSignal(SignalBase):
    """F6: 日内脉冲回归信号（5min z-score 触发）"""
    
    FAMILY = "F6"
    DEFAULT_PARAMS = {
        'zscore_threshold': 2.0,           # z-score 触发阈值
        'lookback_bars': 48,               # 回望窗口(5min bar数，约4小时)
        'min_holding_bars': 3,             # 最小持仓根数(5min bar)
        'use_5min_data': True,             # 是否使用独立5min数据
    }
    
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        events = []
        
        params = self.params
        z_thresh = params['zscore_threshold']
        lookback = params['lookback_bars']
        use_5min = params['use_5min_data']
        
        # 当日 5min（缺失则由 1min 重采样）
        if use_5min and ctx.df_5min is not None and len(ctx.df_5min) > 0:
            day5 = ctx.df_5min.reset_index(drop=True)
        else:
            day5 = self._resample_to_5min(ctx.df_1min)
        if len(day5) == 0:
            return events
        
        # lookback 必须跨交易日才能取到足够样本（单日只有 48 根 5min bar，
        # lookback=48 + 1 会让当日区间恒为空 → F6 永不触发）。
        # ctx.df_5min_hist = 当日之前的 5min 尾窗，由 scanner 提供。
        hist5 = getattr(ctx, 'df_5min_hist', None)
        if hist5 is None or len(hist5) == 0:
            hist5 = day5.iloc[0:0]
        n_hist = len(hist5)
        full5 = pd.concat([hist5, day5], ignore_index=True) if n_hist else day5
        
        min_periods = max(lookback // 2, 20)
        if len(full5) < min_periods + 2:
            return events
        
        close_5min = full5['close'].astype(float)
        roll_mean = close_5min.rolling(lookback, min_periods=min_periods).mean()
        roll_std = close_5min.rolling(lookback, min_periods=min_periods).std()
        zscore = (close_5min - roll_mean) / roll_std.replace(0, np.nan)
        
        for j in range(len(day5)):
            i = n_hist + j
            if i >= len(full5):
                break
            z = zscore.iloc[i]
            if pd.isna(z):
                continue
            trigger_1min_idx = j * 5
            if trigger_1min_idx + 1 >= len(ctx.df_1min):
                continue
            
            # F6_LONG: z-score < -阈值 (超卖) → 正T买入
            if z <= -z_thresh:
                shares = self._estimate_shares(ctx, trigger_1min_idx)
                evt = self._make_event(ctx, 'F6_LONG', trigger_1min_idx, direction=1, shares=shares)
                if evt:
                    events.append(evt)
            
            # F6_SHORT: z-score > +阈值 (超买) → 反T卖出
            elif z >= z_thresh:
                shares = self._estimate_shares(ctx, trigger_1min_idx)
                evt = self._make_event(ctx, 'F6_SHORT', trigger_1min_idx, direction=-1, shares=shares)
                if evt:
                    events.append(evt)
        
        return events
    
    def _resample_to_5min(self, df_1min: pd.DataFrame) -> pd.DataFrame:
        """1min 重采样为 5min（trade_time 兼容 Timestamp / int HHMM）"""
        df = df_1min.copy()
        tt = df['trade_time']
        if np.issubdtype(tt.dtype, np.datetime64):
            dt = pd.to_datetime(df['trade_date']) + pd.to_timedelta(
                tt.dt.hour * 3600 + tt.dt.minute * 60, unit='s')
        else:
            hh = (tt // 100).astype(int)
            mm = (tt % 100).astype(int)
            dt = pd.to_datetime(df['trade_date']) + pd.to_timedelta(hh * 3600 + mm * 60, unit='s')
        df['datetime'] = dt
        df = df.set_index('datetime')
        
        ohlc = df['close'].resample('5min').ohlc()
        vol = df['vol'].resample('5min').sum()
        amt = df['amount'].resample('5min').sum()
        
        df_5min = pd.DataFrame({
            'open': ohlc['open'],
            'high': ohlc['high'],
            'low': ohlc['low'],
            'close': ohlc['close'],
            'vol': vol,
            'amount': amt
        }).dropna()
        
        return df_5min.reset_index(drop=True)
    
    def _map_5min_to_1min(self, df_1min: pd.DataFrame, df_5min: pd.DataFrame) -> List[int]:
        """建立 5min bar 到 1min bar 索引的映射"""
        mapping = []
        
        # 简单映射：每5个1min bar对应1个5min bar
        # 实际应该按时间对齐，这里简化处理
        for i in range(len(df_5min)):
            idx_1min = i * 5
            if idx_1min < len(df_1min):
                mapping.append(idx_1min)
            else:
                mapping.append(len(df_1min) - 1)
        
        return mapping


def create_f6_signal(params: Optional[Dict] = None) -> F6PulseRegressionSignal:
    return F6PulseRegressionSignal(params)