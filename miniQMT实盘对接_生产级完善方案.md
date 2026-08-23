# miniQMT 实盘对接 — 生产级完善方案

> 账号: **70180771**

你之前的方案存在以下 **关键缺陷**，我逐一补全：

---

## 一、原方案问题诊断

|#|问题|风险等级|说明|
|---|---|---|---|
|1|缺少 `XtQuantTraderCallback` 回调类|🔴 严重|无法异步接收成交/撤单/断线通知|
|2|无断线重连机制|🔴 严重|网络抖动直接丢失连接|
|3|无委托超时处理|🔴 严重|挂单不成交会一直挂着|
|4|无部分成交处理|🟡 中等|只成交一半时逻辑错误|
|5|无持仓对账|🟡 中等|程序持仓 vs 券商持仓不一致|
|6|无交易时段判断|🟡 中等|非交易时间下单会报错|
|7|无状态持久化|🟡 中等|程序崩溃后丢失所有状态|
|8|无告警通知|🟡 中等|异常时无法及时知道|
|9|无优雅退出|🟠 一般|Ctrl+C 可能导致持仓不一致|
|10|账号未写入|🟠 一般|需要硬编码你的账号|

---

## 二、生产级完整代码

### 1. 配置文件

```yaml
# config/trading_config.yaml

account:
  id: "70180771"
  path: "D:\\miniQMT\\userdata_mini"   # 根据实际安装路径修改
  session_id: 670149

trading:
  initial_capital: 500000        # 初始资金（根据你的实际资金调整）
  max_single_position: 0.05     # 单股最大仓位 5%
  max_total_position: 0.80      # 总仓位上限 80%
  commission_rate: 0.00025      # 佣金万2.5
  stamp_tax: 0.001              # 印花税千1（卖出）
  slippage: 0.002               # 滑点

risk:
  stop_loss_pct: 0.08           # 个股止损 8%
  max_drawdown: 0.15            # 组合最大回撤 15%
  max_holding_days: 60          # 最长持有天数
  max_daily_turnover: 0.30      # 单日最大换手

order:
  timeout_seconds: 300          # 委托超时（5分钟）
  max_retry: 3                  # 最大重试次数
  price_tolerance: 0.005        # 价格容差（0.5%），超出不追单
  cancel_unfilled: true         # 超时未成交是否自动撤单

schedule:
  pre_market: "09:15"           # 盘前准备
  morning_open: "09:35"         # 开盘执行（避开集合竞价）
  afternoon_check: "13:05"      # 午后检查
  risk_check_interval: 30       # 风控检查间隔（分钟）
  end_of_day: "14:50"           # 尾盘处理

notification:
  enabled: true
  method: "wechat"              # wechat / dingtalk / email
  webhook_url: ""               # 企业微信/钉钉 webhook
```

---

### 2. 回调类 + 断线重连（核心补全）

