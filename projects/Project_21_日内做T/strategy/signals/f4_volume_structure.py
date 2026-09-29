import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .base import SignalBase, SignalEvent, ScanContext

class F4VolumeStructureSignal(SignalBase):
    """F4: 开盘30分钟量能结构信号"""
    
    FAMILY = "F4"
    DEFAULT_PARAMS = {
        'front_heavy_ratio': 1.5,          # 前15min/后15min 量比阈值
        'price_volume_corr_threshold': 0.3, # 价量相关性阈值
        'min_bars': 25,                    # 最小有效bar数
        'back_heavy_threshold': 0.67,      # 后重阈值 (1/1.5)
    }
    
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        events = []
        df = ctx.df_1min
        
        if len(df) < 30:
            return events
        
        params = self.params
        front_heavy = params['front_heavy_ratio']
        back_heavy = params['back_heavy_threshold']
        corr_thresh = params['price_volume_corr_threshold']
        min_bars = params['min_bars']
        
        # 前30分钟数据
        morning_df = df.iloc[:30].copy()
        if len(morning_df) < min_bars:
            return events
        
        # 分前15min / 后15min
        first_half = morning_df.iloc[:15]
        second_half = morning_df.iloc[15:30]
        
        first_vol = first_half['vol'].sum()
        second_vol = second_half['vol'].sum()
        
        if first_vol <= 0 or second_vol <= 0:
            return events
        
        vol_ratio = first_vol / second_vol
        
        # 价量相关性（前30min）
        price_chg = morning_df['close'].pct_change().dropna()
        vol_series = morning_df['vol'].iloc[1:]  # 对齐
        if len(price_chg) > 5 and len(vol_series) > 5:
            pv_corr = price_chg.corr(vol_series)
        else:
            pv_corr = 0
        
        # 前半段涨跌幅
        first_chg = (first_half.iloc[-1]['close'] - first_half.iloc[0]['open']) / first_half.iloc[0]['open'] * 100
        second_chg = (second_half.iloc[-1]['close'] - second_half.iloc[0]['open']) / second_half.iloc[0]['open'] * 100
        
        trigger_idx = 29  # 30分钟结束触发
        if trigger_idx + 1 >= len(df):
            return events
        
        # === F4_FRONT_LONG: 前重放量 + 价量正相关 + 上涨 → 买入 ===
        if (vol_ratio >= front_heavy and 
            pv_corr >= corr_thresh and 
            first_chg > 0):
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F4_FRONT_LONG', trigger_idx, direction=1, shares=shares)
            if evt:
                events.append(evt)
        
        # === F4_FRONT_SHORT: 前重放量 + 价量正相关 + 下跌 → 卖出(反T) ===
        if (vol_ratio >= front_heavy and 
            pv_corr >= corr_thresh and 
            first_chg < 0):
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F4_FRONT_SHORT', trigger_idx, direction=-1, shares=shares)
            if evt:
                events.append(evt)
        
        # === F4_BACK_LONG: 后重放量 + 价格企稳/上涨 → 买入 ===
        if (vol_ratio <= back_heavy and 
            second_chg >= 0 and
            second_vol > first_vol * 0.5):  # 后半段有实质放量
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F4_BACK_LONG', trigger_idx, direction=1, shares=shares)
            if evt:
                events.append(evt)
        
        # === F4_BACK_SHORT: 后重放量 + 价格下跌 → 卖出(反T) ===
        if (vol_ratio <= back_heavy and 
            second_chg < 0 and
            second_vol > first_vol * 0.5):
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F4_BACK_SHORT', trigger_idx, direction=-1, shares=shares)
            if evt:
                events.append(evt)
        
        return events


def create_f4_signal(params: Optional[Dict] = None) -> F4VolumeStructureSignal:
    return F4VolumeStructureSignal(params)