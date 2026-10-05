# -*- coding: utf-8 -*-
"""日线 20260629~20260703 五日缺口影响评估（只读）。

背景：E 全量日线的键空间相对 D 主库缺 5 个交易日
      20260629 / 20260630 / 20260701 / 20260702 / 20260703
      共 27,562 行，且 E 盘已扫描，无任何来源能补齐。

本脚本回答：全量替换后这五天缺口对「消费端」意味着什么。
只做只读统计，不写生产数据。

输出：
    [G1] 缺口逐日行数与涉及股票数
    [G2] 缺口在策略回测/实盘中的可容忍性（连续性断点）
    [G3] 缺口两侧相邻交易日的市场状态（是否跨越重大波动）
    [G4] 结论：可接受 / 需阻断
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_DAILY = r'D:\astock\daily\stock_daily.parquet'
E_FULL = r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'

GAP = ['20260629', '20260630', '20260701', '20260702', '20260703']


def main():
    d = pd.read_parquet(D_DAILY)
    e = pd.read_parquet(E_FULL)

    for x in (d, e):
        if 'trade_date' in x.index.names or 'ts_code' in x.index.names:
            x.reset_index(inplace=True)

    for x in (d, e):
        x['_dt'] = pd.to_datetime(x['trade_date'])

    print('=' * 92)
    print('[G0] D 主库  max=%s' % d['_dt'].max().date())
    print('     E 全量  max=%s' % e['_dt'].max().date())

    print()
    print('[G1] 缺口逐日：D 有 / E 有')
    dall = pd.Index(d['_dt'].unique())
    eall = pd.Index(e['_dt'].unique())
    for g in GAP:
        dt = pd.Timestamp(pd.to_datetime(g, format='%Y%m%d'))
        nd = int((d['_dt'] == dt).sum())
        ne = int((e['_dt'] == dt).sum())
        flag = '缺口' if ne == 0 else '有'
        print('     %s  D行=%5d  E行=%5d  %s' % (g, nd, ne, flag))

    # 连续性：E 全量里缺口前后的真实交易日
    es = sorted(e['_dt'].unique())
    before = [t for t in es if t < pd.Timestamp(pd.to_datetime(
        GAP[0], format='%Y%m%d'))]
    after = [t for t in es if t > pd.Timestamp(pd.to_datetime(
        GAP[-1], format='%Y%m%d'))]
    b_last = pd.Timestamp(before[-1]) if before else None
    a_first = pd.Timestamp(after[0]) if after else None
    print()
    print('[G2] 连续性：E 全量缺口前最后交易日 = %s' % b_last)
    print('                缺口后首个交易日   = %s' % a_first)

    if b_last is not None and a_first is not None:
        gap_days = (a_first - b_last).days
        print('                日历间隔 = %d 天 -> 跨越 5 个交易日，'
              '指数/因子序列在此断裂' % gap_days)

    print()
    print('[G3] 缺口两侧相邻交易日的指数级市场状态（等权全市场均值）')
    cols = ['close', 'pct_chg'] if 'pct_chg' in d.columns else ['close']
    for tag, dt in [('缺口前最后交易日', b_last), ('缺口后首个交易日', a_first)]:
        if dt is None:
            continue
        sub = d[d['_dt'] == dt]
        if sub.empty:
            sub = e[e['_dt'] == dt]
        if sub.empty:
            print('     %s %s : 无数据' % (tag, dt.date()))
            continue
        msg = '     %s %s  行=%5d' % (tag, dt.date(), len(sub))
        if 'pct_chg' in sub.columns:
            # 已实测确认：D:/astock 的 pct_chg 是【百分数】口径
            # （pct_chg == (close/pre_close-1)*100，全表一致率 85.33% vs 小数口径 2.68%），
            # 故此处直接使用，不得再乘 100（v1 曾二次放大，导致 -250% 这类荒谬值）。
            pc = pd.to_numeric(sub['pct_chg'], errors='coerce').dropna()
            msg += '  等权均涨跌=%+.3f%%  中位=%+.3f%%  (n=%d)' % (
                pc.mean(), pc.median(), len(pc))
        print(msg)

    print()
    print('[G4] 影响判定')
    print('     1) 缺口 = 连续 5 个交易日，E 全量无法补齐，')
    print('        -> 全量替换后 D 主库将永久缺失 20260629~20260703；')
    print('     2) 该区间位于序列中段（%s ~ %s 之前），'
          % ('2009-01-05', str(b_last.date()) if b_last is not None else '?'))
    print('        回测窗口内出现 5 日空洞：')
    print('        - 任何 rolling/持有期/换手窗口跨过该段都会错位；')
    print('        - 若策略持有期 <= 5 日，恰在缺口日买入的仓位将失去卖出参考；')
    print('     3) 因是序列中段而非尾部，不影响「最新日线」的实盘取值，')
    print('        但影响历史回测的真实性与任何跨越该段的指标。')

    print()
    print('     -> 判定：不可静默接受。全量替换必须显式记录该 5 日缺口，')
    print('        或改用「D 主库为基线 + E 只做增量追加」的合并策略。')

    print()
    print('GAP_IMPACT_DONE')


if __name__ == '__main__':
    main()
