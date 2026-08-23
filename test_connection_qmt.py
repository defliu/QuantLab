"""
测试连接 - 使用QMT Python
账号: 70180771
"""
import sys
from pathlib import Path

print("=" * 60)
print("  QuantLab - QMT连接测试")
print("  账号: 70180771")
print("=" * 60)
print()

# 1. 检查环境
print("[1/5] 检查Python环境...")
print(f"   Python版本: {sys.version}")
print(f"   Python路径: {sys.executable}")

# 2. 检查xtquant
print("\n[2/5] 检查xtquant库...")
try:
    from xtquant import xttrader, xtdata
    from xtquant.xttype import StockAccount
    print("✅ xtquant库已加载")
except ImportError as e:
    print(f"❌ xtquant库导入失败: {e}")
    print("\n解决方案:")
    print("  必须使用QMT内置Python运行:")
    print("    E:\\国金QMT交易端模拟\\bin.x64\\python3\\python.exe test_connection_qmt.py")
    sys.exit(1)

# 3. 加载配置
print("\n[3/5] 加载配置...")
config_path = Path("config/trading_config.yaml")
if not config_path.exists():
    print(f"❌ 配置文件不存在: {config_path}")
    sys.exit(1)

import yaml
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

qmt_path = config['account']['path']
session_id = config['account']['session_id']
account_id = config['account']['id']

print(f"✅ 配置已加载")
print(f"   QMT路径: {qmt_path}")
print(f"   会话ID: {session_id}")
print(f"   账号: {account_id}")

# 4. 连接QMT
print("\n[4/5] 连接QMT...")
try:
    trader = xttrader.XtQuantTrader(qmt_path, session_id)
    trader.start()
    
    result = trader.connect()
    if result != 0:
        print(f"❌ 连接失败，错误码: {result}")
        print("\n可能原因:")
        print("  1. QMT未启动 → 请启动: E:\\国金QMT交易端模拟\\bin.x64\\XtMiniQmt.exe")
        print("  2. 账号未登录 → 请在QMT中登录账号 70180771")
        print("  3. 路径错误 → 检查config/trading_config.yaml中的account.path")
        sys.exit(1)
    
    print("✅ QMT连接成功")
    
    # 订阅账户
    account = StockAccount(account_id)
    trader.subscribe(account)
    
    # 5. 查询账户信息
    print("\n[5/5] 查询账户信息...")
    asset = trader.query_stock_asset(account)
    
    print("\n" + "=" * 60)
    print("✅ 连接测试成功！")
    print("=" * 60)
    print("\n账户信息:")
    print(f"  总资产:   ¥{asset.total_asset:>15,.2f}")
    print(f"  可用资金: ¥{asset.cash:>15,.2f}")
    print(f"  持仓市值: ¥{asset.market_value:>15,.2f}")
    print(f"  冻结资金: ¥{asset.frozen_cash:>15,.2f}")
    
    # 查询持仓
    positions = trader.query_stock_positions(account)
    active_pos = [p for p in positions if p.volume > 0]
    
    print(f"\n持仓数量: {len(active_pos)}只")
    if active_pos:
        print("\n持仓明细:")
        print(f"{'代码':<12} {'数量':>8} {'成本价':>10} {'市值':>12} {'盈亏%':>8}")
        print("-" * 60)
        for pos in active_pos[:15]:
            profit_pct = (pos.market_value / (pos.open_price * pos.volume) - 1) * 100 if pos.open_price > 0 else 0
            print(f"{pos.stock_code:<12} {pos.volume:>8} {pos.open_price:>10.2f} {pos.market_value:>12,.0f} {profit_pct:>+7.2f}%")
    
    # 断开
    trader.stop()
    
    print("\n" + "=" * 60)
    print("下一步:")
    print("  1. 配置通知webhook（可选）")
    print("     编辑: config/trading_config.yaml → notification.webhook_url")
    print()
    print("  2. 启动实盘")
    print("     方式1: start_trading_qmt.bat")
    print("     方式2: E:\\国金QMT交易端模拟\\bin.x64\\python3\\python.exe live_trading.py")
    print("=" * 60)
    
except Exception as e:
    print(f"\n❌ 异常: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
