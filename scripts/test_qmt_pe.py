# coding=gbk
"""QMT pe 字段测试

QMT文档显示 Tick/Bar 对象有 pe 字段（市盈率），不是 pe_ttm
测试 get_market_data_ex 能否获取 pe 字段
"""

def init(C):
    print("[pe_test] init OK")

def handlebar(C):
    if getattr(C, '_done', False):
        return
    C._done = True

    print("[pe_test] ====== pe字段测试 ======")
    test_codes = ['000001.SZ', '600519.SH']

    # 测试1: get_market_data_ex 用 pe 字段名
    print("[pe_test] --- 测试1: get_market_data_ex (pe) ---")
    try:
        data = C.get_market_data_ex(
            ["close", "pe", "volume", "amount"],
            test_codes, period="1d", count=5
        )
        for code in test_codes:
            if code in data:
                d = data[code]
                for f in ["close", "pe", "volume", "amount"]:
                    v = d.get(f, None)
                    if v is not None:
                        if hasattr(v, '__len__'):
                            vals = [x for x in v if x is not None and not (isinstance(x, float) and x != x)]
                            print("[pe_test]   %s.%s: %s (valid=%d)" % (code, f, type(v).__name__, len(vals)))
                        else:
                            print("[pe_test]   %s.%s: %s" % (code, f, v))
    except Exception as e:
        print("[pe_test] 错误: %s" % e)

    # 测试2: get_full_tick 的 pe 字段
    print("[pe_test] --- 测试2: get_full_tick (pe) ---")
    try:
        tick = C.get_full_tick(test_codes[:1])
        if tick and test_codes[0] in tick:
            t = tick[test_codes[0]]
            print("[pe_test]   lastPrice: %s" % t.get('lastPrice'))
            print("[pe_test]   pe: %s" % t.get('pe'))
            print("[pe_test]   所有字段: %s" % list(t.keys()))
    except Exception as e:
        print("[pe_test] 错误: %s" % e)

    # 测试3: 用 get_financial_data 获取 BPS 计算 pb
    print("[pe_test] --- 测试3: BPS (计算pb) ---")
    try:
        fin = C.get_financial_data(
            ["PERSHAREINDEX.bps"],
            [test_codes[0]],
            "20260101", "20260701"
        )
        if fin is not None and hasattr(fin, 'empty') and not fin.empty:
            # 获取最新值
            bps_val = fin.iloc[-1, 0] if len(fin) > 0 else None
            print("[pe_test]   BPS: %s" % bps_val)
            # 获取当前价格计算 pb
            tick = C.get_full_tick([test_codes[0]])
            if tick and test_codes[0] in tick:
                price = tick[test_codes[0]].get('lastPrice', 0)
                if price > 0 and bps_val and not (isinstance(bps_val, float) and bps_val != bps_val):
                    pb = price / bps_val
                    print("[pe_test]   计算PB: %.2f / %.2f = %.2f" % (price, bps_val, pb))
    except Exception as e:
        print("[pe_test] 错误: %s" % e)

    print("[pe_test] ====== 测试完成 ======")
