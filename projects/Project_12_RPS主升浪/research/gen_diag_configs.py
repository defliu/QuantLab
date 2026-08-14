# coding: utf-8
"""生成缩短区间（2020-2021）的变体测试配置，快速定位"几乎全年空仓"根因。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import yaml
import copy

with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/strategy.yaml", "r", encoding="utf-8") as f:
    base = yaml.safe_load(f)

base["backtest"]["start_date"] = "2020-01-01"
base["backtest"]["end_date"] = "2021-12-31"

# baseline（原配置）
cfg0 = copy.deepcopy(base)
cfg0["backtest"]["name"] = "diag_baseline"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/diag_baseline.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg0, f, allow_unicode=True, default_flow_style=False)

# 1. 关闭大盘门控
cfg1 = copy.deepcopy(base)
cfg1["strategy_params"]["market_gate"] = 0
cfg1["backtest"]["name"] = "diag_nogate"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/diag_nogate.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg1, f, allow_unicode=True, default_flow_style=False)

# 2. 放宽回调
cfg2 = copy.deepcopy(base)
cfg2["strategy_params"]["pullback_min"] = 0.03
cfg2["strategy_params"]["pullback_max"] = 0.25
cfg2["backtest"]["name"] = "diag_loose_pullback"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/diag_loose.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg2, f, allow_unicode=True, default_flow_style=False)

# 3. RPS 阈值 80
cfg3 = copy.deepcopy(base)
cfg3["strategy_params"]["rps_threshold"] = 80
cfg3["backtest"]["name"] = "diag_rps80"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/diag_rps80.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg3, f, allow_unicode=True, default_flow_style=False)

# 4. 关闭板块 RPS
cfg4 = copy.deepcopy(base)
cfg4["strategy_params"]["sector_rps_enabled"] = 0
cfg4["backtest"]["name"] = "diag_nosector"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/diag_nosector.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg4, f, allow_unicode=True, default_flow_style=False)

# 5. 关闭板块RPS + 关闭大盘门控（最宽松）
cfg5 = copy.deepcopy(base)
cfg5["strategy_params"]["sector_rps_enabled"] = 0
cfg5["strategy_params"]["market_gate"] = 0
cfg5["backtest"]["name"] = "diag_loose_all"
with open("D:/QuantLab/projects/Project_12_RPS主升浪/config/diag_loose_all.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg5, f, allow_unicode=True, default_flow_style=False)

print("已生成 6 个诊断配置（2020-2021）：")
for name in ["diag_baseline", "diag_nogate", "diag_loose", "diag_rps80",
             "diag_nosector", "diag_loose_all"]:
    print("  %s.yaml" % name)
