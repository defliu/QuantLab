# coding=utf-8
"""Phase 1: 120组参数矩阵回测。"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import warnings; warnings.filterwarnings('ignore')
import time, json
from pathlib import Path
import pandas as pd
import numpy as np

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import backtest, backtest_stop_loss
from research.multi_factor_ic.config import OUTPUT_DIR

# ============================================================
# 参数矩阵定义（任务书 Phase 1）
# 频率: 月(M) / 双月(2M) / 季度(Q)
# 持仓: 20 / 30 / 50 / 80
# 止损: 无 / -10% / -12% / -15%
# 市值区间: 全池 / 0-30亿 / 30-80亿 / 80亿+
# ============================================================

FREQS = ['M', '2M', 'Q']
TOP_NS = [20, 30, 50, 80]
STOP_LOSSES = [None, -0.10, -0.12, -0.15]

# 市值过滤函数（circ_mv单位=万元）
MV_FILTERS = {
    '全池': None,
    '0-30亿': lambda p, f, d: (p.loc[d]['circ_mv'] > 0) & (p.loc[d]['circ_mv'] < 300000),
    '30-80亿': lambda p, f, d: (p.loc[d]['circ_mv'] >= 300000) & (p.loc[d]['circ_mv'] < 800000),
    '80亿+': lambda p, f, d: (p.loc[d]['circ_mv'] >= 800000),
}

# 裁剪规则（任务书 L133-138）
def is_excluded(freq, top_n, sl, mv):
    """返回True表示被裁剪。"""
    # 移除：月频 + 80持仓 + 无止损
    if freq == 'M' and top_n == 80 and sl is None:
        return True
    # 移除：季度 + 20持仓 + 任何止损
    if freq == 'Q' and top_n == 20 and sl is not None:
        return True
    # 移除：80亿+ 小市值 + 任何止损  (注：80亿+不是小市值，这里理解为"80亿+ + 任何止损")
    if mv == '80亿+' and sl is not None:
        return True
    # 移除：0-30亿 + 月频 + TOP80
    if mv == '0-30亿' and freq == 'M' and top_n == 80:
        return True
    return False

# 生成所有组合
params = []
for freq in FREQS:
    for top_n in TOP_NS:
        for sl in STOP_LOSSES:
            for mv_name, mv_func in MV_FILTERS.items():
                if is_excluded(freq, top_n, sl, mv_name):
                    continue
                params.append({
                    'freq': freq, 'top_n': top_n, 'stop_loss': sl, 'mv': mv_name,
                    'mv_func': mv_func,
                })

print('参数矩阵: {}组'.format(len(params)))

# 加载数据
codes = load_universe()
panel, fin_ffill = build_panel(codes)
print('面板加载完成: {}'.format(panel.shape))

# 输出路径
out_dir = '{}/v3_optimize'.format(OUTPUT_DIR)
Path(out_dir).mkdir(parents=True, exist_ok=True)
csv_path = '{}/param_matrix_20260719.csv'.format(out_dir)

# 如果已有部分结果，跳过已完成的
completed = set()
if os.path.exists(csv_path):
    try:
        done = pd.read_csv(csv_path)
        for _, r in done.iterrows():
            completed.add((r['调仓频率'], r['持仓数'], r['止损线'], r['市值区间']))
        print('已有 {} 组结果，继续未完成部分'.format(len(completed)))
    except Exception:
        pass

results = []
total = len(params)
start = time.time()

for idx, p in enumerate(params):
    key = (p['freq'], p['top_n'], str(p['stop_loss']), p['mv'])
    if key in completed:
        continue

    t0 = time.time()
    try:
        if p['stop_loss'] is None:
            eq, td, met = backtest(panel, fin_ffill, top_n=p['top_n'], freq=p['freq'],
                                    tx_cost=0.002, dynamic_universe=True, filter_func=p['mv_func'])
            sl_count = -1
        else:
            eq, td, sldf, met = backtest_stop_loss(panel, fin_ffill, top_n=p['top_n'], freq=p['freq'],
                                                  tx_cost=0.002, dynamic_universe=True,
                                                  stop_loss=p['stop_loss'], filter_func=p['mv_func'])
            sl_count = len(sldf) if sldf is not None else 0

        row = {
            '调仓频率': p['freq'], '持仓数': p['top_n'],
            '止损线': str(p['stop_loss']) if p['stop_loss'] else '无',
            '市值区间': p['mv'],
            '年化收益': met.get('年化收益', ''),
            '夏普比率': met.get('夏普比率', ''),
            '最大回撤': met.get('最大回撤', ''),
            '胜率': met.get('胜率', ''),
            '调仓次数': met.get('调仓次数', ''),
            '止损次数': sl_count,
            '耗时(秒)': round(time.time() - t0, 1),
        }
        results.append(row)
        print('[{:3d}/{:3d}] {:.0f}s | {} TOP{} 止损={} {} 年化={} 夏普={} 回撤={}'.format(
            idx+1, total, time.time()-t0, p['freq'], p['top_n'],
            str(p['stop_loss']) if p['stop_loss'] else '无', p['mv'],
            met.get('年化收益', '?'), met.get('夏普比率', '?'), met.get('最大回撤', '?')))

    except Exception as e:
        print('[{:3d}/{:3d}] ERROR {} TOP{} {}: {}'.format(
            idx+1, total, p['freq'], p['top_n'], p['mv'], str(e)[:80]))
        row = {
            '调仓频率': p['freq'], '持仓数': p['top_n'],
            '止损线': str(p['stop_loss']) if p['stop_loss'] else '无',
            '市值区间': p['mv'],
            '年化收益': 'ERROR', '夏普比率': 'ERROR', '最大回撤': 'ERROR',
            '胜率': 'ERROR', '调仓次数': 'ERROR', '止损次数': -1,
            '耗时(秒)': round(time.time() - t0, 1),
        }
        results.append(row)

    # 每完成 10 组立即追加写入 CSV（防中断）
    if len(results) % 10 == 0:
        df = pd.DataFrame(results)
        # 合并已有completed的结果（如果有）
        if completed:
            old = pd.read_csv(csv_path) if os.path.exists(csv_path) else pd.DataFrame()
            df = pd.concat([old, df], ignore_index=True)
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print('  >> 已保存 {} 组到 {}'.format(len(df), csv_path))

# 最终保存
if results:
    df = pd.DataFrame(results)
    if os.path.exists(csv_path) and completed:
        old = pd.read_csv(csv_path)
        df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=['调仓频率','持仓数','止损线','市值区间'], keep='last')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')

print('\n完成! 总耗时 {:.0f}秒, {} 组回测'.format(time.time()-start, len(params)))
print('结果已保存: {}'.format(csv_path))
