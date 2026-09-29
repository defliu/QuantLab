import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import yaml

@dataclass
class SignalEvent:
    """单笔信号事件"""
    ts_code: str
    trade_date: str          # YYYYMMDD
    family: str              # F1-F6
    signal_type: str         # 细分类型
    trigger_bar_idx: int     # 触发时的 1min bar 索引
    trigger_time: str        # HH:MM
    trigger_price: float     # 触发价（参考）
    entry_price: float       # 入场价 = t+1 bar open
    exit_price: float        # 出场价 = 14:50 bar close
    direction: int           # +1 买入(正T) / -1 卖出(反T)
    shares: int              # 预估成交股数
    pnl_bps: float           # 单笔收益 bps（含成本）
    cost_bps: float          # 单笔成本 bps
    holding_bars: int        # 持仓根数
    
    def to_dict(self) -> Dict:
        return {
            'ts_code': self.ts_code,
            'trade_date': self.trade_date,
            'family': self.family,
            'signal_type': self.signal_type,
            'trigger_bar_idx': self.trigger_bar_idx,
            'trigger_time': self.trigger_time,
            'trigger_price': self.trigger_price,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'direction': self.direction,
            'shares': self.shares,
            'pnl_bps': self.pnl_bps,
            'cost_bps': self.cost_bps,
            'holding_bars': self.holding_bars
        }

