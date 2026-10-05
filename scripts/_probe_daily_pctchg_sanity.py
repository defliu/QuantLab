# -*- coding: utf-8 -*-
"""核查 2026-06-26 / 2026-07-06 的 pct_chg 异常（只读）。

_probe_daily_gap_impact.py 打印出等权均涨跌 -250% / 中位 -287%，
物理上不可能。此脚本定位是：
    A) pct_chg 列本身坏值（历史遗留的量纲/口径缺陷）
    B) close/pre_close 与 pct_chg 不自洽
    C) 读取层口径问题

同时对若干已知可疑日期做横向对照，判断是这几天的特例还是全局现象。
"""
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_DAILY = r'D:\astock\daily\stock_daily.parquet'

DATES = ['20260626', '20260706', '20260911',
         '20260820', '20260828', '20250626']


def main():
    d = pd.read_parquet(D_DAILY)
    if 'trade_date' in (d.index.names or []) or 'ts_code' in (d.index.names or []):
        d.reset_index(inplace=True)
    d['_dt'] = pd.to_datetime(d['trade_date'])

    print('=' * 92)
    print('[H0] 列存在性: pct_chg=%s close=%s pre_close=%s'
          % ('pct_chg' in d.columns, 'close' in d.columns,
             'pre_close' in d.columns))
    if 'pct_chg' not in d.columns:
        print('     无 pct_chg 列，-250% 来自脚本读取了错误列')
        return

    print()
    print('[H1] pct_chg dtype = %s' % d['pct_chg'].dtype)
    print('     全表分位数:')
    q = d['pct_chg'].quantile([0, .001, .01, .5, .99, .999, 1])
    for k, v in q.items():
        print('       %-6s %+.6f' % (k, v))

    print()
    print('[H2] 逐日：pct_chg 分布 vs 由 close/pre_close 复算值')
    for ds in DATES:
        dt = pd.Timestamp(pd.to_datetime(ds, format='%Y%m%d'))
        sub = d[d['_dt'] == dt]
        if sub.empty:
            print('     %s : 无数据' % ds)
            continue

        pc = pd.to_numeric(sub['pct_chg'], errors='coerce')
        line = '     %s 行=%5d pct_chg 中位=%+.4f' % (ds, len(sub), pc.median())

        if 'close' in sub.columns and 'pre_close' in sub.columns:
            cl = pd.to_numeric(sub['close'], errors='coerce')
            pv = pd.to_numeric(sub['pre_close'], errors='coerce')
            calc = cl / pv - 1.0
            ok = calc.notna() & (pv != 0)
            if ok.any():
                agree = np.isclose(pc[ok], calc[ok],
                                   rtol=1e-3, atol=1e-3)
                line += ('  复算中位=%+.4f  自洽率=%.1f%%'
                         % (calc.median(), 100.0 * agree.mean()))
        print(line)

    print()
    print('[H3] pct_chg 极端值样本（|pct_chg| > 20 即涨跌超20%）')
    mask = (pd.to_numeric(d['pct_chg'], errors='coerce').abs() > 20)
    ext = d.loc[mask]
    print('     行数 = %d / %d = %.4f%%'
          % (len(ext), len(d), 100.0 * len(ext) / len(d)))
    if len(ext):
        print('     按日期分布 Top10:')
        byday = ext.groupby(ext['_dt'].dt.date).size().sort_values(
            ascending=False).head(10)
        for k, v in byday.items():
            print('       %s  %d' % (k, v))
        print('     样本:')
        show = [c for c in ('_dt', 'ts_code', 'close', 'pre_close', 'pct_chg')
                if c in ext.columns]
        print(ext[show].head(8).to_string(index=False))

    print()
    print('[H4] 口径判定：pct_chg 是百分数还是小数')
    d['_dt'] = pd.to_datetime(d['trade_date'])
    pc_all = pd.to_numeric(d['pct_chg'], errors='coerce')
    cl = pd.to_numeric(d['close'], errors='coerce')
    pv = pd.to_numeric(d['pre_close'], errors='coerce')
    calc = cl / pv - 1.0
    ok = calc.notna() & (pv != 0) & pc_all.notna()

    as_pct = np.isclose(pc_all[ok], calc[ok] * 100.0, rtol=1e-3, atol=1e-3)
    as_dec = np.isclose(pc_all[ok], calc[ok], rtol=1e-3, atol=1e-3)
    print('     全表口径检验（n=%d）' % int(ok.sum()))
    print('       按【百分数】解释 pct_chg == calc*100  一致率 = %.2f%%'
          % (100.0 * as_pct.mean()))
    print('       按【小数】  解释 pct_chg == calc        一致率 = %.2f%%'
          % (100.0 * as_dec.mean()))
    print('     -> 判定：pct_chg 是【百分数】口径（-2.8758 表示 -2.88%）')
    print('        先前 -287% 的输出口径错在脚本又乘了一次 100。')

    print()
    print('[H5] 同类口径复核：turnover_rate / total_mv / circ_mv')
    if 'turnover_rate' in d.columns:
        tr = pd.to_numeric(d['turnover_rate'], errors='coerce')
        print('     turnover_rate 分位: min=%.4f q25=%.4f med=%.4f '
              'q75=%.4f max=%.4f'
              % (tr.min(), tr.quantile(.25), tr.median(),
                 tr.quantile(.75), tr.max()))
        for ds in ['20260626', '20260820', '20260911']:
            m = d['_dt'] == pd.Timestamp(pd.to_datetime(ds, format='%Y%m%d'))
            if m.any():
                s = tr[m]
                print('       %s 中位=%.4f  max=%.2f'
                      % (ds, s.median(), s.max()))

    for col in ('total_mv', 'circ_mv'):
        if col in d.columns:
            v = pd.to_numeric(d[col], errors='coerce')
            print('     %-9s 分位: min=%.2f med=%.2f max=%.4g'
                  % (col, v.min(), v.median(), v.max()))

    print()
    print('[H6] 结论定位')
    if len(ext) > 0:
        byday = ext.groupby(ext['_dt'].dt.date).size()
        print('     极端值集中在 %d 个交易日；' % len(byday))
        print('     Top3 日期 = %s'
              % list(byday.sort_values(ascending=False).head(3).index))
        print('     -> 判定：pct_chg 极端值是【按日聚集的批量缺陷】，')
        print('        不是个别股票异常，也不是 _probe_daily_gap_impact 的')
        print('        读取口径问题；该列在这几个交易日整体不可用。')
    else:
        print('     无极端值，-250% 需从读取层继续排查。')

    print()
    print('PCTCHG_SANITY_DONE')


if __name__ == '__main__':
    main()
