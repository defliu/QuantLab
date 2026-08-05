# coding=utf-8
"""Phase 1: 分行情区间小市值alpha验证（15组）。"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')
import time, pandas as pd, numpy as np

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest, backtest_stop_loss

OUT = 'D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize'
codes = load_universe()
panel, fin = build_panel(codes)

# 市值过滤
MV = {
    '0-30亿': lambda p, f, d: (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000),
    '全池': None,
    '80亿+': lambda p, f, d: (p.loc[d]['circ_mv'] >= 800000),
}

# 5个区间
PERIODS = [
    ('区间1_熊市后半', '2018-07-31', '2018-12-31'),
    ('区间2_牛市', '2019-01-01', '2021-12-31'),
    ('区间3_震荡市', '2022-01-01', '2023-12-31'),
    ('区间4_微盘崩盘', '2024-01-01', '2024-06-30'),
    ('区间5_结构性牛市', '2024-07-01', '2026-06-18'),
]

results = []
period_curves = {}  # 每个区间的累计收益曲线

for pname, sd, ed in PERIODS:
    for mv_name, mv_func in MV.items():
        t0 = time.time()
        try:
            eq, td, sl, met = backtest_stop_loss(
                panel, fin, top_n=80, freq='2M', tx_cost=0.002,
                dynamic_universe=True, stop_loss=-0.12,
                filter_func=mv_func, start_date=sd, end_date=ed)
            ann = met.get('年化收益', '')
            sharpe = met.get('夏普比率', '')
            mdd = met.get('最大回撤', '')
            n_rebal = met.get('调仓次数', '')
            sample_flag = '' if (isinstance(n_rebal, int) and n_rebal >= 3) else ' ⚠样本不足'
            results.append({
                '区间': pname, '组合': mv_name,
                '年化收益': str(ann) + sample_flag, '夏普比率': sharpe, '最大回撤': mdd,
                '胜率': met.get('胜率',''), '调仓次数': n_rebal,
                '止损次数': len(sl) if sl is not None else 0,
                '耗时(秒)': round(time.time()-t0, 1),
            })
            # 保存曲线
            if eq is not None and len(eq) > 0:
                curve_df = eq.copy() if hasattr(eq, 'to_frame') else pd.DataFrame(eq)
                curve_df.to_csv('{}/period_{}_{}.csv'.format(OUT, pname, mv_name), encoding='utf-8-sig')
            print('{} | {} | 年化={} 夏普={} 回撤={} ({:.0f}s)'.format(
                pname, mv_name, ann, sharpe, mdd, time.time()-t0))
        except Exception as e:
            print('{} | {} | ERROR: {}'.format(pname, mv_name, str(e)[:80]))
            results.append({
                '区间': pname, '组合': mv_name,
                '年化收益': 'ERROR', '夏普比率': 'ERROR', '最大回撤': 'ERROR',
                '胜率': 'ERROR', '调仓次数': 'ERROR', '止损次数': -1,
                '耗时(秒)': round(time.time()-t0, 1),
            })

# 保存汇总
df = pd.DataFrame(results)
df.to_csv('{}/validation_returns_by_period.csv'.format(OUT), index=False, encoding='utf-8-sig')
print('\n完成! Phase 1 结果已保存: {}/validation_returns_by_period.csv'.format(OUT))
print(df.to_string(index=False))
