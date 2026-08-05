# coding=utf-8
"""10万本金实盘参数回测：阈值2000万 + 0.15%成本 + 单票上限2%。"""
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

CAPITAL = 100000  # 10万本金

# 阈值2000万：日均成交额 > 2000万（amount单位=千元，故阈值=20000千元）
# 0-30亿小市值 + 前20日日均成交额 > 2000万
def make_filter(min_amount_k=20000):
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
    panel, fin, top_n=80, freq='2M', tx_cost=0.0015,  # 0.15%成本
    dynamic_universe=True, stop_loss=-0.12,
    filter_func=make_filter(20000),  # 阈值2000万
    max_weight_per_stock=0.02)  # 单票上限2%

ann = met.get('年化收益', '')
sharpe = met.get('夏普比率', '')
mdd = met.get('最大回撤', '')
total_ret = met.get('总收益', '')

print('=== 10万本金实盘参数回测 ===')
print('  本金: 10万元')
print('  参数: 0-30亿 + 双月 + TOP80 + 止损-12% + 成交额>2000万 + 成本0.15% + 单票≤2%')
print('  年化收益: {}'.format(ann))
print('  夏普比率: {}'.format(sharpe))
print('  最大回撤: {}'.format(mdd))
print('  总收益: {}'.format(total_ret))
print('  胜率: {}'.format(met.get('胜率', '')))
print('  调仓次数: {}'.format(met.get('调仓次数', '')))
print('  止损次数: {}'.format(met.get('止损次数', '')))
print('  耗时: {:.0f}秒'.format(time.time()-t0))

# 计算实际资金曲线（10万本金）
if eq is not None and len(eq) > 0:
    equity_10w = eq.copy()
    equity_10w['portfolio_value_10w'] = equity_10w['portfolio_value'] * CAPITAL
    equity_10w.to_csv('{}/backtest_10w_capital.csv'.format(OUT), index=False, encoding='utf-8-sig')
    final_val = equity_10w['portfolio_value_10w'].iloc[-1]
    print('  10万本金终值: {:.0f}元'.format(final_val))
    print('  累计收益: {:.0f}元'.format(final_val - CAPITAL))

# 保存结果
pd.DataFrame([{
    '本金': '10万元',
    '市值区间': '0-30亿',
    '成交额阈值': '2000万',
    '成本': '0.15%',
    '单票上限': '2%',
    '年化收益': ann, '夏普比率': sharpe, '最大回撤': mdd,
    '总收益': total_ret, '胜率': met.get('胜率',''),
    '调仓次数': met.get('调仓次数',''), '止损次数': met.get('止损次数',''),
}]).to_csv('{}/backtest_10w_result.csv'.format(OUT), index=False, encoding='utf-8-sig')

print('\n完成! 结果已保存: {}/backtest_10w_result.csv'.format(OUT))
