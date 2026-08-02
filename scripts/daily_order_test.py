# coding=gbk
"""QMT 每日委托测试脚本（含自动撤单+市价追单）

功能：
  1. 每天 09:35 限价买入 000001.SZ 100股
  2. 1分钟内未成交 → 自动撤单 → 改市价（卖1价）重下
  3. 日志增量写入 D:/QMT_POOL/order_test_log.txt

部署：粘贴到 miniQMT 策略编辑器，设置为全天运行，K线周期1分钟
"""
ACCOUNT_ID = '67014907'
TARGET_CODE = '000001.SZ'
TARGET_VOLUME = 100
ORDER_HOUR = 9
ORDER_MINUTE = 35
LOG_FILE = "D:/QMT_POOL/order_test_log.txt"
TIMEOUT_SECONDS = 60  # 限价单超时时间（秒）

def _get_time(C):
    try:
        tick_time = C.get_tick_timetag()
        if tick_time and tick_time > 0:
            from datetime import datetime
            return datetime.fromtimestamp(tick_time)
    except Exception:
        pass
    try:
        bar_time = C.get_bar_timetag(C.barpos)
        if bar_time and bar_time > 0:
            from datetime import datetime
            return datetime.fromtimestamp(bar_time)
    except Exception:
        pass
    from datetime import datetime
    return datetime.now()

def _log(msg):
    """打印并增量写入日志文件"""
    print(msg)
    try:
        import os
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        with open(LOG_FILE, "a", encoding="gbk") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def _get_order_status(C, code):
    """查询指定股票的委托状态"""
    try:
        orders = get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'order')
        if not orders:
            return None
        for o in orders:
            if code in getattr(o, 'm_strInstrumentID', ''):
                return {
                    'order_id': getattr(o, 'm_strOrderSysID', ''),
                    'status': getattr(o, 'm_nOrderStatus', -1),
                    'volume': getattr(o, 'm_nOrderVolume', 0),
                    'traded': getattr(o, 'm_nTradedVolume', 0),
                    'price': getattr(o, 'm_dOrderPrice', 0),
                    'traded_price': getattr(o, 'm_dTradePrice', 0),
                }
    except Exception:
        pass
    return None

def _cancel_order(C, order_id):
    """撤单"""
    try:
        result = cancel(order_id, ACCOUNT_ID, 'STOCK', C)
        return result
    except Exception as e:
        _log("[test] 撤单异常: %s" % e)
        return False

def _get_sell1_price(code):
    """获取卖1价（用于市价单）"""
    try:
        tick = get_full_tick([code])
        if tick and code in tick:
            ask1 = tick[code].get('askPrice', [0])[0] if isinstance(tick[code].get('askPrice'), list) else tick[code].get('askPrice', 0)
            if ask1 and ask1 > 0:
                return ask1
            # fallback: 用最新价
            return tick[code].get('lastPrice', 0)
    except Exception:
        pass
    return 0

def init(C):
    from datetime import datetime
    C.test_date = datetime.now().strftime("%Y-%m-%d")
    C.test_state = "idle"  # idle -> limit_placed -> monitoring -> done
    C.test_limit_order_id = None
    C.test_limit_time = None
    C.test_retry_count = 0
    C.test_max_retry = 2
    _log("")
    _log("=" * 50)
    _log("[%s] init OK, 账号: %s" % (C.test_date, ACCOUNT_ID))
    _log("=" * 50)

