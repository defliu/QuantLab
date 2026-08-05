# coding=utf-8
"""
QMT 委托买卖标准模块（防坑版）
================================

把"QMT 实盘下单"收敛为一个可复用、经过实战踩坑验证的模块。
任何 QMT 单文件策略（经 broker/qmt_builder.py 生成或手写）都应复用本模块，
**不要在策略里直接裸调 C.passorder** —— 直接调极易踩以下 3 个坑（2026-08-04 实盘审计结论）：

坑1【P0 致命】passorder 第 6 / 第 7 参数颠倒（价格 <-> 股数）
    正确签名（与 6+2 生产写法 strategy_main.py、scripts/test_passorder.py 一致）：
        passorder(op, order_type, account_id, code, price_type, price, volume, C)
                 ^第6位 = 价格                    ^第7位 = 股数
    错误写法：把第6位传成股数、第7位传成价格 -> 委托必废单 -> 表现为"没有委托成交"。
    本模块 send_limit_order / send_market_order 严格按正确签名封装，调用方只管传 (code, price, volume)。

坑2【P1】miniQMT 本地端没有 get_trade_detail_data -> 反查必失败 -> 持仓状态错乱
    本地 miniQMT（E:\\国金QMT交易端模拟）的 C 对象没有 get_trade_detail_data 方法。
    若依赖反查确认成交，每次都抛异常返回 None -> 买入被误判为 pending ->
    超时回滚把刚建的 ledger 删掉（"回滚持仓"）-> 账户实则已成交但策略以为没持仓 ->
    下次再平衡重复买入 / 持仓失联。
    修复：lookup_order() 检测 getattr(C, 'get_trade_detail_data', None) is None 时返回
          ('OPTIMISTIC', None)，调用方走"乐观确认"分支（写/删 ledger、不进 pending 死循环）。

坑3【P1】买入 pending 超时误删 ledger
    _check_pending_orders 买入 pending 超时原逻辑 del _g_my_codes[code] 回滚，
    在乐观模式下会误删已成交持仓。修复：乐观模式下买入超时只清 pending、保留 ledger。

本模块同时兼容：
    - 真实 QMT 环境（有 get_trade_detail_data，走精确反查）
    - 本地 miniQMT（无该方法，自动走乐观确认）
    - SAFEMODE（只记录不真正下单，用于灰度/演练）

兼容性：纯 Python 3.6 语法（无 f-string / 无 typing / 无 walrus），可直接被
broker/qmt_builder.py 合并进 GBK 单文件策略。
"""

import time


# ---- QMT passorder 常量（对齐 6+2 生产写法 / scripts/test_passorder.py）----
OP_BUY = 23            # 股票买入
OP_SELL = 24           # 股票卖出
ORDER_TYPE_VOL = 1101  # 单股单账号按股数
PRTYPE_LIMIT = 11      # 指定价（最稳健，推荐）
PRTYPE_LATEST = 0      # 最新价
# 注：对手价/市价等 price_type 常量以 QMT 官方 API 为准；不确定时一律用 PRTYPE_LIMIT + 显式价格。


class OrderResult(object):
    """下单结果。status 取值：SUBMITTED / SAFEMODE / REJECT / ERR / EXC。"""

    def __init__(self, status, order_id=None, note=""):
        self.status = status
        self.order_id = order_id
        self.note = note

    def ok(self):
        return self.status in ("SUBMITTED", "SAFEMODE")

    def __repr__(self):
        return "OrderResult(status=%s, order_id=%s, note=%s)" % (
            self.status, self.order_id, self.note)


def _as_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


