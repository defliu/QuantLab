# coding=utf-8
"""Panel缓存脚本：一次性加载并保存，后续所有脚本直接读缓存
预计耗时: <30秒
"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')

from research.multi_factor_ic.data_loader import load_universe, build_panel
import pandas as pd

CACHE_DIR = "D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

print("[缓存] 加载universe...")
codes = load_universe()

print("[缓存] 构建面板...")
panel, fin = build_panel(codes)

print(f"[缓存] 面板形状: {panel.shape}，保存到 parquet...")
panel.to_parquet(f"{CACHE_DIR}/panel.parquet")
fin.to_parquet(f"{CACHE_DIR}/fin.parquet")
print(f"[缓存] 完成！后续脚本直接从 {CACHE_DIR} 加载，节省每次约20秒")

# 验证缓存
print("\n[验证] 读取缓存测试...")
panel_cache = pd.read_parquet(f"{CACHE_DIR}/panel.parquet")
print(f"[验证] 缓存加载成功: {panel_cache.shape}")
