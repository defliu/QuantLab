# coding=gbk
"""trade_executor 最小测试策略
目标：验证 _te_ 函数的 下单→反查→超时→撤单→重试 链路
账号: 67014907
"""
import time as _time
from datetime import datetime

ACCOUNT_ID = "67014907"
LOG_FILE = "D:/QMT_POOL/strategy_log_v2.txt"
BUILD_TAG = "te_test_20260804_v2"

# --- 测试参数 ---
TEST_CODE = "600016.SH"    # 民生银行（低价股，不容易涨停）
TEST_AMOUNT = 100          # 最小100股
TEST_TIMEOUT = 180         # 3分钟超时
TEST_MAX_RETRIES = 2       # 最多重试2次


# --- _te_ 内嵌区（从 trade_executor.py 复制） ---
_TE_ACCT = ""
_TE_pending = {}
_TE_last_check_min = -1
_TE_TIMEOUT_SEC = 180
_TE_MAX_RETRIES = 3
_TE_LOOKUP_RETRIES = 15     # passorder后反查短轮询次数（撞~100ms异步分配订单号延迟）
_TE_LOOKUP_INTERVAL = 0.2   # 短轮询间隔秒（15次x0.2s=3s 起步，实测不够再加）


def _te_init(C, acct="67014907"):
    global _TE_ACCT, _TE_pending, _TE_last_check_min
    _TE_ACCT = acct
    _TE_pending = {}
    _TE_last_check_min = -1