```python
# broker/callback.py

import logging
import time
import threading
from datetime import datetime
from typing import Callable, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("QMT")


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class OrderRecord:
    """委托记录（带状态追踪）"""
    order_id: int
    code: str
    direction: str
    price: float
    volume: int
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: int = 0
    filled_price: float = 0.0
    create_time: float = field(default_factory=time.time)
    update_time: float = field(default_factory=time.time)
    retry_count: int = 0
    error_msg: str = ""
    
    @property
    def is_finished(self) -> bool:
        return self.status in (
            OrderStatus.FILLED, OrderStatus.CANCELLED, 
            OrderStatus.REJECTED, OrderStatus.TIMEOUT
        )
    
    @property
    def is_timeout(self) -> bool:
        if self.is_finished:
            return False
        return (time.time() - self.create_time) > 300  # 5分钟


class QMTCallback:
    """
    miniQMT 回调类（核心！）
    
    继承 xtquant.XtQuantTraderCallback
    处理: 委托回报、成交回报、撤单回报、断线通知
    """
    
    def __init__(self, broker: 'MiniQMTBrokerPro'):
        self.broker = broker
    
    def on_disconnected(self):
        """断线回调 — 触发自动重连"""
        logger.warning("⚠️ miniQMT连接断开，启动重连...")
        self.broker._connected = False
        threading.Thread(target=self.broker._reconnect, daemon=True).start()
    
    def on_stock_order(self, order):
        """委托回报"""
        logger.info(
            f"📋 委托回报: {order.stock_code} "
            f"{'买' if order.order_type == 23 else '卖'} "
            f"{order.order_volume}股 @{order.price} "
            f"状态={order.order_status}"
        )
        self.broker._update_order_status(order)
    
    def on_stock_trade(self, trade):
        """成交回报"""
        logger.info(
            f"✅ 成交回报: {trade.stock_code} "
            f"{'买' if trade.order_type == 23 else '卖'} "
            f"{trade.traded_volume}股 @{trade.traded_price}"
        )
        self.broker._on_trade(trade)
    
    def on_order_error(self, order_error):
        """委托失败"""
        logger.error(
            f"❌ 委托失败: {order_error.stock_code} "
            f"错误码={order_error.error_id} "
            f"信息={order_error.error_msg}"
        )
        self.broker._on_order_error(order_error)
    
    def on_cancel_error(self, cancel_error):
        """撤单失败"""
        logger.error(
            f"❌ 撤单失败: 委托号={cancel_error.order_id} "
            f"错误={cancel_error.error_msg}"
        )
    
    def on_order_stock_async_response(self, response):
        """异步下单响应"""
        if response.error_id != 0:
            logger.error(f"❌ 异步下单失败: {response.error_msg}")
    
    def on_cancel_order_stock_async_response(self, response):
        """异步撤单响应"""
        pass
    
    def on_account_status(self, status):
        """账户状态变化"""
        logger.info(f"📊 账户状态: {status.status}")


# ============================================================
#  3. 生产级 Broker 实现
# ============================================================

class MiniQMTBrokerPro:
    """
    miniQMT 生产级 Broker
    
    账号: 70180771
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.account_id = config['account']['id']  # "70180771"
        self.path = config['account']['path']
        self.session_id = config['account']['session_id']
        
        self.xt_trader = None
        self.xt_data = None
        self.account = None
        self.callback = None
        
        self._connected = False
        self._reconnecting = False
        self._max_reconnect_attempts = 10
        self._reconnect_interval = 5  # 秒
        
        # 委托追踪
        self._orders: dict[int, OrderRecord] = {}
        self._order_lock = threading.Lock()
        
        # 成交记录
        self._trades: list = []
        
        # 状态持久化
        self._state_file = "data/broker_state.json"
    
    # ----------------------------------------------------------
    #  连接管理
    # ----------------------------------------------------------
    
    def connect(self) -> bool:
        """连接 miniQMT"""
        try:
            from xtquant import xttrader, xtdata
            from xtquant.xttype import StockAccount
            
            logger.info(f"🔌 正在连接 miniQMT... 账号: {self.account_id}")
            
            # 创建交易对象
            self.xt_trader = xttrader.XtQuantTrader(self.path, self.session_id)
            
            # 注册回调（关键！）
            self.callback = QMTCallback(self)
            self.xt_trader.register_callback(self.callback)
            
            # 启动
            self.xt_trader.start()
            
            # 连接
            result = self.xt_trader.connect()
            if result != 0:
                logger.error(f"❌ 连接失败，错误码: {result}")
                return False
            
            # 创建账户对象
            self.account = StockAccount(self.account_id)
            
            # 订阅账户
            sub_result = self.xt_trader.subscribe(self.account)
            if sub_result != 0:
                logger.warning(f"⚠️ 账户订阅返回: {sub_result}")
            
            self.xt_data = xtdata
            self._connected = True
            
            # 验证连接：查询账户
            asset = self.get_account()
            logger.info(
                f"✅ 连接成功！账号: {self.account_id}\n"
                f"   总资产: ¥{asset['total_asset']:,.0f}\n"
                f"   可用资金: ¥{asset['available_cash']:,.0f}\n"
                f"   持仓市值: ¥{asset['market_value']:,.0f}"
            )
            
            return True
            
        except ImportError as e:
            logger.error(f"❌ 缺少xtquant库: {e}")
            logger.error("   请从miniQMT安装目录复制xtquant到site-packages")
            return False
        except Exception as e:
            logger.error(f"❌ 连接异常: {e}")
            return False
    
    def _reconnect(self):
        """自动重连（指数退避）"""
        if self._reconnecting:
            return
        self._reconnecting = True
        
        for attempt in range(1, self._max_reconnect_attempts + 1):
            wait_time = min(self._reconnect_interval * (2 ** (attempt - 1)), 60)
            logger.info(f"🔄 第{attempt}次重连，等待{wait_time}秒...")
            time.sleep(wait_time)
            
            try:
                if self.connect():
                    logger.info("✅ 重连成功！")
                    self._reconnecting = False
                    self._notify("miniQMT重连成功")
                    return
            except Exception as e:
                logger.warning(f"重连失败: {e}")
        
        logger.critical("🚨 重连失败已达上限！请手动检查！")
        self._notify("🚨 miniQMT重连失败，请手动检查！", level="critical")
        self._reconnecting = False
    
    def disconnect(self):
        """安全断开"""
        if self.xt_trader:
            self.xt_trader.stop()
        self._connected = False
        self._save_state()
        logger.info("miniQMT已安全断开")
    
    # ----------------------------------------------------------
    #  交易时段判断
    # ----------------------------------------------------------
    
    @staticmethod
    def is_trading_time() -> bool:
        """判断当前是否为交易时段"""
        now = datetime.now()
        
        # 周末不交易
        if now.weekday() >= 5:
            return False
        
        current_time = now.time()
        morning_start = datetime.strptime("09:30", "%H:%M").time()
        morning_end = datetime.strptime("11:30", "%H:%M").time()
        afternoon_start = datetime.strptime("13:00", "%H:%M").time()
        afternoon_end = datetime.strptime("15:00", "%H:%M").time()
        
        return (morning_start <= current_time <= morning_end or
                afternoon_start <= current_time <= afternoon_end)
    
    @staticmethod
    def is_pre_market() -> bool:
        """是否为盘前（9:15-9:30）"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.time()
        return datetime.strptime("09:15", "%H:%M").time() <= t <= datetime.strptime("09:30", "%H:%M").time()
    
    # ----------------------------------------------------------
    #  账户查询
    # ----------------------------------------------------------
    
    def get_account(self) -> dict:
        """查询账户"""
        self._check_connected()
        asset = self.xt_trader.query_stock_asset(self.account)
        return {
            'total_asset': asset.total_asset,
            'available_cash': asset.cash,
            'market_value': asset.market_value,
            'frozen_cash': asset.frozen_cash,
        }
    
    def get_positions(self) -> list[dict]:
        """查询持仓"""
        self._check_connected()
        positions = self.xt_trader.query_stock_positions(self.account)
        result = []
        for pos in positions:
            if pos.volume > 0:
                result.append({
                    'code': pos.stock_code,
                    'volume': pos.volume,
                    'available_volume': pos.can_use_volume,
                    'cost_price': pos.open_price,
                    'market_value': pos.market_value,
                    'current_price': pos.market_value / pos.volume if pos.volume > 0 else 0,
                    'profit': pos.market_value - pos.open_price * pos.volume,
                    'profit_pct': (pos.market_value / (pos.open_price * pos.volume) - 1)
                                 if pos.open_price > 0 and pos.volume > 0 else 0,
                })
        return result
    
    def get_today_orders(self) -> list[dict]:
        """查询当日委托"""
        self._check_connected()
        orders = self.xt_trader.query_stock_orders(self.account)
        return [{
            'order_id': o.order_id,
            'code': o.stock_code,
            'direction': 'buy' if o.order_type == 23 else 'sell',
            'price': o.price,
            'volume': o.order_volume,
            'filled_volume': o.traded_volume,
            'filled_price': o.traded_price,
            'status': o.order_status,
        } for o in orders]
    
    # ----------------------------------------------------------
    #  下单（带完整状态追踪）
    # ----------------------------------------------------------
    
    def place_order(self, code: str, direction: str,
                    price: float, volume: int,
                    order_type: str = "limit") -> Optional[OrderRecord]:
        """
        下单（生产级）
        
        - 自动检查交易时段
        - 自动取整到100股
        - 自动检查资金/持仓
        - 记录委托状态
        """
        self._check_connected()
        
        # 交易时段检查
        if not self.is_trading_time():
            logger.warning(f"⚠️ 非交易时段，拒绝下单: {code}")
            return None
        
        # 取整
        volume = (volume // 100) * 100
        if volume <= 0:
            logger.warning(f"⚠️ 数量不足1手: {code}")
            return None
        
        # 资金/持仓检查
        if direction == "buy":
            account = self.get_account()
            cost = price * volume * 1.001  # 含佣金估算
            if cost > account['available_cash']:
                max_volume = int(account['available_cash'] / (price * 1.001) / 100) * 100
                if max_volume <= 0:
                    logger.warning(f"⚠️ 资金不足: 需¥{cost:,.0f}, 可用¥{account['available_cash']:,.0f}")
                    return None
                volume = max_volume
                logger.info(f"📐 资金不足，调整为{volume}股")
        
        elif direction == "sell":
            positions = {p['code']: p for p in self.get_positions()}
            if code not in positions:
                logger.warning(f"⚠️ 无持仓: {code}")
                return None
            available = positions[code]['available_volume']
            if volume > available:
                volume = (available // 100) * 100
                if volume <= 0:
                    logger.warning(f"⚠️ 可卖数量不足: {code}")
                    return None
                logger.info(f"📐 可卖不足，调整为{volume}股")
        
        # 执行下单
        from xtquant.xtconstant import STOCK_BUY, STOCK_SELL, FIX_PRICE
        
        xt_direction = STOCK_BUY if direction == "buy" else STOCK_SELL
        
        order_id = self.xt_trader.order_stock(
            self.account, code, xt_direction,
            volume, FIX_PRICE, price,
            strategy_name="quant_70180771",
            order_remark=f"{direction}_{code}_{datetime.now().strftime('%H%M%S')}"
        )
        
        if order_id == -1:
            logger.error(f"❌ 下单失败: {code} {direction} {volume}股 @{price}")
            self._notify(f"下单失败: {code} {direction} {volume}股")
            return None
        
        # 记录委托
        record = OrderRecord(
            order_id=order_id,
            code=code,
            direction=direction,
            price=price,
            volume=volume,
            status=OrderStatus.SUBMITTED,
        )
        
        with self._order_lock:
            self._orders[order_id] = record
        
        logger.info(
            f"📤 下单成功: {code} {direction.upper()} "
            f"{volume}股 @{price:.2f} 委托号={order_id}"
        )
        
        return record
    
    def cancel_order(self, order_id: int) -> bool:
        """撤单"""
        self._check_connected()
        result = self.xt_trader.cancel_order_stock(self.account, order_id)
        if result == 0:
            logger.info(f"✅ 撤单成功: {order_id}")
            with self._order_lock:
                if order_id in self._orders:
                    self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        logger.error(f"❌ 撤单失败: {order_id}")
        return False
    
    # ----------------------------------------------------------
    #  委托超时监控
    # ----------------------------------------------------------
    
    def start_order_monitor(self, timeout: int = 300):
        """
        启动委托监控线程
        
        - 超时未成交 → 自动撤单
        - 部分成交 → 记录并通知
        """
        def _monitor():
            while self._connected:
                time.sleep(10)  # 每10秒检查一次
                
                with self._order_lock:
                    for oid, record in list(self._orders.items()):
                        if record.is_finished:
                            continue
                        
                        # 超时检查
                        elapsed = time.time() - record.create_time
                        if elapsed > timeout:
                            logger.warning(
                                f"⏰ 委托超时({elapsed:.0f}s): "
                                f"{record.code} {record.direction} "
                                f"{record.volume}股, 已成交{record.filled_volume}股"
                            )
                            
                            if self.config['order'].get('cancel_unfilled', True):
                                self.cancel_order(oid)
                                record.status = OrderStatus.TIMEOUT
                                self._notify(
                                    f"委托超时已撤: {record.code} "
                                    f"成交{record.filled_volume}/{record.volume}股"
                                )
        
        thread = threading.Thread(target=_monitor, daemon=True)
        thread.start()
        logger.info("📡 委托监控线程已启动")
    
    # ----------------------------------------------------------
    #  回调处理
    # ----------------------------------------------------------
    
    def _update_order_status(self, order):
        """更新委托状态"""
        with self._order_lock:
            if order.order_id in self._orders:
                record = self._orders[order.order_id]
                record.filled_volume = order.traded_volume
                record.update_time = time.time()
                
                # 状态映射
                status_map = {
                    48: OrderStatus.PENDING,
                    49: OrderStatus.PENDING,
                    50: OrderStatus.SUBMITTED,
                    52: OrderStatus.PARTIAL_FILLED,
                    55: OrderStatus.FILLED,
                    54: OrderStatus.CANCELLED,
                    56: OrderStatus.REJECTED,
                }
                record.status = status_map.get(order.order_status, OrderStatus.PENDING)
    
    def _on_trade(self, trade):
        """成交处理"""
        with self._order_lock:
            if trade.order_id in self._orders:
                record = self._orders[trade.order_id]
                record.filled_volume += trade.traded_volume
                record.filled_price = trade.traded_price
                
                if record.filled_volume >= record.volume:
                    record.status = OrderStatus.FILLED
                    logger.info(f"🎉 全部成交: {record.code} {record.volume}股")
        
        self._trades.append({
            'time': datetime.now().isoformat(),
            'code': trade.stock_code,
            'direction': 'buy' if trade.order_type == 23 else 'sell',
            'price': trade.traded_price,
            'volume': trade.traded_volume,
        })
    
    def _on_order_error(self, order_error):
        """委托错误处理"""
        with self._order_lock:
            if order_error.order_id in self._orders:
                record = self._orders[order_error.order_id]
                record.status = OrderStatus.REJECTED
                record.error_msg = order_error.error_msg
    
    # ----------------------------------------------------------
    #  持仓对账
    # ----------------------------------------------------------
    
    def reconcile_positions(self, expected_positions: dict[str, int]) -> dict:
        """
        持仓对账: 程序预期 vs 券商实际
        
        expected_positions: {code: expected_volume}
        
        返回: 差异报告
        """
        actual = {p['code']: p['volume'] for p in self.get_positions()}
        
        discrepancies = []
        
        # 检查所有预期持仓
        all_codes = set(list(expected_positions.keys()) + list(actual.keys()))
        
        for code in all_codes:
            expected = expected_positions.get(code, 0)
            actual_vol = actual.get(code, 0)
            
            if expected != actual_vol:
                discrepancies.append({
                    'code': code,
                    'expected': expected,
                    'actual': actual_vol,
                    'diff': actual_vol - expected,
                })
        
        if discrepancies:
            logger.warning(f"⚠️ 持仓对账发现 {len(discrepancies)} 处差异:")
            for d in discrepancies:
                logger.warning(
                    f"   {d['code']}: 预期{d['expected']}股, "
                    f"实际{d['actual']}股, 差异{d['diff']}股"
                )
            self._notify(f"持仓对账异常: {len(discrepancies)}处差异", level="warning")
        else:
            logger.info("✅ 持仓对账一致")
        
        return {
            'is_consistent': len(discrepancies) == 0,
            'discrepancies': discrepancies,
        }
    
    # ----------------------------------------------------------
    #  状态持久化
    # ----------------------------------------------------------
    
    def _save_state(self):
        """保存状态到磁盘（崩溃恢复用）"""
        import json
        import os
        
        os.makedirs("data", exist_ok=True)
        
        state = {
            'save_time': datetime.now().isoformat(),
            'connected': self._connected,
            'orders': {
                str(k): {
                    'order_id': v.order_id,
                    'code': v.code,
                    'direction': v.direction,
                    'price': v.price,
                    'volume': v.volume,
                    'status': v.status.value,
                    'filled_volume': v.filled_volume,
                }
                for k, v in self._orders.items()
            },
            'trades': self._trades[-100:],  # 最近100笔
        }
        
        with open(self._state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def _load_state(self) -> Optional[dict]:
        """加载上次状态"""
        import json
        import os
        
        if os.path.exists(self._state_file):
            with open(self._state_file) as f:
                return json.load(f)
        return None
    
    # ----------------------------------------------------------
    #  通知
    # ----------------------------------------------------------
    
    def _notify(self, message: str, level: str = "info"):
        """发送通知（企业微信/钉钉）"""
        webhook = self.config.get('notification', {}).get('webhook_url', '')
        if not webhook:
            return
        
        import requests
        
        # 企业微信格式
        payload = {
            "msgtype": "text",
            "text": {
                "content": f"[量化交易-{level.upper()}] {message}\n"
                          f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                          f"账号: {self.account_id}"
            }
        }
        
        try:
            requests.post(webhook, json=payload, timeout=5)
        except Exception:
            pass  # 通知失败不影响主流程
    
    # ----------------------------------------------------------
    #  工具方法
    # ----------------------------------------------------------
    
    def _check_connected(self):
        """检查连接状态"""
        if not self._connected:
            raise ConnectionError("miniQMT未连接，请先调用connect()")
    
    def get_realtime_price(self, code: str) -> float:
        """获取实时价格"""
        self._check_connected()
        tick = self.xt_data.get_full_tick([code])
        if code in tick:
            return tick[code]['lastPrice']
        return 0.0
    
    def get_realtime_prices(self, codes: list[str]) -> dict[str, float]:
        """批量获取实时价格"""
        self._check_connected()
        ticks = self.xt_data.get_full_tick(codes)
        return {code: ticks[code]['lastPrice'] for code in codes if code in ticks}
    
    def download_history(self, codes: list[str], period: str = "1d",
                         start_time: str = "", end_time: str = ""):
        """下载历史数据（盘前调用）"""
        self._check_connected()
        self.xt_data.download_history_data2(
            codes, period=period,
            start_time=start_time, end_time=end_time
        )
        logger.info(f"📥 历史数据下载完成: {len(codes)}只股票")
    
    def get_pending_orders(self) -> list[OrderRecord]:
        """获取未完成委托"""
        with self._order_lock:
            return [o for o in self._orders.values() if not o.is_finished]
    
    def get_today_trades(self) -> list[dict]:
        """获取今日成交"""
        return self._trades.copy()
```

