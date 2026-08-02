# coding=utf-8
"""最小测试策略：每根K线尝试买入一只股票，验证下单通路。"""

def init(C):
    print("[test] init OK")
    C.test_count = 0

def handlebar(C):
    C.test_count = getattr(C, 'test_count', 0) + 1

    # 每100根K线打印一次
    if C.test_count % 100 == 0:
        print("[test] bar #%d" % C.test_count)

    # 只在第一根K线尝试买入
    if C.test_count != 1:
        return

    print("[test] 尝试买入测试...")

    # 获取一只股票
    try:
        codes = C.get_stock_list_in_sector("沪深A股")
        if not codes:
            print("[test] 无股票")
            return
        test_code = codes[0]
        print("[test] 目标: %s" % test_code)

        # 获取价格
        tick = C.get_full_tick([test_code])
        if tick and test_code in tick:
            price = tick[test_code].get("lastPrice", 0)
            print("[test] 价格: %.2f" % price)

            if price > 0:
                # 买入100股
                C.passorder(23, 1101, C.accountid, test_code, 11, price, 100, C, strRemark="test")
                print("[test] 委托买入 %s 100股 @ %.2f" % (test_code, price))
            else:
                print("[test] 价格为0")
        else:
            print("[test] 无行情")
    except Exception as e:
        print("[test] 错误: %s" % e)
