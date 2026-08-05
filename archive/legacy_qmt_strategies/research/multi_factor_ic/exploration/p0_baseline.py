# coding=utf-8
"""Phase 0: 基线复现硬门预检。"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest_stop_loss

codes = load_universe()
panel, fin = build_panel(codes)
MV = lambda p, f, d: (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)

eq, td, sl, met = backtest_stop_loss(
    panel, fin, top_n=80, freq='2M', tx_cost=0.002,
    dynamic_universe=True, stop_loss=-0.12, filter_func=MV)

ann = met.get('年化收益', '')
sharpe = met.get('夏普比率', '')
mdd = met.get('最大回撤', '')

print('PHASE0 基线复现:')
print('  年化={} 夏普={} 回撤={}'.format(ann, sharpe, mdd))
print('  预期: 年化15.0-16.0%, 回撤-19.5%~-21.5%')

# 硬门判定
ann_f = float(str(ann).rstrip('%'))
mdd_f = float(str(mdd).rstrip('%'))
passed = (15.0 <= ann_f <= 16.0) and (-21.5 <= mdd_f <= -19.5)
print('  硬门结果: {}'.format('✅ PASS' if passed else '❌ FAIL'))

# 保存
import pandas as pd
pd.DataFrame([{
    '组合': '0-30亿+双月+TOP80+止损-12%',
    '年化收益': ann, '夏普比率': sharpe, '最大回撤': mdd,
    '胜率': met.get('胜率',''), '调仓次数': met.get('调仓次数',''),
    '止损次数': len(sl) if sl is not None else 0,
    '硬门': 'PASS' if passed else 'FAIL'
}]).to_csv('D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize/phase0_baseline.csv',
           index=False, encoding='utf-8-sig')

if not passed:
    print('\n🔴 硬门不通过，停止后续任务！')
    sys.exit(1)
else:
    print('\n✅ 硬门通过，继续执行Phase 1/2')
