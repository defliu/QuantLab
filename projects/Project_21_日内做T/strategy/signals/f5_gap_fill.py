import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .base import SignalBase, SignalEvent, ScanContext

class F5GapFillSignal(SignalBase):
    """F5: 隔夜缺口回补信号"""
    
    FAMILY = "F5"
    DEFAULT_PARAMS = {
        'gap_threshold_pct': 1.5,          # 缺口最小幅度
        'fill_window_min': 60,             # 回补观察窗口(分钟，10:30前)
        'partial_fill_ratio': 0.5,         # 部分回补比例
        'min_bars_after_open': 5,          # 开盘后最小bar数
    }
    
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        events = []
        df = ctx.df_1min

        params = self.params
        if len(df) < params.get('fill_window_min', 60):
            return events

        gap_thresh = params['gap_threshold_pct']
        fill_window = params['fill_window_min']
        fill_ratio = params['partial_fill_ratio']
        min_bars = params['min_bars_after_open']
        
        # 隔夜跳空
        prev_close = ctx.prev_close
        if prev_close <= 0:
            return events
        
        open_price = df.iloc[0]['open']
        gap_pct = (open_price - prev_close) / prev_close * 100
        
        # 缺口不足，不触发
        if abs(gap_pct) < gap_thresh:
            return events
        
        # 观察窗口内的价格极值
        window_df = df.iloc[min_bars:min_bars + fill_window]
        if len(window_df) < 10:
            return events
        
        high_in_window = window_df['high'].max()
        low_in_window = window_df['low'].min()
        
        # 触发时刻：观察窗口结束
        trigger_idx = min_bars + fill_window - 1
        if trigger_idx + 1 >= len(df):
            return events
        
        # === F5_GAP_DOWN_FILL: 向下跳空，开盘回升回补 ≥ 50% → 正T买入 ===
        if gap_pct <= -gap_thresh:
            # 回补幅度 = (窗口最高价 - 开盘价) / (前收 - 开盘价)
            fill_extent = (high_in_window - open_price) / (prev_close - open_price)
            if fill_extent >= fill_ratio:
                shares = self._estimate_shares(ctx, trigger_idx)
                evt = self._make_event(ctx, 'F5_GAP_DOWN_FILL', trigger_idx, direction=1, shares=shares)
                if evt:
                    events.append(evt)
        
        # === F5_GAP_UP_FILL: 向上跳空，开盘回落回补 ≥ 50% → 反T卖出 ===
        if gap_pct >= gap_thresh:
            # 回补幅度 = (开盘价 - 窗口最低价) / (开盘价 - 前收)
            fill_extent = (open_price - low_in_window) / (open_price - prev_close)
            if fill_extent >= fill_ratio:
                shares = self._estimate_shares(ctx, trigger_idx)
                evt = self._make_event(ctx, 'F5_GAP_UP_FILL', trigger_idx, direction=-1, shares=shares)
                if evt:
                    events.append(evt)
        
        return events


def create_f5_signal(params: Optional[Dict] = None) -> F5GapFillSignal:
    return F5GapFillSignal(params)