# coding=gbk
"""QMT 交易执行模块（内嵌模板）
==========================================

本文件是【内嵌模板】，不是外部依赖。
新策略开发时，把下面的代码块直接复制粘贴到策略文件中即可。

实测验证（2026-08-03 模拟端）:
  - passorder() 下单正常
  - get_trade_detail_data() 反查正常（返回全部委托列表）
  - _te_find_order() 通过 code + m_strOptName 方向匹配正常
  - 全成交检测正常（m_nVolumeTraded >= amount → 移出 pending）
  - 注意: 模拟端 m_nOrderID 可能返回 None（不影响巡检，撤单有兜底）

用法：
  1. 把 "-嵌入区开始" 到 "嵌入区结束" 之间的代码粘贴到策略文件
  2. 策略全局区放 _TE_ACCT / _TE_pending / _TE_last_check_min / _TE_fallback
  3. init(C) 中调 _te_init(C, acct, fallback=你的替补函数)
  4. handlebar(C) 中调 _te_check_pending(C)
  5. 买入调 _te_buy(C, code, amount)
  6. 换仓前调 _te_cancel_all(C)

  替补函数签名: func(C, expired_code, expired_info) -> (code, amount) or None
  场景: 买单重试耗尽（如涨停买不进），自动撤单后买入替补标的

已内嵌到：strategy_v2.py (Project_10_价值小盘V2)
==========================================
"""

# ============================================================
# -trade_executor 嵌入区开始-
# 复制以下代码到你的策略文件中
# ============================================================

import time as _time

# --- 配置 ---
_TE_TIMEOUT_SEC = 180       # 委托超时秒数（3分钟）
_TE_MAX_RETRIES = 3         # 最大重试次数
_TE_LOOKUP_RETRIES = 15     # passorder后反查短轮询次数（撞~100ms异步分配订单号延迟）
_TE_LOOKUP_INTERVAL = 0.2   # 短轮询间隔秒（15次x0.2s=3s 起步，实测不够再加）

# --- 状态变量（放在策略全局区） ---
_TE_ACCT = ""               # 账号ID
_TE_pending = {}            # {code: {"type","amount","original_amount","price","time","retries"}}
_TE_last_check_min = -1     # 巡检分钟节流
_TE_fallback = None         # 替补回调：func(C, expired_code, expired_info) -> (code, amount) or None


def _te_init(C, acct="70180771", fallback=None):
    """初始化（在策略 init(C) 中调用）
    fallback: 可选回调 func(C, expired_code, expired_info) -> (code, amount) or None
              重试耗尽时调用，返回替补标的和数量"""
    global _TE_ACCT, _TE_pending, _TE_last_check_min, _TE_fallback
    _TE_ACCT = acct
    _TE_pending = {}
    _TE_last_check_min = -1
    _TE_fallback = fallback


def _te_log(msg, C=None):
    """日志输出"""
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s [TE] %s" % (ts, msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        with open("D:/QMT_POOL/strategy_log_v2.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _te_query_orders():
    """反查当日全部委托（全局函数，非C方法）"""
    try:
        return get_trade_detail_data(_TE_ACCT, "STOCK", "order") or []
    except Exception as e:
        _te_log("get_trade_detail_data异常: %s" % str(e))
        return None


def _te_norm_code(code):
    return str(code or "").split(".")[0]


def _te_find_order(orders, code, direction):
    """在委托列表中找 code+方向 的最近一笔
    direction: 'buy'/'sell'；返回 order 对象或 None
    若初始列表为空，短轮询重查（QMT 下单后 ~100ms 才写入委托）"""
    if not orders:
        # 短轮询：委托可能尚未写入（QMT order_id 分配延迟）
        for _ in range(_TE_LOOKUP_RETRIES):
            _time.sleep(_TE_LOOKUP_INTERVAL)
            orders = _te_query_orders()
            if orders:
                break
    if not orders:
        return None
    c6 = _te_norm_code(code)
    for o in reversed(orders):
        inst = getattr(o, "m_strInstrumentID", "") or ""
        if _te_norm_code(inst) != c6:
            continue
        op = getattr(o, "m_strOptName", "") or ""
        if direction == "buy" and "买" not in op:
            continue
        if direction == "sell" and "卖" not in op:
            continue
        return o
    return None


def _te_wait_order_visible(code, direction, retries=None, interval=None):
    """passorder后短轮询反查委托，等QMT异步分配order_id（~100ms延迟）
    返回可见的 order 对象或 None（None时由巡检兜底，不静默断链）"""
    if retries is None:
        retries = _TE_LOOKUP_RETRIES
    if interval is None:
        interval = _TE_LOOKUP_INTERVAL
    for _i in range(retries):
        orders = _te_query_orders()
        if orders:
            order = _te_find_order(orders, code, direction)
            if order is not None:
                return order
        _time.sleep(interval)
    return None


def _te_pass(op_type, code, amount, remark, C):
    """统一 passorder 调用（买入23/卖出24，市价单 prType=5）"""
    passorder(op_type, 1101, _TE_ACCT, code, 5, -1, amount, remark, 2, "", C)