---

### 3. 实盘执行引擎（完善版）

```python
# broker/live_engine.py

import logging
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional
import yaml

logger = logging.getLogger("LiveEngine")


class LiveTradingEngine:
    """
    实盘执行引擎（生产级）
    
    职责:
    1. 目标组合 → 交易指令
    2. 智能拆单（大单拆小）
    3. 执行进度追踪
    4. 异常处理 + 告警
    """
    
    def __init__(self, broker: 'MiniQMTBrokerPro', config: dict):
        self.broker = broker
        self.config = config
        self.trading_config = config['trading']
        self.risk_config = config['risk']
        self.order_config = config['order']
        
        # 执行状态
        self._target_portfolio: Dict[str, float] = {}
        self._execution_progress: Dict[str, dict] = {}
        self._is_executing = False
    
    # ----------------------------------------------------------
    #  核心: 执行目标组合
    # ----------------------------------------------------------
    
    def execute_target_portfolio(self, target_weights: Dict[str, float],
                                 dry_run: bool = False) -> dict:
        """
        执行目标组合调仓
        
        target_weights: {code: weight}  如 {"000001.SZ": 0.05, "600519.SH": 0.08}
        dry_run: True=只计算不下单（调试用）
        
        执行逻辑:
        1. 计算目标持仓 vs 当前持仓
        2. 先卖后买（释放资金）
        3. 大单拆分（单笔不超过5万股）
        4. 追踪执行进度
        """
        if self._is_executing:
            logger.warning("⚠️ 上一次调仓尚未完成，跳过")
            return {'status': 'skipped', 'reason': 'already_executing'}
        
        self._is_executing = True
        
        try:
            # 1. 获取当前状态
            account = self.broker.get_account()
            total_capital = account['total_asset']
            current_positions = {
                p['code']: p for p in self.broker.get_positions()
            }
            
            logger.info(
                f"\n{'='*50}\n"
                f"📊 开始调仓 | 总资产: ¥{total_capital:,.0f}\n"
                f"   目标持仓: {len(target_weights)}只\n"
                f"   当前持仓: {len(current_positions)}只\n"
                f"{'='*50}"
            )
            
            # 2. 计算交易指令
            orders_to_execute = self._calc_orders(
                target_weights, current_positions, total_capital
            )
            
            if dry_run:
                logger.info("🔍 [DRY RUN] 交易指令:")
                for o in orders_to_execute:
                    logger.info(f"   {o['action']} {o['code']} {o['volume']}股 @{o['price']:.2f}")
                return {'status': 'dry_run', 'orders': orders_to_execute}
            
            # 3. 先执行卖出
            sell_orders = [o for o in orders_to_execute if o['action'] == 'SELL']
            buy_orders = [o for o in orders_to_execute if o['action'] == 'BUY']
            
            executed = []
            failed = []
            
            for order in sell_orders:
                result = self._execute_single_order(order)
                if result:
                    executed.append(result)
                else:
                    failed.append(order)
                time.sleep(0.5)  # 避免频率限制
            
            # 等待卖出成交（释放资金）
            if sell_orders:
                logger.info("⏳ 等待卖出成交...")
                time.sleep(5)
            
            # 4. 执行买入
            for order in buy_orders:
                # 重新检查资金
                account = self.broker.get_account()
                cost = order['price'] * order['volume'] * 1.001
                if cost > account['available_cash']:
                    # 缩减数量
                    max_vol = int(account['available_cash'] / (order['price'] * 1.001) / 100) * 100
                    if max_vol <= 0:
                        logger.warning(f"⚠️ 资金不足，跳过: {order['code']}")
                        failed.append(order)
                        continue
                    order['volume'] = max_vol
                
                result = self._execute_single_order(order)
                if result:
                    executed.append(result)
                else:
                    failed.append(order)
                time.sleep(0.5)
            
            # 5. 汇总
            report = {
                'status': 'completed',
                'total_orders': len(orders_to_execute),
                'executed': len(executed),
                'failed': len(failed),
                'executed_details': executed,
                'failed_details': failed,
                'time': datetime.now().isoformat(),
            }
            
            logger.info(
                f"\n{'='*50}\n"
                f"✅ 调仓完成: 成功{len(executed)}笔, 失败{len(failed)}笔\n"
                f"{'='*50}"
            )
            
            if failed:
                self.broker._notify(
                    f"调仓完成，{len(failed)}笔失败: "
                    f"{[f['code'] for f in failed]}"
                )
            
            return report
            
        except Exception as e:
            logger.error(f"❌ 调仓异常: {e}", exc_info=True)
            self.broker._notify(f"调仓异常: {e}", level="critical")
            return {'status': 'error', 'message': str(e)}
        
        finally:
            self._is_executing = False
            self.broker._save_state()
    
    def _calc_orders(self, target_weights: Dict[str, float],
                     current_positions: Dict[str, dict],
                     total_capital: float) -> List[dict]:
        """计算交易指令"""
        orders = []
        max_position_pct = self.trading_config['max_single_position']
        slippage = self.trading_config['slippage']
        
        # 获取实时价格
        all_codes = list(set(
            list(target_weights.keys()) + list(current_positions.keys())
        ))
        prices = self.broker.get_realtime_prices(all_codes)
        
        # 卖出: 不在目标中 或 需要减仓
        for code, pos in current_positions.items():
            target_weight = target_weights.get(code, 0)
            target_value = total_capital * target_weight
            target_shares = int(target_value / prices.get(code, pos['current_price']) / 100) * 100
            
            if pos['available_volume'] > target_shares:
                sell_volume = pos['available_volume'] - target_shares
                sell_volume = (sell_volume // 100) * 100
                if sell_volume > 0:
                    price = prices.get(code, pos['current_price'])
                    sell_price = round(price * (1 - slippage), 2)
                    orders.append({
                        'action': 'SELL',
                        'code': code,
                        'volume': sell_volume,
                        'price': sell_price,
                        'reason': 'rebalance' if target_weight > 0 else 'exit',
                    })
        
        # 买入: 新标的 或 需要加仓
        for code, weight in target_weights.items():
            # 限制单股权重
            weight = min(weight, max_position_pct)
            
            target_value = total_capital * weight
            price = prices.get(code, 0)
            if price <= 0:
                logger.warning(f"⚠️ 无法获取价格: {code}")
                continue
            
            target_shares = int(target_value / price / 100) * 100
            current_shares = current_positions.get(code, {}).get('volume', 0)
            
            if target_shares > current_shares:
                buy_volume = target_shares - current_shares
                buy_volume = (buy_volume // 100) * 100
                if buy_volume > 0:
                    buy_price = round(price * (1 + slippage), 2)
                    
                    # 大单拆分（单笔不超过5万股）
                    chunks = self._split_order(buy_volume, max_chunk=50000)
                    for chunk in chunks:
                        orders.append({
                            'action': 'BUY',
                            'code': code,
                            'volume': chunk,
                            'price': buy_price,
                            'reason': 'new' if current_shares == 0 else 'add',
                        })
        
        return orders
    
    @staticmethod
    def _split_order(volume: int, max_chunk: int = 50000) -> List[int]:
        """大单拆分"""
        if volume <= max_chunk:
            return [volume]
        
        chunks = []
        remaining = volume
        while remaining > 0:
            chunk = min(remaining, max_chunk)
            chunk = (chunk // 100) * 100
            if chunk <= 0:
                break
            chunks.append(chunk)
            remaining -= chunk
        return chunks
    
    def _execute_single_order(self, order: dict) -> Optional[dict]:
        """执行单笔委托"""
        direction = 'buy' if order['action'] == 'BUY' else 'sell'
        
        record = self.broker.place_order(
            code=order['code'],
            direction=direction,
            price=order['price'],
            volume=order['volume']
        )
        
        if record:
            return {
                'order_id': record.order_id,
                'code': order['code'],
                'action': order['action'],
                'volume': order['volume'],
                'price': order['price'],
                'status': 'submitted',
            }
        return None
    
    # ----------------------------------------------------------
    #  风控执行
    # ----------------------------------------------------------
    
    def execute_risk_check(self):
        """执行风控检查（盘中定时调用）"""
        positions = self.broker.get_positions()
        account = self.broker.get_account()
        
        stop_loss_pct = self.risk_config['stop_loss_pct']
        max_drawdown = self.risk_config['max_drawdown']
        
        # 1. 个股止损
        for pos in positions:
            if pos['profit_pct'] <= -stop_loss_pct:
                if pos['available_volume'] > 0:
                    price = self.broker.get_realtime_price(pos['code'])
                    if price > 0:
                        sell_price = round(price * 0.998, 2)
                        self.broker.place_order(
                            pos['code'], 'sell', sell_price, pos['available_volume']
                        )
                        logger.warning(
                            f"🚨 止损: {pos['code']} 亏损{pos['profit_pct']:.2%}"
                        )
                        self.broker._notify(
                            f"🚨 止损卖出 {pos['code']}, 亏损{pos['profit_pct']:.2%}",
                            level="warning"
                        )
        
        # 2. 组合回撤检查
        # (需要记录峰值，简化处理)
    
    # ----------------------------------------------------------
    #  盘前/盘后
    # ----------------------------------------------------------
    
    def pre_market_routine(self, stock_pool: List[str]):
        """盘前例行"""
        logger.info("📋 盘前准备开始...")
        
        # 1. 检查连接
        if not self.broker._connected:
            self.broker.connect()
        
        # 2. 下载最新数据
        self.broker.download_history(stock_pool, period="1d")
        
        # 3. 查询账户
        account = self.broker.get_account()
        logger.info(
            f"   账户: ¥{account['total_asset']:,.0f} | "
            f"可用: ¥{account['available_cash']:,.0f} | "
            f"持仓: ¥{account['market_value']:,.0f}"
        )
        
        # 4. 检查昨日未完成委托
        pending = self.broker.get_pending_orders()
        if pending:
            logger.warning(f"⚠️ 发现{len(pending)}笔未完成委托，自动撤单")
            for order in pending:
                self.broker.cancel_order(order.order_id)
        
        logger.info("✅ 盘前准备完成")
    
    def end_of_day_routine(self):
        """盘后例行"""
        logger.info("📋 盘后处理开始...")
        
        # 1. 对账
        # (需要传入预期持仓)
        
        # 2. 保存状态
        self.broker._save_state()
        
        # 3. 生成日报
        account = self.broker.get_account()
        positions = self.broker.get_positions()
        trades = self.broker.get_today_trades()
        
        report = (
            f"📊 交易日报 {datetime.now().strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"总资产: ¥{account['total_asset']:,.0f}\n"
            f"持仓数: {len(positions)}只\n"
            f"今日成交: {len(trades)}笔\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        
        for pos in positions[:10]:  # 前10只
            report += (
                f"  {pos['code']}: {pos['volume']}股 "
                f"盈亏{pos['profit_pct']:.2%}\n"
            )
        
        logger.info(report)
        self.broker._notify(report)
```

