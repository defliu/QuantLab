# coding: utf-8
"""对照测试：关闭大盘门控 + 放宽选股，看持仓天数是否恢复。

验证假设：策略"几乎全年空仓"是因为过滤太严。
"""
import sys
sys.path.insert(0, "D:/QuantLab")
import yaml
import copy

# 读取主配置并修改
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/strategy.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# 变体 1：关掉大盘门控
cfg1 = copy.deepcopy(cfg)
cfg1["strategy_params"]["market_gate"] = 0
cfg1["backtest"]["name"] = "rps_momentum_v2_nogate"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/variant_nogate.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg1, f, allow_unicode=True, default_flow_style=False)

# 变体 2：放宽回调（回调 5-25% + 均线容忍 10%）
cfg2 = copy.deepcopy(cfg)
cfg2["strategy_params"]["pullback_min"] = 0.05
cfg2["strategy_params"]["pullback_max"] = 0.25
cfg2["backtest"]["name"] = "rps_momentum_v2_loose_pullback"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/variant_loose.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg2, f, allow_unicode=True, default_flow_style=False)

# 变体 3：降低 RPS 阈值到 80
cfg3 = copy.deepcopy(cfg)
cfg3["strategy_params"]["rps_threshold"] = 80
cfg3["backtest"]["name"] = "rps_momentum_v2_rps80"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/variant_rps80.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg3, f, allow_unicode=True, default_flow_style=False)

# 变体 4：关掉板块 RPS
cfg4 = copy.deepcopy(cfg)
cfg4["strategy_params"]["sector_rps_enabled"] = 0
cfg4["backtest"]["name"] = "rps_momentum_v2_nosector"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/variant_nosector.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg4, f, allow_unicode=True, default_flow_style=False)

print("已生成 4 个变体配置：")
print("  1. variant_nogate.yaml    - 关闭大盘门控")
print("  2. variant_loose.yaml     - 放宽回调（5-25%）")
print("  3. variant_rps80.yaml     - RPS 阈值降到 80")
print("  4. variant_nosector.yaml  - 关闭板块 RPS")
