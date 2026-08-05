# coding=utf-8
"""Phase 3.1: 因子权重优化（等权 / IC加权 / 风险平价）。"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')
import time
import pandas as pd
import numpy as np
from pathlib import Path

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest

OUT = 'D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize'

WEIGHTS = {
    '等权': {'BP': 0.25, 'reversal_1m': 0.25, 'volatility_60d': 0.25, 'ROE': 0.25},
    '原权重': {'BP': 0.30, 'reversal_1m': 0.25, 'volatility_60d': 0.25, 'ROE': 0.20},
    'IC加权': {'BP': 0.40, 'reversal_1m': 0.10, 'volatility_60d': 0.35, 'ROE': 0.15},
    'BP主导': {'BP': 0.50, 'reversal_1m': 0.15, 'volatility_60d': 0.20, 'ROE': 0.15},
    '低波主导': {'BP': 0.20, 'reversal_1m': 0.15, 'volatility_60d': 0.50, 'ROE': 0.15},
}

MV_FILTER = lambda p, f, d: (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)

codes = load_universe()
panel, fin_ffill = build_panel(codes)
print('面板加载完成: {}'.format(panel.shape))

results = []
total = len(WEIGHTS)
for i, (wname, wdict) in enumerate(WEIGHTS.items()):
    t0 = time.time()
    eq, td, met = backtest(panel, fin_ffill, top_n=50, freq='2M', tx_cost=0.002,
                           dynamic_universe=True, filter_func=MV_FILTER, weights=wdict)
    results.append({
        '权重方案': wname,
        '年化收益': met.get('年化收益', ''),
        '夏普比率': met.get('夏普比率', ''),
        '最大回撤': met.get('最大回撤', ''),
        '胜率': met.get('胜率', ''),
        '耗时(秒)': round(time.time()-t0, 1),
    })
    print('[{}/{}] {} {:.0f}s 年化={} 夏普={}'.format(
        i+1, total, wname, time.time()-t0, met.get('年化收益'), met.get('夏普比率')))

df = pd.DataFrame(results)
df.to_csv('{}/factor_weights.csv'.format(OUT), index=False, encoding='utf-8-sig')
print('\n完成! 权重对比已保存: {}/factor_weights.csv'.format(OUT))
print(df.to_string(index=False))
