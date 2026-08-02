"""
一键配置脚本 - QuantLab实盘环境
账号: 67014907
"""
import sys
import shutil
from pathlib import Path

print("=" * 60)
print("  QuantLab 一键配置")
print("  账号: 67014907")
print("=" * 60)
print()

# 1. 查找xtquant源路径
print("[1/4] 查找xtquant源路径...")
possible_paths = [
    Path("E:/国金QMT交易端模拟/bin.x64/Lib/site-packages/xtquant"),
    Path("E:/国金QMT交易端模拟/Lib/site-packages/xtquant"),
    Path("D:/miniQMT/userdata_mini/Lib/site-packages/xtquant"),
]

xtquant_src = None
for p in possible_paths:
    if p.exists():
        xtquant_src = p
        print(f"✅ 找到: {p}")
        break

if not xtquant_src:
    print("❌ 未找到xtquant源路径")
    print("\n请手动指定路径:")
    print("  例如: E:\\国金QMT交易端模拟\\bin.x64\\Lib\\site-packages\\xtquant")
    sys.exit(1)

# 2. 复制xtquant到系统Python
print("\n[2/4] 复制xtquant到系统Python...")
site_packages = Path(sys.path[3])  # 通常第4个是site-packages
xtquant_dst = site_packages / "xtquant"

if xtquant_dst.exists():
    print(f"⚠️  目标已存在: {xtquant_dst}")
    choice = input("   是否覆盖? (y/n): ")
    if choice.lower() != 'y':
        print("   跳过复制")
    else:
        shutil.rmtree(xtquant_dst)
        shutil.copytree(xtquant_src, xtquant_dst)
        print(f"✅ 已覆盖: {xtquant_dst}")
else:
    shutil.copytree(xtquant_src, xtquant_dst)
    print(f"✅ 已复制: {xtquant_dst}")

# 3. 验证安装
print("\n[3/4] 验证xtquant安装...")
try:
    from xtquant import xttrader, xtdata
    print("✅ xtquant导入成功")
except ImportError as e:
    print(f"❌ xtquant导入失败: {e}")
    sys.exit(1)

# 4. 创建必要目录
print("\n[4/4] 创建必要目录...")
dirs = [
    Path("E:/QuantLab/logs"),
    Path("E:/QuantLab/data"),
    Path("E:/QuantLab/data/cache"),
]
for d in dirs:
    d.mkdir(parents=True, exist_ok=True)
    print(f"✅ {d}")

print("\n" + "=" * 60)
print("✅ 配置完成！")
print("=" * 60)
print("\n下一步:")
print("  1. 启动QMT: E:\\国金QMT交易端模拟\\bin.x64\\XtMiniQmt.exe")
print("  2. 登录账号: 67014907")
print("  3. 测试连接: python test_connection.py")
print("  4. 启动实盘: start_trading.bat")
