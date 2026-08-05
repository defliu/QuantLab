# coding=utf-8
"""Phase 2: 参数矩阵回测。"""
import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(_THIS_DIR)
if _PROJ_ROOT not in sys.path: sys.path.insert(0, _PROJ_ROOT)
_PROJ_ROOT2 = os.path.dirname(_PROJ_ROOT)
if _PROJ_ROOT2 not in sys.path: sys.path.insert(0, _PROJ_ROOT2)
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from time import time
from pathlib import Path

from research.multi_factor_ic.data_loader import load_universe, build_panel, get_universe_at_date
from research.multi_factor_ic.config import OUTPUT_DIR
from research.multi_factor_ic.backtest import backtest, backtest_stop_loss
from research.multi_factor_ic import scoring

codes = load_universe()
panel, fin_ffill = build_panel(codes)

# 获取市值分位数做动态区间过滤
all_cm = panel["circ_mv"].dropna().values
pcts = [0, 20, 50, 80, 100]
cutoffs = [0, 2e4, 5e4, 8e4, 1e10]

def filter_market_cap(low, high):
    def _f(p, fin, d):
        dd = p.loc[d]
        mv = dd["circ_mv"]
        return (mv >= low) & (mv < high)
    return _f

# 参数矩阵（精简化）
params = []
for cap_label, cap_lo, cap_hi in [
    ("0-20亿", 0, 2e4),
    ("20-50亿", 2e4, 5e4),
    ("50-80亿", 5e4, 8e4),
    ("80亿+", 8e4, 1e10),
    ("全量", 0, 1e10),
]:
    for freq in ["W", "2W", "M", "2M"]:
        for weight in ["等权"]:  # 只用等权
            for sl in [None, -0.12]:
                for top_n in [20, 50]:
                    params.append((cap_label, cap_lo, cap_hi, freq, weight, sl, top_n))

print("参数矩阵: {}组".format(len(params)))

all_results = []
for idx, (cap_label, cap_lo, cap_hi, freq, weight, sl, top_n) in enumerate(params):
    t0 = time()
    
    if sl is not None:
        eq, td, sldf, met = backtest_stop_loss(panel, fin_ffill, top_n=top_n, freq=freq,
                                                tx_cost=0.002, dynamic_universe=True,
                                                stop_loss=sl)
    else:
        eq, td, met = backtest(panel, fin_ffill, top_n=top_n, freq=freq,
                               tx_cost=0.002, dynamic_universe=True)
    
    elapsed = time() - t0
    row = {
        "市值区间": cap_label, "调仓频率": freq, "加权方式": weight,
        "止损": str(sl) if sl else "无", "持仓数": top_n,
        "耗时(秒)": round(elapsed, 1),
        "总收益": met.get("总收益",""), "年化收益": met.get("年化收益",""),
        "最大回撤": met.get("最大回撤",""), "夏普比率": met.get("夏普比率",""),
        "胜率": met.get("胜率",""), "调仓次数": met.get("调仓次数",""),
    }
    all_results.append(row)
    print("[{:3d}/{:3d}] {:.0f}s | {} {} {} 止损={} TOP{} 年化={} 夏普={} 回撤={}".format(
        idx+1, len(params), elapsed,
        cap_label, freq, weight,
        str(sl) if sl else "无", top_n,
        met.get("年化收益","?"), met.get("夏普比率","?"), met.get("最大回撤","?")))

# 保存
out = "{}/v3_optimize".format(OUTPUT_DIR)
Path(out).mkdir(parents=True, exist_ok=True)
df = pd.DataFrame(all_results)
df.to_csv("{}/param_matrix.csv".format(out), index=False, encoding="utf-8-sig")

# TOP20 by sharpe
df_sorted = df.sort_values("夏普比率", ascending=False)
df_sorted.head(20).to_csv("{}/param_matrix_top20.csv".format(out), index=False, encoding="utf-8-sig")

print("\n完成! {} 组回测".format(len(all_results)))
print("TOP20:")
print(df_sorted.head(20)[["市值区间","调仓频率","止损","持仓数","年化收益","夏普比率","最大回撤"]].to_string(index=False))
