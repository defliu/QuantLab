# coding=utf-8
"""第二批财务：D 独有键明细（整体替换会丢失的记录）。只读。"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

TASKS = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date'],
     ['ts_code', 'end_date', 'ann_date', 'q_roe', 'q_gsprofit_margin',
      'dtprofit_to_profit', 'ocf_to_or']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type'],
     ['ts_code', 'end_date', 'ann_date', 'type', 'p_change_min',
      'p_change_max']),
]


def load(path, keys, valcols):
    f = pq.ParquetFile(path)
    have = set(f.schema_arrow.names)
    use = [c for c in keys + valcols if c in have]
    t = pq.read_table(path, columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    if t.columns.duplicated().any():
        t = t.loc[:, ~t.columns.duplicated(keep='last')]
    return t, [c for c in use]


def kset(t, keys):
    out = {}
    for k in keys:
        v = t[k]
        if k.endswith('_date'):
            v = pd.to_datetime(v, format='mixed', errors='coerce')
            v = v.dt.strftime('%Y%m%d')
        else:
            v = v.astype('string').fillna('<NA>')
        out[k] = v
    return set(pd.MultiIndex.from_arrays([out[k] for k in keys]))


def main():
    for name, keys, valcols in TASKS:
        ep = os.path.join(E_ROOT, name + '.parquet')
        dp = os.path.join(D_FIN, name + '.parquet')
        print('=' * 92)
        print(name)
        print('=' * 92)
        et, ecols = load(ep, keys, valcols)
        dt, dcols = load(dp, keys, valcols)
        print('E 行数=%d  D 行数=%d' % (len(et), len(dt)))
        print('E 重复键=%d  D 重复键=%d'
              % (int(et.duplicated(subset=keys).sum()),
                 int(dt.duplicated(subset=keys).sum())))
        et = et.drop_duplicates(subset=keys, keep='first')
        dt = dt.drop_duplicates(subset=keys, keep='first')
        print('E 独有列: %s' % sorted(set(ecols) - set(dcols)))
        print('D 独有列: %s' % sorted(set(dcols) - set(ecols)))
        es, ds = kset(et, keys), kset(dt, keys)
        only_d = ds - es
        only_e = es - ds
        print('D 独有键=%d   E 独有键=%d   共有键=%d'
              % (len(only_d), len(only_e), len(es & ds)))
        if only_d:
            idx = pd.MultiIndex.from_tuples(sorted(only_d),
                                            names=keys).to_frame(index=False)
            print('')
            print('--- D 独有记录（整体替换会丢失）---')
            m = dt.merge(idx, on=keys, how='inner')
            m = m.sort_values(keys)
            for _, r in m.iterrows():
                vals = []
                for c in dcols:
                    if c in keys:
                        continue
                    v = r[c]
                    if pd.notna(v):
                        vals.append('%s=%s' % (c, v))
                print('  %s' % ' | '.join(
                    ['%s=%s' % (k, r[k]) for k in keys] + vals[:8]))
            print('')
            print('  涉及股票: %d' % m[keys[0]].nunique())
            print('  涉及报告期: %s'
                  % sorted(m[keys[1]].astype(str).unique())[:20])
        print('')
    return 0


if __name__ == '__main__':
    sys.exit(main())