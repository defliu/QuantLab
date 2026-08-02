"""
测试miniQMT连接

账号: 67014907
"""
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 60)
print("  QuantLab - miniQMT连接测试")
print("  账号: 67014907")
print("=" * 60)
print()

# 1. 检查xtquant库
print("[1/5] 检查xtquant库...")
try:
    from xtquant import xttrader, xtdata
    print("✅ xtquant库已安装")
except ImportError as e:
    print("❌ xtquant库未安装")
    print(f"   错误: {e}")
    print("\n解决方案:")
    print("  方案1: 从QMT安装目录复制")
    print("    copy E:\\国金QMT交易端模拟\\bin.x64\\Lib\\site-packages\\xtquant C:\\Python310\\Lib\\site-packages\\")
    print("  方案2: 使用QMT内置Python")
    sys.exit(1)

# 2. 检查QMT路径
print("\n[2/5] 检查QMT路径...")
import yaml

config_path = Path("config/trading_config.yaml")
if not config_path.exists():
    print(f"❌ 配置文件不存在: {config_path}")
    sys.exit(1)

with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

qmt_path = Path(config['account']['path'])
if qmt_path.exists():
    print(f"✅ QMT路径存在: {qmt_path}")
else:
    print(f"❌ QMT路径不存在: {qmt_path}")
    print(f"   请修改配置文件: {config_path}")
    print(f"   将 account.path 改为实际路径")
    sys.exit(1)

# 3. 尝试连接
print("\n[3/5] 尝试连接miniQMT...")
try:
    from xtquant.xttype import StockAccount
    
    session_id = config['account']['session_id']
    account_id = config['account']['id']
    
    trader = xttrader.XtQuantTrader(str(qmt_path), session_id)
    trader.start()
    
    result = trader.connect()
    if result != 0:
        print(f"❌ 连接失败，错误码: {result}")
        print("\n可能原因:")
        print("  1. miniQMT未启动")
        print("  2. 账号未登录")
        print("  3. 路径不正确")
        sys.exit(1)
    
    print("✅ 连接成功")
    
    # 4. 查询账户
    print("\n[4/5] 查询账户信息...")
    account = StockAccount(account_id)
    trader.subscribe(account)
    
    asset = trader.query_stock_asset(account)
    print(f"✅ 账户查询成功")
    print(f"   总资产: ¥{asset.total_asset:,.2f}")
    print(f"   可用资金: ¥{asset.cash:,.2f}")
    print(f"   持仓市值: ¥{asset.market_value:,.2f}")
    print(f"   冻结资金: ¥{asset.frozen_cash:,.2f}")
    
    # 5. 查询持仓
    print("\n[5/5] 查询持仓...")
    positions = trader.query_stock_positions(account)
    active_positions = [p for p in positions if p.volume > 0]
    
    print(f"✅ 持仓查询成功")
    print(f"   持仓数量: {len(active_positions)}只")
    
    if active_positions:
        print("\n   持仓明细:")
        for pos in active_positions[:10]:  # 显示前10只
            profit_pct = (pos.market_value / (pos.open_price * pos.volume) - 1) * 100 if pos.open_price > 0 else 0
            print(f"     {pos.stock_code}: {pos.volume}股, 盈亏{profit_pct:+.2f}%")
    
    # 断开
    trader.stop()
    
    print("\n" + "=" * 60)
    print("✅ 连接测试成功！")
    print("=" * 60)
    print("\n下一步:")
    print("  1. 配置通知webhook（可选）: config/trading_config.yaml → notification.webhook_url")
    print("  2. 启动实盘: start_trading.bat")
    print("  3. 或手动运行: python live_trading.py")
    
except Exception as e:
    print(f"\n❌ 连接异常: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