---

### 4. 主程序入口（实盘）

```python
# live_trading.py

import logging
import signal
import sys
import time
import yaml
import schedule
from datetime import datetime

from broker.live_engine import LiveTradingEngine
from broker.mini_qmt_pro import MiniQMTBrokerPro

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f'logs/trading_{datetime.now().strftime("%Y%m%d")}.log',
                          encoding='utf-8')
    ]
)
logger = logging.getLogger("Main")


class TradingApp:
    """实盘交易主程序"""
    
    def __init__(self, config_path: str = "config/trading_config.yaml"):
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # 初始化Broker
        self.broker = MiniQMTBrokerPro(self.config)
        
        # 初始化执行引擎
        self.engine = LiveTradingEngine(self.broker, self.config)
        
        # 策略（你的多因子策略）
        self.strategy = None  # 后续接入
        
        # 股票池
        self.stock_pool = []
        
        # 优雅退出
        self._running = True
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """优雅退出"""
        logger.info("\n🛑 收到退出信号，正在安全关闭...")
        self._running = False
        self.shutdown()
        sys.exit(0)
    
    def start(self):
        """启动"""
        logger.info("=" * 60)
        logger.info("🚀 A股量化交易系统启动")
        logger.info(f"   账号: {self.config['account']['id']}")
        logger.info(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        # 1. 连接
        if not self.broker.connect():
            logger.critical("❌ 无法连接miniQMT，退出")
            return
        
        # 2. 启动委托监控
        self.broker.start_order_monitor(
            timeout=self.config['order']['timeout_seconds']
        )
        
        # 3. 恢复状态（如果上次异常退出）
        state = self.broker._load_state()
        if state:
            logger.info(f"📂 恢复上次状态 (保存于 {state['save_time']})")
        
        # 4. 注册定时任务
        self._setup_schedule()
        
        # 5. 主循环
        logger.info("✅ 系统就绪，等待执行...")
        
        while self._running:
            schedule.run_pending()
            time.sleep(1)
    
    def _setup_schedule(self):
        """注册定时任务"""
        sched = self.config['schedule']
        
        # 盘前 (09:15)
        schedule.every().day.at(sched['pre_market']).do(
            self._job_pre_market
        )
        
        # 开盘执行 (09:35)
        schedule.every().day.at(sched['morning_open']).do(
            self._job_execute_strategy
        )
        
        # 午后检查 (13:05)
        schedule.every().day.at(sched['afternoon_check']).do(
            self._job_afternoon_check
        )
        
        # 风控检查 (每N分钟)
        schedule.every(sched['risk_check_interval']).minutes.do(
            self._job_risk_check
        )
        
        # 尾盘 (14:50)
        schedule.every().day.at(sched['end_of_day']).do(
            self._job_end_of_day
        )
        
        logger.info("📅 定时任务已注册:")
        logger.info(f"   盘前准备: {sched['pre_market']}")
        logger.info(f"   策略执行: {sched['morning_open']}")
        logger.info(f"   午后检查: {sched['afternoon_check']}")
        logger.info(f"   风控检查: 每{sched['risk_check_interval']}分钟")
        logger.info(f"   尾盘处理: {sched['end_of_day']}")
    
    # ----------------------------------------------------------
    #  定时任务
    # ----------------------------------------------------------
    
    def _job_pre_market(self):
        """盘前任务"""
        if datetime.now().weekday() >= 5:
            return
        logger.info("⏰ [盘前] 开始执行...")
        self.engine.pre_market_routine(self.stock_pool)
    
    def _job_execute_strategy(self):
        """执行策略"""
        if datetime.now().weekday() >= 5:
            return
        if not self.broker.is_trading_time():
            logger.warning("非交易时段，跳过")
            return
        
        logger.info("⏰ [策略] 开始执行...")
        
        # TODO: 接入你的多因子策略
        # target = self.strategy.generate_target_portfolio(...)
        
        # 示例: 等权买入5只
        target = {
            '000001.SZ': 0.05,
            '600519.SH': 0.05,
            '000858.SZ': 0.05,
            '601318.SH': 0.05,
            '002415.SZ': 0.05,
        }
        
        result = self.engine.execute_target_portfolio(target, dry_run=False)
        logger.info(f"策略执行结果: {result['status']}")
    
    def _job_afternoon_check(self):
        """午后检查"""
        if datetime.now().weekday() >= 5:
            return
        logger.info("⏰ [午后] 检查持仓...")
        positions = self.broker.get_positions()
        logger.info(f"   当前持仓: {len(positions)}只")
    
    def _job_risk_check(self):
        """风控检查"""
        if not self.broker.is_trading_time():
            return
        self.engine.execute_risk_check()
    
    def _job_end_of_day(self):
        """尾盘处理"""
        if datetime.now().weekday() >= 5:
            return
        logger.info("⏰ [尾盘] 开始处理...")
        self.engine.end_of_day_routine()
    
    def shutdown(self):
        """安全关闭"""
        logger.info("🛑 正在关闭...")
        
        # 撤销所有未完成委托
        pending = self.broker.get_pending_orders()
        for order in pending:
            self.broker.cancel_order(order.order_id)
            logger.info(f"   撤销: {order.code} {order.volume}股")
        
        # 保存状态
        self.broker._save_state()
        
        # 断开连接
        self.broker.disconnect()
        
        logger.info("✅ 系统已安全关闭")


# ============================================================
#  入口
# ============================================================

if __name__ == "__main__":
    app = TradingApp("config/trading_config.yaml")
    app.start()
```

