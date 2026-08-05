# coding=utf-8
"""10万本金：成交额阈值1000万对比组。"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')
import time, pandas as pd, numpy as np

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest_stop_loss

OUT = 'D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize'
codes = load_universe()
panel, fin = build_panel(codes)
CAPITAL = 100000

def make_filter(min_amount_k=10000):
    def _f(p, f, d):
        mv_mask = (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)
        trade_dates = sorted(p.index.get_level_values('trade_date').unique())
        di = trade_dates.index(d)
        if di < 20:
            return mv_mask
        start_d = trade_dates[di-20]
        window = p.loc[start_d:d]
        avg_amt = window.groupby('ts_code')['amount'].mean()
        liq_mask = avg_amt > min_amount_k
        liq_mask = liq_mask.reindex(p.loc[d].index, fill_value=False)
        return mv_mask & liq_mask
    return _f

t0 = time.time()
eq, td, sl, met = backtest_stop_loss(
    panel, fin, top_n=80, freq='2M', tx_cost=0.0015,
    dynamic_universe=True, stop_loss=-0.12,
    filter_func=make_filter(10000),
    max_weight_per_stock=0.02)

ann = met.get('年化收益', '')
sharpe = met.get('夏普比率', '')
mdd = met.get('最大回撤', '')
total_ret = met.get('总收益', '')
print('=== 10万本金 + 阈值1000万 ===')
print('  年化: {} 夏普: {} 回撤: {} 总收益: {}'.format(ann, sharpe, mdd, total_ret))
print('  胜率: {} 调仓: {} 止损: {}'.format(met.get('胜率',''), met.get('调仓次数',''), met.get('止损次数','')))
print('  耗时: {:.0f}秒'.format(time.time()-t0))
if eq is not None and len(eq) > 0:
    eq2 = eq.copy()
    eq2['portfolio_value_10w'] = eq2['portfolio_value'] * CAPITAL
    eq2.to_csv('{}/backtest_10w_1000w_capital.csv'.format(OUT), index=False, encoding='utf-8-sig')
    final = eq2['portfolio_value_10w'].iloc[-1]
    print('  10万终值: {:.0f}元 累计: {:.0f}元'.format(final, final-CAPITAL))
pd.DataFrame([{
    '本金':'10万元','市值区间':'0-30亿','成交额阈值':'1000万','成本':'0.15%','单票上限':'2%',
    '年化收益':ann,'夏普比率':sharpe,'最大回撤':mdd,'总收益':total_ret,
    '胜率':met.get('胜率',''),'调仓次数':met.get('调仓次数',''),'止损次数':met.get('止损次数',''),
}]).to_csv('{}/backtest_10w_1000w_result.csv'.format(OUT), index=False, encoding='utf-8-sig')
print('保存: {}/backtest_10w_1000w_result.csv'.format(OUT))
