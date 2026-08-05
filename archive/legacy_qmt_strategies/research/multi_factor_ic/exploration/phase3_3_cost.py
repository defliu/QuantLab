# coding=utf-8
"""Phase 3.3: 交易成本敏感性。"""
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

# 测试不同单边费率
COSTS = [0.001, 0.002, 0.003, 0.005]
results = []
for cost in COSTS:
    t0 = time.time()
    # 小市值最优组合：双月 + TOP50 + 止损-12%
    eq, td, sldf, met = backtest_stop_loss = __import__(
        'research.multi_factor_ic.backtest', fromlist=['backtest_stop_loss']
    ).backtest_stop_loss(panel, fin_ffill, top_n=50, freq='2M', tx_cost=cost,
                         dynamic_universe=True, stop_loss=-0.12, filter_func=MV_FILTER)
    results.append({
        '单边费率': '{:.1f}‰'.format(cost*1000),
        '年化收益': met.get('年化收益', ''),
        '夏普比率': met.get('夏普比率', ''),
        '最大回撤': met.get('最大回撤', ''),
        '耗时(秒)': round(time.time()-t0, 1),
    })
    print('费率{:.1f}‰ {:.0f}s 年化={} 夏普={}'.format(
        cost*1000, time.time()-t0, met.get('年化收益'), met.get('夏普比率')))

df = pd.DataFrame(results)
df.to_csv('{}/cost_sensitivity.csv'.format(OUT), index=False, encoding='utf-8-sig')
print('\n完成! 成本敏感性已保存: {}/cost_sensitivity.csv'.format(OUT))
print(df.to_string(index=False))
