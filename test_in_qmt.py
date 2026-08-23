"""
QMT连接测试脚本（在QMT中运行）

使用方法：
1. 启动QMT: E:\国金QMT交易端模拟\bin.x64\XtMiniQmt.exe
2. 登录账号: 70180771
3. 打开Python策略研究 → 新建策略 → 粘贴本代码 → 运行
"""

def test_connection():
    from xtquant import xttrader
    from xtquant.xttype import StockAccount
    
    # 配置
    qmt_path = r"E:\国金QMT交易端模拟\userdata_mini"
    session_id = 670149
    account_id = "70180771"
    
    print("=" * 60)
    print("  QMT连接测试 - 账号 70180771")
    print("=" * 60)
    
    # 连接
    trader = xttrader.XtQuantTrader(qmt_path, session_id)
    trader.start()
    
    result = trader.connect()
    if result != 0:
        print(f"❌ 连接失败，错误码: {result}")
        return
    
    print("✅ 连接成功")
    
    # 查询账户
    account = StockAccount(account_id)
    trader.subscribe(account)
    
    asset = trader.query_stock_asset(account)
    print(f"\n账户信息:")
    print(f"  总资产: ¥{asset.total_asset:,.2f}")
    print(f"  可用资金: ¥{asset.cash:,.2f}")
    print(f"  持仓市值: ¥{asset.market_value:,.2f}")
    
    # 查询持仓
    positions = trader.query_stock_positions(account)
    active = [p for p in positions if p.volume > 0]
    print(f"\n持仓: {len(active)}只")
    
    trader.stop()
    print("\n✅ 测试完成")

# 运行
test_connection()
