import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .base import SignalBase, SignalEvent, ScanContext

class F3VWAPDeviationSignal(SignalBase):
    """F3: VWAP 偏离信号（均值回归）"""
    
    FAMILY = "F3"
    DEFAULT_PARAMS = {
        'deviation_threshold_pct': 1.0,    # 偏离阈值
        'reentry_cooldown_min': 30,        # 重入冷却期(分钟)
        'min_bars_after_open': 15,         # 开盘后最小 bar 数才开始监控
    }
    
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        events = []
        df = ctx.df_1min

        params = self.params
        if len(df) < params.get('min_bars_after_open', 15):
            return events

        dev_thresh = params['deviation_threshold_pct']
        cooldown = params['reentry_cooldown_min']
        min_bars = params['min_bars_after_open']
        
        # 使用基类预计算的 VWAP
        vwap = ctx.vwap_series
        if len(vwap) < min_bars:
            return events
        
        # 收盘价
        close = df['close'].values
        
        # 计算偏离度: (close - vwap) / vwap * 100
        deviation = (close - vwap) / vwap * 100
        
        # 状态跟踪：防止同方向频繁触发
        last_long_idx = -cooldown - 1
        last_short_idx = -cooldown - 1
        
        # 从 min_bars 开始扫描（避免开盘初期 VWAP 不稳定）
        for i in range(min_bars, len(df) - 1):  # -1 保证有 t+1 bar
            dev = deviation[i]
            
            # F3_LONG: 价格显著低于 VWAP → 买入期待回归
            if dev <= -dev_thresh and (i - last_long_idx) > cooldown:
                shares = self._estimate_shares(ctx, i)
                evt = self._make_event(ctx, 'F3_LONG', i, direction=1, shares=shares)
                if evt:
                    events.append(evt)
                    last_long_idx = i
            
            # F3_SHORT: 价格显著高于 VWAP → 卖出期待回归(反T)
            if dev >= dev_thresh and (i - last_short_idx) > cooldown:
                shares = self._estimate_shares(ctx, i)
                evt = self._make_event(ctx, 'F3_SHORT', i, direction=-1, shares=shares)
                if evt:
                    events.append(evt)
                    last_short_idx = i
        
        return events


def create_f3_signal(params: Optional[Dict] = None) -> F3VWAPDeviationSignal:
    return F3VWAPDeviationSignal(params)