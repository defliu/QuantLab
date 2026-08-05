# coding=utf-8
"""Phase 2: 流动性约束回测（6组）。"""
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

# amount 单位 = 千元，阈值换算：2000万=20000千元, 3000万=30000千元, 5000万=50000千元
def make_liq_filter(min_amount_k):
    """0-30亿 + 前20日日均成交额 > 阈值(千元)。"""
    def _f(p, f, d):
        mv_mask = (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)
        # 前20个交易日日均成交额
        trade_dates = sorted(p.index.get_level_values('trade_date').unique())
        di = trade_dates.index(d)
        if di < 20:
            return mv_mask  # warmup期不过滤
        start_d = trade_dates[di-20]
        # amount 是截面列，取前20日该截面均值
        window = p.loc[start_d:d]
        avg_amt = window.groupby('ts_code')['amount'].mean()
        liq_mask = avg_amt > min_amount_k
        # 对齐到 d 日索引
        liq_mask = liq_mask.reindex(p.loc[d].index, fill_value=False)
        return mv_mask & liq_mask
    return _f

# 方案A：成交额阈值
LIQ_THRESHOLDS = {
    '阈值2000万': 20000,   # 千元
    '阈值3000万': 30000,
    '阈值5000万': 50000,
}

# 方案B：分级成本
COST_MODELS = {
    '成本0.15%': 0.0015,
    '成本0.30%': 0.0030,
    '成本0.50%': 0.0050,
}

results = []

# 方案A：全周期 + 不同成交额阈值
print('=== 方案A：成交额阈值过滤（全周期）===')
for tname, thr in LIQ_THRESHOLDS.items():
    t0 = time.time()
    try:
        eq, td, sl, met = backtest_stop_loss(
            panel, fin, top_n=80, freq='2M', tx_cost=0.002,
            dynamic_universe=True, stop_loss=-0.12,
            filter_func=make_liq_filter(thr))
        # 统计过滤后平均剩余股票数
        results.append({
            '方案': 'A_' + tname, '年化收益': met.get('年化收益',''),
            '夏普比率': met.get('夏普比率',''), '最大回撤': met.get('最大回撤',''),
            '胜率': met.get('胜率',''), '过滤后剩余股数': 'N/A',
            '耗时(秒)': round(time.time()-t0, 1),
        })
        print('{} | 年化={} 夏普={} 回撤={} ({:.0f}s)'.format(
            tname, met.get('年化收益'), met.get('夏普比率'), met.get('最大回撤'), time.time()-t0))
    except Exception as e:
        print('{} | ERROR: {}'.format(tname, str(e)[:80]))
        results.append({'方案': 'A_' + tname, '年化收益': 'ERROR', '夏普比率': 'ERROR',
                        '最大回撤': 'ERROR', '胜率': 'ERROR', '过滤后剩余股数': 'N/A',
                        '耗时(秒)': round(time.time()-t0, 1)})

# 方案B：分级成本（无成交额过滤，仅0-30亿）
print('\n=== 方案B：分级交易成本（全周期，0-30亿）===')
MV = lambda p, f, d: (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000)
for cname, cost in COST_MODELS.items():
    t0 = time.time()
    try:
        eq, td, sl, met = backtest_stop_loss(
            panel, fin, top_n=80, freq='2M', tx_cost=cost,
            dynamic_universe=True, stop_loss=-0.12, filter_func=MV)
        results.append({
            '方案': 'B_' + cname, '年化收益': met.get('年化收益',''),
            '夏普比率': met.get('夏普比率',''), '最大回撤': met.get('最大回撤',''),
            '胜率': met.get('胜率',''), '过滤后剩余股数': 'N/A',
            '耗时(秒)': round(time.time()-t0, 1),
        })
        print('{} | 年化={} 夏普={} 回撤={} ({:.0f}s)'.format(
            cname, met.get('年化收益'), met.get('夏普比率'), met.get('最大回撤'), time.time()-t0))
    except Exception as e:
        print('{} | ERROR: {}'.format(cname, str(e)[:80]))
        results.append({'方案': 'B_' + cname, '年化收益': 'ERROR', '夏普比率': 'ERROR',
                        '最大回撤': 'ERROR', '胜率': 'ERROR', '过滤后剩余股数': 'N/A',
                        '耗时(秒)': round(time.time()-t0, 1)})

df = pd.DataFrame(results)
df.to_csv('{}/validation_liquidity.csv'.format(OUT), index=False, encoding='utf-8-sig')
df.to_csv('{}/validation_cost_model.csv'.format(OUT), index=False, encoding='utf-8-sig')
print('\n完成! Phase 2 结果已保存')
print(df.to_string(index=False))
