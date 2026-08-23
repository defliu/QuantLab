# coding=gbk
"""挂单调试脚本：下一单 → 盯3分钟 → 没成交就撤单重下
用法：在QMT中加载运行，观察 debug_pending_log.txt 输出
"""
import time
from datetime import datetime

ACCOUNT_ID = "70180771"
TEST_SIDE = "buy"         # buy / sell（sell 模式 init 先校验账户持仓，无持仓不执行）
TEST_CODE = "600016.SH"  # 测试股票（流动性好）
TEST_AMOUNT = 100        # 测试数量（最小100股）
TIMEOUT_SEC = 180        # 超时秒数（3分钟）
LOG_FILE = "D:/QMT_POOL/debug_pending_log.txt"

# 模块级变量（QMT的global必须在模块级初始化）
_order_time = 0
_retries = 0
_order_id = None
_stage = "未启动"


def _log(msg, C=None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s [debug_pending] %s" % (ts, msg)
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _query_orders():
    """反查当日全部委托"""
    try:
        return get_trade_detail_data(ACCOUNT_ID, "STOCK", "order") or []
    except Exception as e:
        _log("get_trade_detail_data异常: %s" % str(e))
        return None


def _norm_code(code):
    return str(code or "").split(".")[0]


def _find_order(orders, code):
    """找指定股票的最近一笔委托"""
    if not orders:
        return None
    c6 = _norm_code(code)
    for o in reversed(orders):
        inst = getattr(o, "m_strInstrumentID", "") or ""
        if _norm_code(inst) != c6:
            continue
        return o
    return None


def init(C):
    _log("=== 挂单调试启动 ===")
    _log("方向=%s 测试标的: %s, 数量: %d, 超时: %d秒" % (TEST_SIDE, TEST_CODE, TEST_AMOUNT, TIMEOUT_SEC))
    # sell 模式：先校验账户持仓（红线：绝不盲卖无持仓的票）
    if TEST_SIDE == "sell":
        try:
            pos_vol = 0
            c6 = _norm_code(TEST_CODE)
            for p in (get_trade_detail_data(ACCOUNT_ID, "STOCK", "position") or []):
                inst = getattr(p, "m_strInstrumentID", "") or ""
                if _norm_code(inst) == c6:
                    pos_vol = int(getattr(p, "m_nVolume", 0) or 0)
                    break
            if pos_vol < TEST_AMOUNT:
                _log("[abort] 卖出模式但账户持仓 %d < %d，不执行（防盲卖）" % (pos_vol, TEST_AMOUNT))
                _order_time = time.time()
                _stage = "已中止"
                return
            _log("[sell] 账户持仓校验通过: %d股" % pos_vol)
        except Exception as e:
            _log("[abort] 持仓校验异常: %s，不执行" % str(e))
            _order_time = time.time()
            _stage = "已中止"
            return
    # 第一步：下市价单（买卖同链路，方向不同）
    _order_type = 24 if TEST_SIDE == "sell" else 23
    _log("[1] 下单 passorder(%d, 1101, %s, %s, 5, -1, %d)" % (_order_type, ACCOUNT_ID, TEST_CODE, TEST_AMOUNT))
    try:
        passorder(_order_type, 1101, ACCOUNT_ID, TEST_CODE, 5, -1, TEST_AMOUNT, "调试测试", 2, "", C)
        _log("[1] passorder调用成功")
    except Exception as e:
        _log("[1] passorder异常: %s" % str(e))
        return
    # 记录下单时间
    _order_time = time.time()
    _retries = 0
    _order_id = None
    _stage = "等待成交"


def handlebar(C):
    global _order_time, _retries, _order_id, _stage
    now = time.time()
    elapsed = now - _order_time

    # 每30秒反查一次（避免刷屏）
    if int(elapsed) % 30 != 0:
        return

    _log("[巡检] 已等%.0f秒, 重试%d次, 阶段=%s" % (elapsed, _retries, _stage))

    # 反查委托
    orders = _query_orders()
    if orders is None:
        _log("[巡检] get_trade_detail_data返回None，跳过")
        return

    order = _find_order(orders, TEST_CODE)
    if order is None:
        _log("[巡检] 未找到委托记录")
        return

    # 打印委托详情
    inst = getattr(order, "m_strInstrumentID", "?")
    status = getattr(order, "m_nOrderStatus", "?")
    vol_total = getattr(order, "m_nOrderVolume", 0)
    vol_traded = getattr(order, "m_nVolumeTraded", 0)
    oid = getattr(order, "m_nOrderID", "?")
    _log("[巡检] 委托: %s 状态=%s 委托量=%d 成交量=%d orderID=%s" % (inst, status, vol_total, vol_traded, oid))

    # 判断是否全部成交
    if vol_traded >= TEST_AMOUNT:
        _log("[结果] 全部成交! %d股" % vol_traded)
        _stage = "已成交"
        return

    # 超时检查
    if elapsed < TIMEOUT_SEC:
        _log("[巡检] 未超时（%.0f/%d秒），继续等待" % (elapsed, TIMEOUT_SEC))
        return

    # === 超时处理 ===
    _log("[超时] %.0f秒未全部成交，开始撤单重下" % elapsed)

    # 撤单
    if oid and oid != "?":
        try:
            passorder(24, 1101, ACCOUNT_ID, TEST_CODE, 5, oid, 0, "调试撤单", 2, "", C)
            _log("[撤单] passorder(24) 调用成功, orderID=%s" % oid)
        except Exception as e:
            _log("[撤单] passorder异常: %s" % str(e))
    else:
        _log("[撤单] orderID无效=%s，跳过撤单" % oid)

    # 重下
    _retries += 1
    if _retries >= 3:
        _log("[放弃] 已重试%d次，放弃测试" % _retries)
        _stage = "已放弃"
        return

    try:
        _order_type = 24 if TEST_SIDE == "sell" else 23
        passorder(_order_type, 1101, ACCOUNT_ID, TEST_CODE, 5, -1, TEST_AMOUNT, "调试重试%d" % _retries, 2, "", C)
        _log("[重下] passorder调用成功, retry=%d" % _retries)
        _order_time = now  # 重置计时
        _stage = "重试中"
    except Exception as e:
        _log("[重下] passorder异常: %s" % str(e))


def exit(C):
    _log("=== 挂单调试结束 ===")
