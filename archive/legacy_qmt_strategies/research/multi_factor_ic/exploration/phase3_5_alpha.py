# coding=utf-8
"""Phase 3.5: 新alpha源探索（不同因子组合对比）。"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')
import time
import pandas as pd
import numpy as np

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest

OUT = 'D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize'

MV_FILTER = lambda p, f, d: (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)

codes = load_universe()
panel, fin_ffill = build_panel(codes)
print('面板加载完成: {}'.format(panel.shape))

# 因子组合方案（基于现有因子）
COMBOS = {
    '基础4因子': {'BP': 0.30, 'reversal_1m': 0.25, 'volatility_60d': 0.25, 'ROE': 0.20},
    '价值+质量': {'BP': 0.40, 'EP': 0.20, 'ROE': 0.20, 'grossprofit_margin': 0.20},
    '动量+低波': {'momentum_1m': 0.35, 'momentum_3m': 0.15, 'volatility_60d': 0.35, 'turnover_change': 0.15},
    '全因子等权': {k: 1.0/11 for k in ['EP','BP','dividend_yield','ROE','grossprofit_margin',
                                        'momentum_1m','momentum_3m','momentum_6m','turnover_change',
                                        'volatility_60d','liquidity_avg']},
    '价值+低波(无动量)': {'BP': 0.40, 'EP': 0.20, 'volatility_60d': 0.30, 'dividend_yield': 0.10},
}

results = []
total = len(COMBOS)
for i, (cname, cdict) in enumerate(COMBOS.items()):
    t0 = time.time()
    eq, td, met = backtest(panel, fin_ffill, top_n=50, freq='2M', tx_cost=0.002,
                           dynamic_universe=True, filter_func=MV_FILTER, weights=cdict)
    results.append({
        '因子组合': cname,
        '年化收益': met.get('年化收益', ''),
        '夏普比率': met.get('夏普比率', ''),
        '最大回撤': met.get('最大回撤', ''),
        '胜率': met.get('胜率', ''),
        '耗时(秒)': round(time.time()-t0, 1),
    })
    print('[{}/{}] {} {:.0f}s 年化={} 夏普={}'.format(
        i+1, total, cname, time.time()-t0, met.get('年化收益'), met.get('夏普比率')))

df = pd.DataFrame(results)
df.to_csv('{}/factor_combos.csv'.format(OUT), index=False, encoding='utf-8-sig')
print('\n完成! 因子组合对比已保存: {}/factor_combos.csv'.format(OUT))
print(df.to_string(index=False))
