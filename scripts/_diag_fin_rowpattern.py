# -*- coding: utf-8 -*-
"""_diag_fin_rowpattern.py

一击定案：E 的 fina_indicator「坏列大面积为空」到底是
  (a) 数据源/接口差异（业务事实），
  (b) 文件写入损坏/部分块未填充，
  (c) E 独有的多版本行把好行挤掉（结构问题）。

已排除：列错位（列序集合与顺序完全一致）、分段写入（仅 1 个 row group）。

本脚本做五件事：
  R1. 坏列非空行的**物理行号分布**：连续/聚集 -> 写入损坏；分散 -> 非损坏
  R2. 按 end_date 季度（0331/0630/0930/1231）分组的坏列非空率：D vs E 对照
  R3. E 中完全重复行占比（675628 行里有多少是逐列相同的副本）
  R4. 每个逻辑键的重复行数分布 + 重复行内坏列是否一致（多版本是否"空壳"）
  R5. E 独有行的 ann_date 分布（判断独有行是新披露还是旧版本副本）

输出：_diag_fin_rowpattern.out.txt
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_FIN = r'D:\astock\finance'
E_ROOT = r'E:\temp\行情数据'
RESIDUAL = '__index_level_0__'
TAB = 'fina_indicator'
BAD = ['roe_avg', 'q_profit_yoy', 'ocf_to_or', 'dtprofit_to_profit']
GOOD = ['eps', 'roe', 'gross_margin', 'netprofit_margin', 'bps']


def hr(t):
    print('')
    print('=' * 92)
    print(t)
    print('=' * 92)


def prep(path):
    df = pd.read_parquet(path)
    if RESIDUAL in df.columns:
        df = df.drop(columns=[RESIDUAL])
    for c in ('end_date', 'ann_date', 'f_ann_date'):
        if c in df.columns:
            df[c] = (df[c].astype(str).str.replace('-', '', regex=False)
                       .str.replace(r'\.0$', '', regex=True))
    return df


def main():
    d = prep(os.path.join(D_FIN, TAB + '.parquet'))
    e = prep(os.path.join(E_ROOT, TAB + '.parquet'))
    print('D 行数=%d   E 行数=%d' % (len(d), len(e)))

    hr('R1. 坏列非空行的物理行号分布（连续聚集=损坏 / 分散=非损坏）')
    n = len(e)
    print('E 总行数=%d' % n)
    for c in BAD:
        pos = np.flatnonzero(e[c].notna().values)
        if not len(pos):
            print('  %-22s 全空' % c)
            continue
        seg = np.split(pos, np.flatnonzero(np.diff(pos) > 1) + 1)
        longest = max(len(s) for s in seg)
        print('  %-22s 非空=%6d  连续段数=%5d  最长连续段=%5d  行号跨度=[%d..%d]'
              % (c, len(pos), len(seg), longest, pos[0], pos[-1]))
        decile = np.histogram(pos, bins=10, range=(0, n))[0]
        print('     十分位分布: %s' % list(decile))
    print('  对照（好列）:')
    for c in GOOD:
        pos = np.flatnonzero(e[c].notna().values)
        seg = np.split(pos, np.flatnonzero(np.diff(pos) > 1) + 1)
        print('  %-22s 非空=%6d  连续段数=%5d  最长连续段=%5d'
              % (c, len(pos), len(seg), max(len(s) for s in seg)))

    hr('R2. 按报告期季度分组的坏列非空率（D vs E 对照，年报应最全）')
    for lbl, df in (('D', d), ('E', e)):
        q = df['end_date'].astype(str).str[-4:]
        print('')
        print('  [%s] 各季度行数: %s' % (lbl, dict(q.value_counts().sort_index())))
        print('  %-22s %s' % ('字段', '  '.join('%-9s' % x for x in
                                              ['0331', '0630', '0930', '1231'])))
        for c in BAD + GOOD:
            cells = []
            for qq in ('0331', '0630', '0930', '1231'):
                sub = df[q == qq]
                cells.append('%-9.4f' % (sub[c].notna().mean() if len(sub) else np.nan))
            print('  %-22s %s' % (c, '  '.join(cells)))

    hr('R3. E 中完全重复行（逐列相同）占比')
    val_cols = [c for c in e.columns if c not in ('ts_code', 'end_date', 'ann_date')]
    sub = e.sample(n=min(200000, len(e)), random_state=42)
    h = pd.util.hash_pandas_object(sub[val_cols], index=False)
    dup = int(h.duplicated().sum())
    print('抽样 %d 行，价值列 hash 重复=%d (%.4f%%)'
          % (len(sub), dup, 100.0 * dup / len(sub)))
    print('  推算全表近似重复行数 ≈ %d' % int(dup * len(e) / float(len(sub))))

    hr('R4. 逻辑键重复结构：重复行内坏列是否一致（多版本是否为"空壳"）')
    for lbl, df in (('D', d), ('E', e)):
        g = df.groupby(['ts_code', 'end_date'], sort=False)
        sz = g.size()
        print('')
        print('  [%s] 逻辑键数=%d  重复行数分布: %s'
              % (lbl, len(sz), dict(sz.value_counts().sort_index().head(8))))
        c = 'roe_avg'
        # 对重复>1 的键，看组内 roe_avg 非空个数分布
        multi = sz[sz > 1].index
        if len(multi):
            sample_keys = list(multi[:200000])
            gg = df.set_index(['ts_code', 'end_date']).loc[sample_keys]
            nn = gg.groupby(level=[0, 1])[c].apply(lambda s: int(s.notna().sum()))
            szs = gg.groupby(level=[0, 1]).size()
            print('      重复键(%d)内 roe_avg 非空个数分布: %s'
                  % (len(nn), dict(nn.value_counts().sort_index().head(8))))
            both = nn[nn > 0]
            print('      重复键中「至少1行有 roe_avg」= %d (%.2f%%)，'
                  '这些键里平均非空行数=%.2f / 平均总行数=%.2f'
                  % (len(both), 100.0 * len(both) / len(nn),
                     float(both.mean()) if len(both) else np.nan,
                     float(szs.loc[both.index].mean()) if len(both) else np.nan))

    hr('R5. E 独有行 vs 共同键行 的构成（独有行是新披露还是旧版本空壳）')
    dk = set(map(tuple, d[['ts_code', 'end_date']].drop_duplicates().values))
    ek = set(map(tuple, e[['ts_code', 'end_date']].drop_duplicates().values))
    only_e = ek - dk
    print('D 逻辑键=%d  E 逻辑键=%d  共同=%d  E独有=%d  D独有=%d'
          % (len(dk), len(ek), len(dk & ek), len(only_e), len(dk - ek)))
    oe = e[e.set_index(['ts_code', 'end_date']).index.isin(only_e)]
    ck = e[~e.set_index(['ts_code', 'end_date']).index.isin(only_e)]
    for lbl, df in (('E独有键行', oe), ('E共同键行', ck)):
        print('')
        print('  [%s] 行数=%d' % (lbl, len(df)))
        if not len(df):
            continue
        print('     update_flag 分布: %s'
              % dict(df['update_flag'].value_counts(dropna=False).head(6)))
        print('     ann_date 年份: %s'
              % dict(df['ann_date'].astype(str).str[:4].value_counts().sort_index().head(10)))
        print('     end_date 年份(top): %s'
              % dict(df['end_date'].astype(str).str[:4].value_counts().head(8)))
        print('     坏列非空率: %s'
              % {c: round(float(df[c].notna().mean()), 5) for c in BAD})
        print('     好列非空率: %s'
              % {c: round(float(df[c].notna().mean()), 5) for c in GOOD})

    print('')
    print('ROWPATTERN_DIAG_DONE')


if __name__ == '__main__':
    main()