def _te_buy(C, code, amount):
    """下单买入（市价单）"""
    try:
        _te_pass(23, code, amount, "TE买入", C)
        _TE_pending[code] = {
            "type": "buy",
            "amount": amount,
            "original_amount": amount,
            "price": 0,
            "time": _time.time(),
            "retries": 0,
        }
        order = _te_wait_order_visible(code, "buy")
        _te_log("[buy] %s 数量=%d%s" % (code, amount,
                "" if order else " (委托尚未可见，转巡检"), C)
        return True
    except Exception as e:
        _te_log("[buy error] %s: %s" % (code, str(e)), C)
        return False


def _te_sell(C, code, amount):
    """下单卖出（市价单）"""
    try:
        _te_pass(24, code, amount, "TE卖出", C)
        _TE_pending[code] = {
            "type": "sell",
            "amount": amount,
            "original_amount": amount,
            "price": 0,
            "time": _time.time(),
            "retries": 0,
        }
        order = _te_wait_order_visible(code, "sell")
        _te_log("[sell] %s 数量=%d%s" % (code, amount,
                "" if order else " (委托尚未可见，转巡检"), C)
        return True
    except Exception as e:
        _te_log("[sell error] %s: %s" % (code, str(e)), C)
        return False


def _te_cancel_order(C, code, order_id):
    """按订单号撤单"""
    try:
        passorder(24, 1101, _TE_ACCT, code, 5, order_id, 0, "TE撤单", 2, "", C)
        _te_log("[cancel] %s orderID=%s" % (code, order_id), C)
        return True
    except Exception as e:
        _te_log("[cancel error] %s: %s" % (code, str(e)), C)
        return False


def _te_cancel_all(C):
    """撤销所有待成交订单（换仓前调用）"""
    orders = _te_query_orders()
    for code, info in list(_TE_pending.items()):
        if orders:
            order = _te_find_order(orders, code, info["type"])
            oid = getattr(order, "m_nOrderID", None) if order else None
            if oid:
                _te_cancel_order(C, code, oid)
    _TE_pending.clear()
    _te_log("[cancel_all] 已清空待成交队列", C)


def _te_check_pending(C):
    """挂单巡检（handlebar 每bar调用，内部分钟节流）
    超时未成交 → 撤单重下，重试封顶后放弃"""
    global _TE_pending, _TE_last_check_min
    if not _TE_pending:
        return

    now = _time.time()
    now_min = int(now / 60)
    if now_min == _TE_last_check_min:
        return
    _TE_last_check_min = now_min

    _te_log("[pending] 巡检 start, 待成交=%d" % len(_TE_pending), C)

    orders = _te_query_orders()
    if orders is None:
        _te_log("[pending] 反查不可用，跳过", C)
        return

    for code, info in list(_TE_pending.items()):
        elapsed = now - info["time"]

        # 反查成交状态
        order = _te_find_order(orders, code, info["type"])
        if order is not None:
            vol_traded = getattr(order, "m_nVolumeTraded", 0) or 0
            if vol_traded >= info["amount"]:
                _TE_pending.pop(code, None)
                _te_log("[pending] %s 已全部成交 %d股" % (code, vol_traded), C)
                continue
            if vol_traded > 0:
                info["amount"] = info["amount"] - vol_traded
                _te_log("[pending] %s 部分成交 %d股，剩余 %d" % (code, vol_traded, info["amount"]), C)
        else:
            _te_log("[pending] %s 未找到委托，跳过" % code, C)
            continue

        # 超时检查
        if elapsed < _TE_TIMEOUT_SEC:
            _te_log("[pending] %s 等待%.0f秒（超时%d秒）" % (code, elapsed, _TE_TIMEOUT_SEC), C)
            continue

        # 重试封顶 → 尝试替补
        if info["retries"] >= _TE_MAX_RETRIES:
            _TE_pending.pop(code, None)
            _te_log("[pending] %s 重试%d次耗尽" % (code, info["retries"]), C)
            # 尝试替补标的（仅买单有替补意义）
            if info["type"] == "buy" and _TE_fallback is not None:
                try:
                    fb = _TE_fallback(C, code, info)
                    if fb is not None:
                        fb_code, fb_amount = fb
                        if fb_code and fb_amount and fb_amount > 0:
                            _te_log("[pending] %s → 替补 %s %d股" % (code, fb_code, fb_amount), C)
                            _te_buy(C, fb_code, fb_amount)
                        else:
                            _te_log("[pending] 替补函数返回空，放弃" , C)
                    else:
                        _te_log("[pending] 替补函数返回None，放弃", C)
                except Exception as e:
                    _te_log("[pending] 替补函数异常: %s" % str(e), C)
            else:
                _te_log("[pending] %s 放弃（无替补）" % code, C)
            continue

        # 撤单重下
        oid = getattr(order, "m_nOrderID", None) if order else None
        if oid:
            _te_cancel_order(C, code, oid)
        _te_pass(23 if info["type"] == "buy" else 24, code, info["amount"], "TE重试", C)
        _te_wait_order_visible(code, info["type"])
        info["retries"] += 1
        info["time"] = now
        _te_log("[pending] %s 重下 retry=%d" % (code, info["retries"]), C)


def _te_pending_count():
    """当前待成交数量"""
    return len(_TE_pending)


def _te_pending_codes():
    """当前待成交代码列表"""
    return list(_TE_pending.keys())


# ============================================================
# -trade_executor 嵌入区结束-
# ============================================================
