# coding=gbk
"""QMT 财务数据获取测试

测试 QMT 的 get_financial_data 和 get_market_data_ex 能否获取 pb/pe_ttm/circ_mv
如果可以，策略就不需要依赖本地 CSV
"""
ACCOUNT_ID = '67014907'

def init(C):
    print("[fin_test] init OK")

def handlebar(C):
    if getattr(C, '_done', False):
        return
    C._done = True

    print("[fin_test] ====== 财务数据测试 ======")

    test_codes = ['000001.SZ', '600519.SH', '000858.SZ']

    # ====== 测试1: get_market_data_ex 各字段 ======
    print("[fin_test] --- 测试1: get_market_data_ex ---")
    fields = ["close", "open", "volume", "amount", "pe_ttm", "pb", "circ_mv", "pct_chg"]
    try:
        data = C.get_market_data_ex(fields, test_codes, period="1d", count=5)
        for code in test_codes:
            if code in data:
                d = data[code]
                for f in fields:
                    v = d.get(f, None)
                    if v is not None:
                        if hasattr(v, '__len__'):
                            vals = [x for x in v if x is not None and not (isinstance(x, float) and x != x)]
                            valid = len(vals) > 0
                        else:
                            valid = v is not None
                        print("[fin_test]   %s.%s: %s (valid=%s)" % (code, f, type(v).__name__, valid))
                    else:
                        print("[fin_test]   %s.%s: None" % (code, f))
    except Exception as e:
        print("[fin_test] get_market_data_ex 错误: %s" % e)

    # ====== 测试2: get_financial_data ======
    print("[fin_test] --- 测试2: get_financial_data ---")
    fin_fields = [
        ("PERSHAREINDEX.du_return_on_equity", "ROE"),
        ("PERSHAREINDEX.eps", "EPS"),
        ("PERSHAREINDEX.bps", "BPS"),
        ("BALANCE.total_share", "总股本"),
        ("BALANCE.float_share", "流通股本"),
    ]
    for field_id, label in fin_fields:
        try:
            fin = C.get_financial_data([field_id], [test_codes[0]], "20260101", "20260701")
            if fin is not None:
                if hasattr(fin, 'empty'):
                    is_empty = fin.empty
                    if not is_empty:
                        print("[fin_test]   %s: PASS shape=%s" % (label, str(fin.shape)))
                    else:
                        print("[fin_test]   %s: EMPTY" % label)
                else:
                    print("[fin_test]   %s: type=%s" % (label, type(fin).__name__))
            else:
                print("[fin_test]   %s: None" % label)
        except Exception as e:
            print("[fin_test]   %s: ERROR (%s)" % (label, str(e)[:50]))

    # ====== 测试3: get_full_tick 实时数据 ======
    print("[fin_test] --- 测试3: get_full_tick ---")
    try:
        tick = C.get_full_tick(test_codes[:1])
        if tick and test_codes[0] in tick:
            t = tick[test_codes[0]]
            print("[fin_test]   lastPrice: %s" % t.get('lastPrice'))
            print("[fin_test]   amount: %s" % t.get('amount'))
        else:
            print("[fin_test]   无数据")
    except Exception as e:
        print("[fin_test]   错误: %s" % e)

    print("[fin_test] ====== 测试完成 ======")