@dataclass 
class ScanContext:
    """扫描上下文：预加载的数据和参数"""
    ts_code: str
    trade_date: str
    df_1min: pd.DataFrame      # 当日 1min 数据，已按时间排序
    df_5min: pd.DataFrame      # 当日 5min 数据（可选）
    df_auction_o: pd.DataFrame # 当日开盘集合竞价
    df_auction_c: pd.DataFrame # 当日收盘集合竞价
    prev_close: float          # 前日收盘价
    params: Dict               # 信号参数
    cost_model: Dict           # 成本模型
    df_auction_o_hist: pd.DataFrame = field(default_factory=pd.DataFrame)  # 该股全期竞价（F1 需 20 日均量）
    df_5min_hist: pd.DataFrame = field(default_factory=pd.DataFrame)       # 该股当日之前的 5min（F6 lookback）

    # 运行时计算缓存
    vwap_series: pd.Series = field(default_factory=pd.Series, init=False)
    cum_vol: pd.Series = field(default_factory=pd.Series, init=False)
    cum_amt: pd.Series = field(default_factory=pd.Series, init=False)
    _exit_price: float = field(default=np.nan, init=False)
    _median_vol: float = field(default=0.0, init=False)
    
    def __post_init__(self):
        if len(self.df_1min) > 0:
            self.cum_vol = self.df_1min['vol'].cumsum()
            self.cum_amt = self.df_1min['amount'].cumsum()
            self.vwap_series = self.cum_amt / self.cum_vol.replace(0, np.nan)
            self._median_vol = float(self.df_1min['vol'].median())
        # 出场价按日只算一次（原先每个事件都做一次 strftime 过 241 行，占全扫描 ~50% 耗时）
        self._exit_price = self._calc_exit_price()

    def get_bar_time(self, idx: int) -> str:
        """获取 bar 索引对应的时间 HH:MM（trade_time 兼容 Timestamp / int HHMM / str）"""
        if 0 <= idx < len(self.df_1min):
            t = self.df_1min['trade_time'].iat[idx]
            if hasattr(t, 'hour') and hasattr(t, 'minute'):
                return "{:02d}:{:02d}".format(int(t.hour), int(t.minute))
            if isinstance(t, str):
                s = t.replace('-', '').replace(':', '').replace(' ', '')
                return "{}:{}".format(s[8:10], s[10:12]) if len(s) >= 12 else t
            ti = int(t)
            return "{:02d}:{:02d}".format(ti // 100, ti % 100)
        return ""

    def get_bar_price(self, idx: int, field: str = 'open') -> float:
        """获取 bar 价格"""
        if 0 <= idx < len(self.df_1min):
            return float(self.df_1min[field].iat[idx])
        return np.nan

    def get_t1_open(self, trigger_idx: int) -> float:
        """获取 t+1 bar 的 open 价（入场价）"""
        return self.get_bar_price(trigger_idx + 1, 'open')

    def get_exit_price(self) -> float:
        """出场价：14:50 强平价（每日缓存）"""
        return self._exit_price

    def _calc_exit_price(self) -> float:
        """14:50 强平（取 >=14:50 第一根 bar 的 close）；无该 bar 退回最后一根。

        方案 r3 口径「至 14:50 未平仓者强制以收盘价了结」，数据最后一根是 15:00，两者差
        10 分钟动量，此处按方案取 14:50。
        """
        if len(self.df_1min) == 0:
            return np.nan
        tt = self.df_1min['trade_time']
        try:
            if np.issubdtype(tt.dtype, np.datetime64):
                sel = tt[tt.dt.strftime('%H:%M') >= '14:50']
            elif np.issubdtype(tt.dtype, np.integer):
                sel = tt[tt >= 1450]
            else:
                sel = tt.iloc[0:0]
            if len(sel) > 0:
                return float(self.df_1min['close'].iat[sel.index[0]])
        except Exception:
            pass
        return float(self.df_1min['close'].iat[-1])

class SignalBase(ABC):
    """信号计算器基类"""
    
    FAMILY: str = "BASE"
    DEFAULT_PARAMS: Dict = {}
    
    def __init__(self, params: Optional[Dict] = None):
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
    
    @abstractmethod
    def compute_signals(self, ctx: ScanContext) -> List[SignalEvent]:
        """计算该族信号，返回事件列表"""
        pass
    
    def _make_event(self, ctx: ScanContext, signal_type: str, 
                    trigger_idx: int, direction: int, 
                    shares: int = 100) -> Optional[SignalEvent]:
        """构造 SignalEvent，含防前视验证"""
        # 防前视：入场必须是 t+1 bar open
        entry_price = ctx.get_t1_open(trigger_idx)
        if np.isnan(entry_price):
            return None
        
        exit_price = ctx.get_exit_price()
        if np.isnan(exit_price):
            return None
        
        trigger_price = ctx.get_bar_price(trigger_idx, 'close')
        trigger_time = ctx.get_bar_time(trigger_idx)
        
        # 计算收益（扣成本）
        if direction == 1:  # 正T：买入->卖出
            raw_pnl_bps = (exit_price - entry_price) / entry_price * 10000
        else:  # 反T：卖出->买回
            raw_pnl_bps = (entry_price - exit_price) / entry_price * 10000
        
        cost_bps = ctx.cost_model.get('round_trip_bps', 15.2)
        pnl_bps = raw_pnl_bps - cost_bps
        
        holding_bars = len(ctx.df_1min) - trigger_idx - 1
        
        # 零股过滤
        if shares < 100:
            return None
        
        return SignalEvent(
            ts_code=ctx.ts_code,
            trade_date=ctx.trade_date,
            family=self.FAMILY,
            signal_type=signal_type,
            trigger_bar_idx=trigger_idx,
            trigger_time=trigger_time,
            trigger_price=trigger_price,
            entry_price=entry_price,
            exit_price=exit_price,
            direction=direction,
            shares=shares,
            pnl_bps=pnl_bps,
            cost_bps=cost_bps,
            holding_bars=holding_bars
        )
    
    def _estimate_shares(self, ctx: ScanContext, trigger_idx: int, 
                         max_position_pct: float = 0.01) -> int:
        """估算可成交股数（基于触发时刻后的流动性）

        拍板④（2026-09-27）：不足 100 股直接跳过、不入统计。
        故此处不做 100 股下限兜底（否则零股规则永远不生效），
        由 _make_event 的 `shares < 100` 过滤。
        """
        if ctx._median_vol > 0:
            est = int(ctx._median_vol * 30 * max_position_pct)  # 30分钟窗口
            return min(est, 100000)
        return 0

def load_config(path: str) -> Dict:
    """加载 YAML 配置"""
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def get_cost_model(config: Dict, trade_date: str) -> Dict:
    """根据日期获取分段成本模型"""
    # 2023-08-28 印花税单边化
    dt = pd.to_datetime(trade_date, format='%Y%m%d')
    cutoff = pd.Timestamp('2023-08-28')
    
    base = config.get('cost_model', {})
    if dt < cutoff:
        # 双边印花税
        return {
            **base,
            'round_trip_bps': base.get('commission_bps', 2.5) * 2 + 
                             base.get('stamp_tax_bps', 5.0) * 2 + 
                             base.get('transfer_fee_bps', 0.2) * 2 +
                             base.get('slippage_bps', 5.0) * 2
        }
    else:
        # 单边印花税（仅卖出）
        return {
            **base,
            'round_trip_bps': base.get('commission_bps', 2.5) * 2 + 
                             base.get('stamp_tax_bps', 5.0) + 
                             base.get('transfer_fee_bps', 0.2) * 2 +
                             base.get('slippage_bps', 5.0) * 2
        }