def handlebar(C):
    now = _get_time(C)
    hour = now.hour
    minute = now.minute
    ts = now.strftime("%H:%M:%S")

    # ====== 1. 限价下单（09:35） ======
    if C.test_state == "idle" and hour == ORDER_HOUR and minute >= ORDER_MINUTE:
        _log("[%s] 限价下单..." % ts)
        try:
            tick = get_full_tick([TARGET_CODE])
            if not tick or TARGET_CODE not in tick:
                _log("[%s] 无行情" % ts)
                return

            price = tick[TARGET_CODE].get("lastPrice", 0)
            if price <= 0:
                _log("[%s] 价格无效" % ts)
                return

            # 限价买入 (prType=11 指定价)
            passorder(23, 1101, ACCOUNT_ID, TARGET_CODE, 11, price, TARGET_VOLUME, C)
            C.test_limit_time = now
            C.test_state = "monitoring"
            _log("[%s] 限价委托已发送: 买入 %s %d股 @ %.2f" % (ts, TARGET_CODE, TARGET_VOLUME, price))

        except Exception as e:
            _log("[%s] 下单错误: %s" % (ts, e))
            C.test_state = "done"

    # ====== 2. 监控委托状态 ======
    if C.test_state == "monitoring":
        order_info = _get_order_status(C, TARGET_CODE)

        if order_info:
            status = order_info['status']
            traded = order_info['traded']

            # 已成交
            if status == 55:
                _log("[%s] 已成交: %d股 @ %.2f" % (ts, traded, order_info['traded_price']))
                C.test_state = "done"
                return

            # 废单
            if status == 56:
                _log("[%s] 废单，重新下单" % ts)
                C.test_state = "idle"
                C.test_retry_count = 0
                return

            # 已撤
            if status == 54:
                _log("[%s] 已撤单" % ts)
                C.test_state = "idle"
                return

            # 检查超时（限价单未成交超过1分钟）
            if C.test_limit_time:
                elapsed = (now - C.test_limit_time).total_seconds()
                if elapsed > TIMEOUT_SECONDS and traded == 0:
                    _log("[%s] 限价单%.0f秒未成交，撤单改市价" % (ts, elapsed))

                    # 撤单
                    if order_info['order_id']:
                        cancel_ok = _cancel_order(C, order_info['order_id'])
                        _log("[%s] 撤单: %s" % (ts, "成功" if cancel_ok else "失败"))

                    # 等待1根K线让撤单生效，然后市价追单
                    C.test_state = "wait_cancel"
                    C.test_cancel_time = now
                    return

    # ====== 3. 等待撤单生效后市价追单 ======
    if C.test_state == "wait_cancel":
        elapsed = (now - C.test_cancel_time).total_seconds()
        if elapsed >= 5:  # 等5秒让撤单生效
            # 获取卖1价作为市价
            ask1 = _get_sell1_price(TARGET_CODE)
            if ask1 <= 0:
                tick = get_full_tick([TARGET_CODE])
                if tick and TARGET_CODE in tick:
                    ask1 = tick[TARGET_CODE].get("lastPrice", 0)

            if ask1 > 0:
                _log("[%s] 市价追单: 买入 %s %d股 @ 卖1=%.2f" % (ts, TARGET_CODE, TARGET_VOLUME, ask1))
                # 市价买入 (prType=5 最新价)
                passorder(23, 1101, ACCOUNT_ID, TARGET_CODE, 5, ask1, TARGET_VOLUME, C)
                C.test_retry_count += 1
                C.test_limit_time = now
                C.test_state = "monitoring"
                _log("[%s] 市价委托已发送 (第%d次重试)" % (ts, C.test_retry_count))
            else:
                _log("[%s] 无法获取卖1价" % ts)
                C.test_state = "done"

    # ====== 4. 收盘汇总（15:00） ======
    if hour == 15 and minute == 0 and C.test_state != "reported":
        C.test_state = "reported"
        _log("")
        _log("====== 收盘汇总 ======")
        try:
            deals = get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'deal')
            today_deals = [d for d in deals if TARGET_CODE in getattr(d, 'm_strInstrumentID', '')]
            if today_deals:
                total_vol = 0
                total_amount = 0
                for d in today_deals:
                    vol = getattr(d, 'm_nTradeVolume', 0)
                    price = getattr(d, 'm_dTradePrice', 0)
                    total_vol += vol
                    total_amount += vol * price
                    _log("成交: %s %d股 @ %.2f" % (TARGET_CODE, vol, price))
                avg = total_amount / total_vol if total_vol > 0 else 0
                _log("合计: %d股, 均价 %.2f, 金额 %.2f" % (total_vol, avg, total_amount))
            else:
                _log("今日无成交")
        except Exception:
            _log("查询成交记录失败")
        _log("重试次数: %d" % C.test_retry_count)
        _log("===========================")
