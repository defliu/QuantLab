# coding: utf-8
"""miniQMT 连通性测试（不下单）：行情通道 + 交易通道 + 账户查询
非交易时段可安全运行，验证 TraeWork -> miniQMT -> 柜台的链路是否通。
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmt_config as C
sys.path.append(C.XTPACK)

def test_quote(xtdata, code):
    """行情通道：实时 tick + 日线"""
    ok = True
    try:
        xtdata.subscribe_quote(code, period="tick", count=-1)
        time.sleep(1.0)
        tick = xtdata.get_full_tick([code]).get(code)
        if tick and tick.get("lastPrice"):
            print(f"    ✅ 实时tick: {code}  lastPrice={tick['lastPrice']:.2f}  vol={tick.get('volume',0)}  ts={tick.get('time')}")
        else:
            print(f"    ⚠️ 实时tick为空（非交易时段属正常，仅说明行情订阅通道已通）")
        # 日线（验证本地行情数据服务）
        k = xtdata.get_market_data_ex([], [code], period="1d", count=5)
        df = k.get(code)
        if df is not None and len(df):
            print(f"    ✅ 日线: {code} 最近{len(df)}根, 最新close={df['close'].iloc[-1]:.2f}")
        else:
            print(f"    ⚠️ 日线为空")
    except Exception as e:
        print(f"    ❌ 行情异常: {e}")
        ok = False
    return ok

def test_trade(xttrader, xttype):
    """交易通道：连接 + 订阅账号 + 查资产/持仓"""
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    res = trader.connect()
    if res != 0:
        print(f"    ❌ 交易连接失败 code={res}（0=成功；-1=连接未建立；-7=账号未登录）")
        trader.stop()
        return False
    print(f"    ✅ 交易连接成功 (connect={res})")
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    time.sleep(0.5)
    # 查资金
    try:
        asset = trader.query_stock_asset(account)
        if asset:
            print(f"    ✅ 账号 {C.ACCOUNT_ID} 资金: 总资产={asset.total_asset:.0f} 可用={asset.cash:.0f} 市值={asset.market_value:.0f} 持仓盈亏={asset.frozen_cash if hasattr(asset,'frozen_cash') else 0}")
        else:
            print(f"    ⚠️ 查资金返回空")
    except Exception as e:
        print(f"    ❌ 查资金异常: {e}")
    # 查持仓
    try:
        pos = trader.query_stock_positions(account)
        n = len(pos or [])
        print(f"    ✅ 持仓数: {n}")
        for p in (pos or [])[:5]:
            print(f"      {p.stock_code} {p.volume}股 成本{p.open_price:.2f} 市值{p.market_value:.0f} 可用{p.can_use_volume}")
    except Exception as e:
        print(f"    ❌ 查持仓异常: {e}")
    trader.stop()
    return True

if __name__ == "__main__":
    print("=" * 50)
    print("miniQMT 链路连通性测试（不下单）")
    print(f"QMT_PATH = {C.QMT_PATH}")
    print(f"USERDATA = {C.USERDATA}")
    print(f"ACCOUNT  = {C.ACCOUNT_ID}")
    print("=" * 50)

    from xtquant import xtdata, xttrader, xttype

    print("\n[1] 行情通道 ...")
    # 用 V1.1 候选相关的票 + 一个常见票
    q_ok = test_quote(xtdata, "603969.SH")
    q_ok2 = test_quote(xtdata, "002237.SZ")
    q_ok = q_ok and q_ok2

    print("\n[2] 交易通道 ...")
    t_ok = test_trade(xttrader, xttype)

    print("\n" + "=" * 50)
    print(f"行情通道: {'✅ 通' if q_ok else '❌ 不通'}")
    print(f"交易通道: {'✅ 通' if t_ok else '❌ 不通'}")
    if q_ok and t_ok:
        print("✅✅ 全链路通（TraeWork -> miniQMT -> 柜台）")
    else:
        print("⚠️ 存在异常，见上方输出")
    print("=" * 50)
