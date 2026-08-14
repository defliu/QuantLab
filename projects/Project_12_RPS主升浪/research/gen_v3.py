# coding: utf-8
"""V3 优化配置生成：平衡过滤参数，尝试抓住牛市又控制回撤。

关键改进（基于诊断）：
  1. 回调放宽：pullback 2-25%（原来 2-15%）
  2. 板块 RPS 前 10（原来 5）
  3. 大盘门控 MA30（原来 MA60，更灵敏）
  4. RPS 阈值 85（原来 90）
"""
import sys
sys.path.insert(0, "D:/QuantLab")
import yaml
import copy

with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/strategy.yaml", "r", encoding="utf-8") as f:
    base = yaml.safe_load(f)

# V3 平衡版（全周期）
cfg = copy.deepcopy(base)
sp = cfg["strategy_params"]
sp["entry_mode"] = "pullback"
sp["pullback_min"] = 0.02
sp["pullback_max"] = 0.25
sp["pullback_ma_window"] = 20
sp["sector_top_n"] = 10
sp["rps_threshold"] = 85
sp["ma_window"] = 30       # 更灵敏的大盘门控
sp["keep_held"] = 1
sp["keep_threshold"] = 55
sp["max_holding_days"] = 90   # 主升浪允许持更久
sp["trailing_stop"] = -0.15   # 移动止盈放宽（让利润奔跑）
cfg["backtest"]["name"] = "rps_momentum_v3_balanced"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/strategy_v3_balanced.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)

# V3 快速版（2020-2021 诊断区间，验证持仓天数是否恢复）
cfg2 = copy.deepcopy(cfg)
cfg2["backtest"]["start_date"] = "2020-01-01"
cfg2["backtest"]["end_date"] = "2021-12-31"
cfg2["backtest"]["name"] = "v3_balanced_2020"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/v3_2020.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg2, f, allow_unicode=True, default_flow_style=False)

print("已生成 V3 平衡版配置：")
print("  1. strategy_v3_balanced.yaml - 全周期 2019-2025")
print("  2. v3_2020.yaml              - 2020-2021 快速验证")
print("\nV3 参数调整：")
print("  pullback: 2-25% (was 2-15%)")
print("  sector_top_n: 10 (was 5)")
print("  rps_threshold: 85 (was 90)")
print("  ma_window: 30 (was 60)")
print("  max_holding_days: 90 (was 60)")
print("  trailing_stop: -15% (was -12%)")