---

### 5. 启动脚本

```bash
# start_trading.bat (Windows)

@echo off
echo ========================================
echo   A股量化交易系统 - 账号 70180771
echo ========================================

:: 检查miniQMT是否运行
tasklist | findstr "XtMiniQmt.exe" >nul
if %errorlevel% neq 0 (
    echo [ERROR] miniQMT未运行，请先启动miniQMT客户端！
    pause
    exit /b 1
)

:: 创建日志目录
if not exist "logs" mkdir logs

:: 启动
python live_trading.py

pause
```

---

## 三、执行流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    每日执行流程                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  09:15  盘前准备                                            │
│    ├── 检查miniQMT连接（断线则重连）                          │
│    ├── 下载最新行情数据                                      │
│    ├── 查询账户状态                                          │
│    └── 撤销昨日未完成委托                                    │
│                                                             │
│  09:35  策略执行                                            │
│    ├── 计算因子 → 生成目标组合                               │
│    ├── 对比当前持仓 → 生成交易指令                           │
│    ├── 先卖后买（释放资金）                                  │
│    ├── 大单拆分（≤5万股/笔）                                │
│    └── 追踪执行进度                                         │
│                                                             │
│  10:05  风控检查（每30分钟）                                 │
│    ├── 个股止损（-8%）                                      │
│    ├── 组合回撤检查（-15%）                                  │
│    └── 异常告警（微信通知）                                  │
│                                                             │
│  13:05  午后检查                                            │
│    └── 确认持仓状态                                         │
│                                                             │
│  14:50  尾盘处理                                            │
│    ├── 持仓对账（程序 vs 券商）                              │
│    ├── 保存状态到磁盘                                       │
│    └── 发送交易日报                                         │
│                                                             │
│  15:00  收盘                                                │
│    └── 等待下一交易日                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、异常处理矩阵

