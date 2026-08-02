# coding=gbk
"""QMT passorder 委托测试 + 状态监控

用法：粘贴到 miniQMT 策略编辑器执行
功能：
  1. 买入100股 000001.SZ
  2. 每根K线检查委托状态
  3. 输出成交/撤单/废单结果
"""
ACCOUNT_ID = '67014907'

def init(C):
    print("[test] init OK, 账号: %s" % ACCOUNT_ID)
    C.test_order_id = None
    C.test_done = False

def handlebar(C):
    # ====== 1. 下单（仅第一次） ======
    if not C.test_done and C.test_order_id is None:
        print("[test] 开始测试委托...")
        try:
            codes = C.get_stock_list_in_sector("沪深A股")
            test_code = codes[0]
            tick = C.get_full_tick([test_code])
            if not tick or test_code not in tick:
                print("[test] 无行情")
                return
            price = tick[test_code].get("lastPrice", 0)
            if price <= 0:
                print("[test] 价格无效")
                return

            print("[test] 目标: %s @ %.2f" % (test_code, price))
            passorder(23, 1101, ACCOUNT_ID, test_code, 11, price, 100, C)
            print("[test] 委托已发送")
            C.test_done = True

        except Exception as e:
            print("[test] 下单错误: %s" % e)
            C.test_done = True

    # ====== 2. 查询委托状态（每根K线） ======
    if C.test_done:
        try:
            orders = get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'order')
            if orders:
                for o in orders:
                    # 获取属性
                    code = getattr(o, 'm_strInstrumentID', '')
                    status = getattr(o, 'm_nOrderStatus', -1)
                    vol = getattr(o, 'm_nOrderVolume', 0)
                    traded = getattr(o, 'm_nTradedVolume', 0)
                    price = getattr(o, 'm_dOrderPrice', 0)
                    traded_price = getattr(o, 'm_dTradePrice', 0)
                    order_sys = getattr(o, 'm_strOrderSysID', '')
                    remark = getattr(o, 'm_strRemark', '')

                    # 只显示000001的委托
                    if '000001' in code:
                        status_map = {
                            48: '未报', 49: '待报', 50: '已报',
                            51: '已报待撤', 52: '部成', 53: '部撤',
                            54: '已撤', 55: '已成', 56: '废单'
                        }
                        status_str = status_map.get(status, '未知(%d)' % status)
                        print("[test] 委托状态: %s %s %d股 @ %.2f 状态=%s 成交%d股" % (
                            code, '买入' if vol > 0 else '卖出', abs(vol),
                            price, status_str, traded))
                        if traded > 0:
                            print("[test] 成交价: %.2f" % traded_price)
            else:
                print("[test] 暂无委托记录")
        except Exception as e:
            print("[test] 查询错误: %s" % e)
