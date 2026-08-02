# coding=utf-8
"""最简买入测试：只买一只股票，验证委托通路。"""

def init(C):
    print("[test] init OK")

def handlebar(C):
    # 只执行一次
    if getattr(C, '_done', False):
        return
    C._done = True

    print("[test] 开始测试买入...")

    try:
        # 获取股票
        codes = C.get_stock_list_in_sector("沪深A股")
        test_code = codes[0]
        print("[test] 股票: %s" % test_code)

        # 获取价格
        tick = C.get_full_tick([test_code])
        if not tick or test_code not in tick:
            print("[test] 无行情")
            return

        price = tick[test_code].get("lastPrice", 0)
        print("[test] 价格: %s" % price)

        if price <= 0:
            print("[test] 价格无效")
            return

        # 委托买入100股
        C.passorder(23, 1101, C.accountid, test_code, 11, price, 100, C, strRemark="test")
        print("[test] 委托已发送: 买入 %s 100股 @ %s" % (test_code, price))

    except Exception as e:
        print("[test] 错误: %s" % e)