class QmtOrderExecutor(object):
    """QMT 委托执行器：正确签名 + 乐观确认 + 安全的 pending 管理。"""

    def __init__(self, C, account_id, safemode=False, pending_timeout=30.0,
                 log_fn=None):
        """
        C              : QMT ContextInfo 对象
        account_id     : 资金账号，如 '67014907'
        safemode       : True 时只记录不真正下单
        pending_timeout: pending 超时秒数（默认 30）
        log_fn         : 可选日志函数 log_fn(msg)
        """
        self.C = C
        self.account_id = account_id
        self.safemode = safemode
        self.pending_timeout = pending_timeout
        self._log = log_fn if log_fn is not None else (lambda m: None)
        self._pending = {}  # key -> {code, volume, t, side}

    # ---------------------------------------------------------------
    # 核心下单：严格按正确签名封装（坑1）
    # ---------------------------------------------------------------
    def send_limit_order(self, side, code, price, volume):
        """限价单。side='BUY'/'SELL'。第6位=价格、第7位=股数，绝不可颠倒。"""
        if volume is None or volume <= 0:
            return OrderResult("REJECT", note="volume<=0")
        price = _as_float(price)
        if price <= 0:
            return OrderResult("REJECT", note="price<=0")
        if self.safemode:
            # SAFEMODE：只记录不真正下单（灰度/演练）
            self._log("[QMT_ORDER][SAFEMODE] %s %s %.2f x%s" % (side, code, price, volume))
            return OrderResult("SAFEMODE", note="safemode skip")
        op = OP_BUY if side == "BUY" else OP_SELL
        try:
            # 正确签名（坑1）：price 在第6位、volume 在第7位。
            # 关键点：passorder 是 QMT 全局函数（非 C.passorder），C 作为最后参数传入。
            ret = passorder(
                op, ORDER_TYPE_VOL, self.account_id, code,
                PRTYPE_LIMIT, price, volume, self.C
            )
            if ret == 0:
                self._log("[QMT_ORDER][买入确认?] %s %s 返回值:0" % (side, code))
                return OrderResult("SUBMITTED", note="passorder ret=0")
            return OrderResult("ERR", note="passorder ret=%s" % ret)
        except Exception as e:  # noqa: BLE001
            return OrderResult("EXC", note="passorder exc: %s" % e)

    def send_market_order(self, side, code, volume, price_type=PRTYPE_LATEST):
        """市价/对手价单。price 由 QMT 按 price_type 决定，仍走正确签名。"""
        if volume is None or volume <= 0:
            return OrderResult("REJECT", note="volume<=0")
        if self.safemode:
            self._log("[QMT_ORDER][SAFEMODE] %s %s x%s" % (side, code, volume))
            return OrderResult("SAFEMODE", note="safemode skip")
        op = OP_BUY if side == "BUY" else OP_SELL
        try:
            # 全局 passorder，C 作为最后参数传入（见 send_limit_order 注释）
            ret = passorder(
                op, ORDER_TYPE_VOL, self.account_id, code,
                price_type, 0, volume, self.C
            )
            if ret == 0:
                return OrderResult("SUBMITTED", note="passorder ret=0")
            return OrderResult("ERR", note="passorder ret=%s" % ret)
        except Exception as e:  # noqa: BLE001
            return OrderResult("EXC", note="passorder exc: %s" % e)

    # ---------------------------------------------------------------
    # 反查：乐观确认（坑2）
    # ---------------------------------------------------------------
    def lookup_order(self, code, side):
        """反查订单状态。返回 (status, order_id)。
        status 取值：
            OPTIMISTIC : miniQMT 无 get_trade_detail_data，按"已提交即视为成功"处理
            FILLED     : 真实环境确认成交
            (其他)     : 未成交 / 未知
        """
        lookup = getattr(self.C, "get_trade_detail_data", None)
        if lookup is None:
            # 坑2：本地 miniQMT 没有反查方法 -> 乐观确认，不抛异常、不进死循环
            return ("OPTIMISTIC", None)
        try:
            # 真实环境：按账号/市场/代码反查（具体字段以 QMT API 为准）
            data = lookup(self.account_id, "stock", code, "today")
            if data:
                return ("FILLED", None)
            return ("UNFILLED", None)
        except Exception:  # noqa: BLE001
            # 反查异常等同乐观确认，避免把已成交误判为失败
            return ("OPTIMISTIC", None)

    # ---------------------------------------------------------------
    # pending 管理：买入超时保留 ledger（坑3）
    # ---------------------------------------------------------------
    def register_pending(self, code, volume, side):
        key = "%s_%s" % (side, code)
        self._pending[key] = {"code": code, "volume": volume, "t": time.time(), "side": side}

    def check_pending_orders(self, on_confirm, on_rollback):
        """遍历 pending 并结算。
        on_confirm(code, volume) : 确认成交后写/修正 ledger
        on_rollback(code, volume): 确认失败/撤销后清理 ledger（仅卖出侧使用）
        乐观模式下买入超时：只清 pending、保留 ledger（坑3 修复核心）。
        """
        now = time.time()
        for key, info in list(self._pending.items()):
            status, _ = self.lookup_order(info["code"], info["side"])
            if status in ("FILLED", "OPTIMISTIC"):
                # 乐观确认或真实成交：写 ledger、清 pending
                self._pending.pop(key, None)
                on_confirm(info["code"], info["volume"])
                self._log("[QMT_ORDER][%s确认] %s 订单%s已确认" % (info["side"], info["code"], status))
                continue
            if now - info["t"] > self.pending_timeout:
                self._pending.pop(key, None)
                if info["side"] == "BUY":
                    # 坑3：买入超时在乐观模式下绝不删 ledger，保守当成交处理
                    on_confirm(info["code"], info["volume"])
                    self._log("[QMT_ORDER][买入超时] %s 乐观保留持仓(不回滚)" % info["code"])
                else:
                    on_rollback(info["code"], info["volume"])
                    self._log("[QMT_ORDER][卖出超时] %s 回滚持仓" % info["code"])


# ---------------------------------------------------------------------------
# 便捷函数（无状态场景直接用）
# ---------------------------------------------------------------------------
def buy(C, account_id, code, price, volume, safemode=False):
    return QmtOrderExecutor(C, account_id, safemode=safemode).send_limit_order("BUY", code, price, volume)


def sell(C, account_id, code, price, volume, safemode=False):
    return QmtOrderExecutor(C, account_id, safemode=safemode).send_limit_order("SELL", code, price, volume)
