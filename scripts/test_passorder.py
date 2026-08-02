# coding=gbk
"""QMT passorder 委托测试

用法：粘贴到 miniQMT 策略编辑器执行
功能：第一根K线买入沪深A股第一只股票100股
预期：日志输出 [test] 委托已发送 且 QMT显示委托记录
"""
ACCOUNT_ID = '67014907'

def init(C):
    print("[test] init OK, 账号: %s" % ACCOUNT_ID)

def handlebar(C):
    if getattr(C, '_done', False):
        return
    C._done = True
    print("[test] 开始测试委托...")
    try:
        # 1. 获取股票列表
        codes = C.get_stock_list_in_sector("沪深A股")
        if not codes:
            print("[test] 无股票")
            return
        test_code = codes[0]
        print("[test] 目标: %s" % test_code)

        # 2. 获取实时价格
        tick = C.get_full_tick([test_code])
        if not tick or test_code not in tick:
            print("[test] 无行情")
            return
        price = tick[test_code].get("lastPrice", 0)
        print("[test] 价格: %.2f" % price)
        if price <= 0:
            print("[test] 价格无效")
            return

        # 3. 委托买入100股
        # passorder(操作类型, 下单方式, 账号, 代码, 选价类型, 价格, 数量, ContextInfo)
        # opType=23: 股票买入
        # orderType=1101: 单股单账号按股数
        # prType=11: 指定价
        passorder(23, 1101, ACCOUNT_ID, test_code, 11, price, 100, C)
        print("[test] 委托已发送: 买入 %s 100股 @ %.2f" % (test_code, price))

    except Exception as e:
        print("[test] 错误: %s" % e)