def _te_log(msg, C=None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s [TE_TEST] %s" % (ts, msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _te_query_orders():
    try:
        return get_trade_detail_data(_TE_ACCT, "STOCK", "order") or []
    except Exception as e:
        _te_log("反查异常: %s" % str(e))
        return None


def _te_norm_code(code):
    return str(code or "").split(".")[0]


def _te_find_order(orders, code, direction):
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
    passorder(op_type, 1101, _TE_ACCT, code, 5, -1, amount, remark, 2, "", C)


def _te_buy(C, code, amount):
    try:
        _te_pass(23, code, amount, "TE_TEST买入", C)
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


def _te_cancel_order(C, code, order_id):
    try:
        passorder(24, 1101, _TE_ACCT, code, 5, order_id, 0, "TE_TEST撤单", 2, "", C)
        _te_log("[cancel] %s orderID=%s" % (code, order_id), C)
        return True
    except Exception as e:
        _te_log("[cancel error] %s: %s" % (code, str(e)), C)
        return False


def _te_check_pending(C):
    """挂单巡检核心逻辑"""
    global _TE_pending, _TE_last_check_min
    if not _TE_pending:
        return

    now = _time.time()
    now_min = int(now / 60)
    if now_min == _TE_last_check_min:
        return
    _TE_last_check_min = now_min

    _te_log("[pending] === 巡检 start, 待成交=%d ===" % len(_TE_pending), C)

    # 步骤1: 反查
    orders = _te_query_orders()
    if orders is None:
        _te_log("[pending] 反查不可用，跳过", C)
        return
    _te_log("[pending] 反查返回 %d 条委托" % len(orders), C)

    for code, info in list(_TE_pending.items()):
        elapsed = now - info["time"]

        # 步骤2: 找委托
        order = _te_find_order(orders, code, info["type"])
        if order is None:
            _te_log("[pending] %s 未找到委托，跳过" % code, C)
            continue

        # 步骤3: 查成交
        vol_traded = getattr(order, "m_nVolumeTraded", 0) or 0
        order_id = getattr(order, "m_nOrderID", None)
        status = getattr(order, "m_nOrderStatus", -1)
        _te_log("[pending] %s 委托状态=%s 成交=%d/%d 订单号=%s"
                % (code, status, vol_traded, info["amount"], order_id), C)

        if vol_traded >= info["amount"]:
            _TE_pending.pop(code, None)
            _te_log("[pending] %s 全部成交！" % code, C)
            continue

        # 步骤4: 超时检查
        _te_log("[pending] %s 已等%.0f秒 / 超时%d秒"
                % (code, elapsed, _TE_TIMEOUT_SEC), C)
        if elapsed < _TE_TIMEOUT_SEC:
            continue

        # 步骤5: 重试封顶
        if info["retries"] >= TEST_MAX_RETRIES:
            _TE_pending.pop(code, None)
            _te_log("[pending] %s 放弃（已达重试上限%d次）" % (code, TEST_MAX_RETRIES), C)
            continue

        # 步骤6: 撤单重下
        _te_log("[pending] %s 超时！准备撤单重下..." % code, C)
        if order_id:
            _te_cancel_order(C, code, order_id)
        else:
            _te_log("[pending] %s 无订单号，跳过撤单" % code, C)

        _te_pass(23, code, info["amount"], "TE_TEST重试", C)
        _te_wait_order_visible(code, info["type"])
        info["retries"] += 1
        info["time"] = now
        _te_log("[pending] %s 重下完成 retry=%d" % (code, info["retries"]), C)

    _te_log("[pending] === 巡检结束, 剩余=%d ===" % len(_TE_pending), C)


# --- 测试流程状态 ---
_test_phase = 0
_test_start_time = 0


def init(C):
    global _test_phase, _test_start_time
    _te_init(C, acct=ACCOUNT_ID)
    _test_start_time = _time.time()
    _test_phase = 0
    _te_log("=" * 60, C)
    _te_log("策略初始化, build=%s" % BUILD_TAG, C)
    _te_log("部署验证标记: DEPLOY_MARK=%s （日志出现此标记=已部署最新源码）" % BUILD_TAG, C)
    _te_log("测试标的: %s, 数量: %d, 超时: %d秒, 最大重试: %d"
            % (TEST_CODE, TEST_AMOUNT, TEST_TIMEOUT, TEST_MAX_RETRIES), C)
    _te_log("=" * 60, C)


def handlebar(C):
    global _test_phase, _test_start_time

    elapsed = _time.time() - _test_start_time

    # 阶段0: 首次调用 → 下单
    if _test_phase == 0:
        _te_log("=" * 60, C)
        _te_log("[阶段0] 下单测试", C)
        _te_log("[阶段0] 测试 get_trade_detail_data 可用性...", C)

        # 先测试反查接口是否可用
        test_orders = _te_query_orders()
        if test_orders is None:
            _te_log("[阶段0] get_trade_detail_data 不可用！无法测试", C)
            _te_log("[阶段0] 检查: 是否在QMT模拟端运行？", C)
            return
        _te_log("[阶段0] 反查接口可用, 返回 %d 条委托" % len(test_orders), C)

        # 下单
        _te_log("[阶段0] 下单: %s %d股" % (TEST_CODE, TEST_AMOUNT), C)
        _te_buy(C, TEST_CODE, TEST_AMOUNT)
        _test_phase = 1
        _test_start_time = _time.time()
        _te_log("[阶段0] 下单完成, 进入阶段1（等待+巡检）", C)
        return

    # 阶段1: 每分钟巡检一次（内部节流）
    if _test_phase == 1:
        _te_check_pending(C)

        # 60秒后进入阶段2（手动检查结果）
        if elapsed > 60 and not _TE_pending:
            _test_phase = 2
            _te_log("[阶段1] 所有订单已处理, 进入阶段2", C)

        # 10分钟强制结束
        if elapsed > 600:
            _te_log("[阶段1] 超时10分钟, 强制结束测试", C)
            _te_log("[阶段1] 剩余待成交: %s" % list(_TE_pending.keys()), C)
            _test_phase = 2

    # 阶段2: 打印测试报告
    if _test_phase == 2:
        _te_log("=" * 60, C)
        _te_log("[阶段2] 测试报告", C)
        _te_log("[阶段2] 总耗时: %.0f秒" % elapsed, C)
        _te_log("[阶段2] 剩余待成交: %d" % len(_TE_pending), C)
        if _TE_pending:
            for code, info in _TE_pending.items():
                _te_log("[阶段2]   %s: type=%s retries=%d amount=%d"
                        % (code, info["type"], info["retries"], info["amount"]), C)
        _te_log("[阶段2] 验证要点:", C)
        _te_log("[阶段2]   1. 反查接口是否返回数据", C)
        _te_log("[阶段2]   2. 委托状态/成交数量是否正确", C)
        _te_log("[阶段2]   3. 超时后是否触发撤单重下", C)
        _te_log("[阶段2]   4. 重试次数是否正确递增", C)
        _te_log("=" * 60, C)
        _test_phase = 3  # 停止


def exit(C):
    _te_log("[exit] 测试策略退出", C)