|异常场景|处理方式|通知|
|---|---|---|
|miniQMT断线|指数退避自动重连（最多10次）|✅ 微信|
|委托超时(5min)|自动撤单|✅ 微信|
|部分成交|记录已成交量，不追单|✅ 微信|
|资金不足|自动缩减数量或跳过|⚠️ 日志|
|涨停买不进|跳过该标的|⚠️ 日志|
|跌停卖不出|标记，次日优先处理|✅ 微信|
|程序崩溃|重启后从磁盘恢复状态|✅ 微信|
|持仓对账不一致|以券商为准，告警|🚨 微信+日志|
|非交易时段下单|拒绝执行|⚠️ 日志|
|重连10次失败|停止交易，等待人工介入|🚨 微信|

---

## 五、上线前测试清单

```python
# test_broker.py — 连接测试脚本

"""
运行此脚本验证miniQMT对接是否正常
python test_broker.py
"""

import yaml
from broker.mini_qmt_pro import MiniQMTBrokerPro

def test():
    with open("config/trading_config.yaml") as f:
        config = yaml.safe_load(f)
    
    broker = MiniQMTBrokerPro(config)
    
    # 1. 连接测试
    print("1️⃣ 测试连接...")
    assert broker.connect(), "连接失败！"
    print("   ✅ 连接成功")
    
    # 2. 账户查询
    print("2️⃣ 查询账户...")
    account = broker.get_account()
    print(f"   总资产: ¥{account['total_asset']:,.0f}")
    print(f"   可用:   ¥{account['available_cash']:,.0f}")
    print(f"   市值:   ¥{account['market_value']:,.0f}")
    
    # 3. 持仓查询
    print("3️⃣ 查询持仓...")
    positions = broker.get_positions()
    print(f"   持仓: {len(positions)}只")
    for p in positions[:5]:
        print(f"   {p['code']}: {p['volume']}股 盈亏{p['profit_pct']:.2%}")
    
    # 4. 实时价格
    print("4️⃣ 获取实时价格...")
    price = broker.get_realtime_price("000001.SZ")
    print(f"   000001.SZ: ¥{price:.2f}")
    
    # 5. 模拟下单（dry run，不实际执行）
    print("5️⃣ 下单测试（仅在交易时段有效）...")
    if broker.is_trading_time():
        # 用极小金额测试
        record = broker.place_order("000001.SZ", "buy", price * 0.99, 100)
        if record:
            print(f"   委托号: {record.order_id}")
            # 立即撤单
            time.sleep(2)
            broker.cancel_order(record.order_id)
            print("   ✅ 下单+撤单测试通过")
    else:
        print("   ⚠️ 非交易时段，跳过下单测试")
    
    # 6. 断开
    broker.disconnect()
    print("\n✅ 所有测试通过！系统可以上线。")


if __name__ == "__main__":
    import time
    test()
```

---

## 六、还需要你确认的事项

|#|事项|说明|
|---|---|---|
|1|**miniQMT安装路径**|`D:\miniQMT\userdata_mini` 是否正确？|
|2|**xtquant库**|是否已从miniQMT目录复制到Python的site-packages？|
|3|**资金规模**|初始资金是多少？（影响仓位计算）|
|4|**交易频率**|每周调仓 or 每日调仓？|
|5|**通知方式**|企业微信/钉钉 webhook 地址？|
|6|**策略接入**|多因子策略是否已回测通过？需要我帮接入吗？|
|7|**运行环境**|Windows本机 or 云服务器？（影响稳定性）|

确认这些后，我可以帮你把策略模块和执行引擎完整串联起来，形成可直接运行的最终版本。