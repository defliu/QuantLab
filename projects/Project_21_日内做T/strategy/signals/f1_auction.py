import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from .base import SignalBase, SignalEvent, ScanContext

class F1AuctionSignal(SignalBase):
    """F1: 集合竞价信号"""
    
    FAMILY = "F1"
    DEFAULT_PARAMS = {
        'auction_threshold_pct': 2.0,      # 集合竞价涨跌幅阈值
        'volume_ratio_threshold': 1.5,     # 竞价量比阈值
        'gap_confirm': True,               # 是否要求隔夜跳空确认
        'min_gap_pct': 1.0,                # 最小隔夜跳空幅度
    }
    
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        events = []
        
        # 当日集合竞价记录（若 ctx 附带全期历史，则按日期过滤；否则用当日切片）
        auction_o = ctx.df_auction_o_hist
        if auction_o is None or len(auction_o) == 0:
            auction_o = ctx.df_auction_o
        if auction_o is None or len(auction_o) == 0:
            return events
        
        today_mask = auction_o['trade_date'] == int(ctx.trade_date)
        if not today_mask.any():
            return events
        
        row = auction_o[today_mask].iloc[0]

        # 实际字段：open/high/low/close/vol/amount/vwap（历史坑：原代码读
        # auction_price/auction_vol/auction_amt 三个不存在的列 → 恒 NaN → F1 永不触发）
        auction_price = row.get('open', np.nan)
        auction_vol = row.get('vol', 0)
        prev_close = ctx.prev_close
        
        if pd.isna(auction_price) or prev_close <= 0 or not auction_vol or auction_vol <= 0:
            return events
        
        # 集合竞价涨跌幅
        auction_chg_pct = (auction_price - prev_close) / prev_close * 100
        
        # 历史集合竞价均量（过去20日，需全期历史帧）
        hist_mask = auction_o['trade_date'] < int(ctx.trade_date)
        hist_auction = auction_o[hist_mask].tail(20)
        avg_auction_vol = hist_auction['vol'].mean() if len(hist_auction) > 0 else 0
        
        if not avg_auction_vol or avg_auction_vol <= 0:
            return events
        
        volume_ratio = auction_vol / avg_auction_vol
        
        # 隔夜跳空
        gap_pct = (ctx.df_1min.iloc[0]['open'] - prev_close) / prev_close * 100 if len(ctx.df_1min) > 0 else 0
        
        # === 信号判定 ===
        params = self.params
        
        # F1_LONG: 集合竞价大涨 + 放量 + 隔夜向上跳空 → 预期高开低走，正T买入（注意：这里逻辑是反向的，集合竞价大涨通常预期回落）
        # 修正：集合竞价大涨往往是情绪透支，预期开盘回落 → 正T逢低买入（但在 t+1 bar open 入场，可能已错过最低点）
        # 标准做法：集合竞价大涨 + 放量 → 开盘追高（动量）或 逢低吸筹（反转）
        # 这里采用：集合竞价涨幅超过阈值 + 放量 → 开盘动量延续买入 (F1_MOM_LONG)
        # 集合竞价跌幅超过阈值 + 放量 → 开盘反转买入 (F1_REV_LONG)
        
        thresh = params['auction_threshold_pct']
        vol_thresh = params['volume_ratio_threshold']
        gap_thresh = params['min_gap_pct']
        
        # 触发时刻：开盘第一根 bar (idx=0 对应 9:30)
        trigger_idx = 0
        
        # 1. F1_MOM_LONG: 集合竞价强势上涨 + 放量 → 动量延续买入
        if (auction_chg_pct >= thresh and 
            volume_ratio >= vol_thresh and
            (not params['gap_confirm'] or gap_pct >= gap_thresh)):
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F1_MOM_LONG', trigger_idx, direction=1, shares=shares)
            if evt:
                events.append(evt)
        
        # 2. F1_MOM_SHORT: 集合竞价强势下跌 + 放量 → 动量延续卖出(反T)
        if (auction_chg_pct <= -thresh and 
            volume_ratio >= vol_thresh and
            (not params['gap_confirm'] or gap_pct <= -gap_thresh)):
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F1_MOM_SHORT', trigger_idx, direction=-1, shares=shares)
            if evt:
                events.append(evt)
        
        # 3. F1_REV_LONG: 集合竞价大跌 + 放量 + 无基本面利空 → 反转买入（超跌反弹）
        # 需要额外过滤：排除利空公告日（暂不实现，留接口）
        if (auction_chg_pct <= -thresh and 
            volume_ratio >= vol_thresh and
            gap_pct <= -gap_thresh):  # 向下跳空超跌
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F1_REV_LONG', trigger_idx, direction=1, shares=shares)
            if evt:
                events.append(evt)
        
        # 4. F1_REV_SHORT: 集合竞价大涨 + 放量 → 反转卖出(反T，超涨回落)
        if (auction_chg_pct >= thresh and 
            volume_ratio >= vol_thresh and
            gap_pct >= gap_thresh):  # 向上跳空超涨
            
            shares = self._estimate_shares(ctx, trigger_idx)
            evt = self._make_event(ctx, 'F1_REV_SHORT', trigger_idx, direction=-1, shares=shares)
            if evt:
                events.append(evt)
        
        return events


def create_f1_signal(params: Optional[Dict] = None) -> F1AuctionSignal:
    """工厂函数"""
    return F1AuctionSignal(params)