import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .base import SignalBase, SignalEvent, ScanContext

class F2MomentumReversalSignal(SignalBase):
    """F2: 早盘动量/反转信号 (9:30-10:00)"""
    
    FAMILY = "F2"
    DEFAULT_PARAMS = {
        'momentum_window_min': 15,         # 动量计算窗口(分钟)
        'reversal_threshold_pct': 1.5,     # 反转判定阈值
        'volume_surge_ratio': 2.0,         # 放量倍数
        'trend_confirm_bars': 3,           # 趋势确认根数
    }
    
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        events = []
        df = ctx.df_1min
        
        if len(df) < 30:  # 至少需要 30 分钟数据
            return events
        
        # 只看前 30 分钟 (9:30-10:00)
        morning_df = df.iloc[:30].copy()
        if len(morning_df) < 15:
            return events
        
        params = self.params
        mom_window = params['momentum_window_min']
        rev_thresh = params['reversal_threshold_pct']
        vol_surge = params['volume_surge_ratio']
        confirm_bars = params['trend_confirm_bars']
        
        # 计算前 N 分钟涨跌幅
        first_price = morning_df.iloc[0]['open']
        mom_end_idx = min(mom_window, len(morning_df)) - 1
        mom_price = morning_df.iloc[mom_end_idx]['close']
        mom_chg_pct = (mom_price - first_price) / first_price * 100
        
        # 成交量放大倍数（vs 过去20日日均量/240）
        hist_vol = ctx.params.get('hist_daily_vol', 0)
        avg_vol_per_min = hist_vol / 240 if hist_vol > 0 else 0
        recent_vol = morning_df['vol'].sum()
        vol_ratio = recent_vol / (avg_vol_per_min * mom_window) if avg_vol_per_min > 0 else 0
        
        # === F2_MOM_LONG: 前15min 大涨 + 放量 → 动量延续买入 ===
        if mom_chg_pct >= rev_thresh and vol_ratio >= vol_surge:
            # 触发时刻：动量窗口结束的下一根 bar
            trigger_idx = mom_end_idx
            if trigger_idx + 1 < len(df):
                shares = self._estimate_shares(ctx, trigger_idx)
                evt = self._make_event(ctx, 'F2_MOM_LONG', trigger_idx, direction=1, shares=shares)
                if evt:
                    events.append(evt)
        
        # === F2_MOM_SHORT: 前15min 大跌 + 放量 → 动量延续卖出(反T) ===
        if mom_chg_pct <= -rev_thresh and vol_ratio >= vol_surge:
            trigger_idx = mom_end_idx
            if trigger_idx + 1 < len(df):
                shares = self._estimate_shares(ctx, trigger_idx)
                evt = self._make_event(ctx, 'F2_MOM_SHORT', trigger_idx, direction=-1, shares=shares)
                if evt:
                    events.append(evt)
        
        # === 反转信号：V型/倒V型 ===
        # 需要完整 30 分钟数据
        if len(morning_df) >= 30:
            # 前15min 和 后15min 对比
            first_half = morning_df.iloc[:15]
            second_half = morning_df.iloc[15:30]
            
            first_chg = (first_half.iloc[-1]['close'] - first_half.iloc[0]['open']) / first_half.iloc[0]['open'] * 100
            second_chg = (second_half.iloc[-1]['close'] - second_half.iloc[0]['open']) / second_half.iloc[0]['open'] * 100
            
            first_vol = first_half['vol'].sum()
            second_vol = second_half['vol'].sum()
            
            # F2_REV_LONG: 前15min 大跌，后15min 企稳回升 (V型)
            if (first_chg <= -rev_thresh and 
                second_chg >= rev_thresh * 0.5 and  # 回升幅度至少一半
                second_vol >= first_vol * 0.8):     # 量能不萎缩
                
                trigger_idx = 29  # 30分钟结束时触发
                if trigger_idx + 1 < len(df):
                    shares = self._estimate_shares(ctx, trigger_idx)
                    evt = self._make_event(ctx, 'F2_REV_LONG', trigger_idx, direction=1, shares=shares)
                    if evt:
                        events.append(evt)
            
            # F2_REV_SHORT: 前15min 大涨，后15min 回落 (倒V型)
            if (first_chg >= rev_thresh and 
                second_chg <= -rev_thresh * 0.5 and
                second_vol >= first_vol * 0.8):
                
                trigger_idx = 29
                if trigger_idx + 1 < len(df):
                    shares = self._estimate_shares(ctx, trigger_idx)
                    evt = self._make_event(ctx, 'F2_REV_SHORT', trigger_idx, direction=-1, shares=shares)
                    if evt:
                        events.append(evt)
        
        return events


def create_f2_signal(params: Optional[Dict] = None) -> F2MomentumReversalSignal:
    return F2MomentumReversalSignal(params)