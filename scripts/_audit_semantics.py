# -*- coding: utf-8 -*-
"""定位财务表 D vs E 载荷差异的精确构成（只读）。

回答：99.3%「都不等」到底差在哪？
- 每个键平均差几列？
- 哪些列贡献了绝大多数不等？
- 是浮点精度、字段口径变更，还是真实数据修订？
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

CASES = (
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
)


def norm_flag(s):
    return pd.to_numeric(s, errors='coerce').fillna(-1).astype('int8')


def audit(tbl, keys):
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_BASE, 'finance', tbl + '.parquet'))
    e = e.drop(columns=[c for c in ('index',) if c in e.columns])
    if 'update_flag' in e.columns:
        e['_f'] = norm_flag(e['update_flag'])

    print('=' * 92)
    print('### %s' % tbl)

    # 逐键：把 D 行与 E 中「该键最可能对应的一版」比（先比 flag=0，再 flag=1）
    d_idx = d.set_index(keys)
    payload = [c for c in d.columns if c not in keys and c != 'update_flag']

    # 只抽 D 的一个样本，控制耗时
    sample_keys = d_idx.index[:4000]
    dsub = d_idx.loc[sample_keys].reset_index()

    esub = e.merge(dsub[keys].drop_duplicates(), on=keys, how='inner')
    if 'update_flag' in esub.columns:
        esub['_f'] = norm_flag(esub['update_flag'])
        # 每键取 flag 最小的一版做对照（对齐 D 历史口径的可能性更高）
        esub = esub.sort_values('_f').drop_duplicates(subset=keys, keep='first')

    m = dsub.merge(esub, on=keys, how='inner', suffixes=('_d', '_e'))
    if len(m) == 0:
        print('  无重叠样本')
        return

    diff_counter = {}
    ndiff = []
    for _, row in m.iterrows():
        c = 0
        for col in payload:
            a, b = row.get(col + '_d'), row.get(col + '_e')
            if pd.isna(a) and pd.isna(b):
                continue
            try:
                if np.isclose(float(a), float(b), rtol=0, atol=1e-6):
                    continue
            except Exception:
                if str(a) == str(b):
                    continue
            c += 1
            diff_counter[col] = diff_counter.get(col, 0) + 1
        ndiff.append(c)

    ndiff = np.array(ndiff)
    print('  样本键=%d  成功配对=%d  载荷列数=%d'
          % (len(sample_keys), len(m), len(payload)))
    print('  每键差异列数分布: 均值=%.2f 中位=%d  为0(完全一致)=%d (%.1f%%)  >=10列=%d'
          % (ndiff.mean(), int(np.median(ndiff)),
             int((ndiff == 0).sum()), 100.0 * (ndiff == 0).mean(),
             int((ndiff >= 10).sum())))
    top = sorted(diff_counter.items(), key=lambda x: -x[1])[:10]
    print('  差异贡献 TOP10 列:')
    for col, n in top:
        print('      %-24s %6d  (%.1f%% 样本)' % (col, n, 100.0 * n / len(m)))

    # 关键列看具体样例
    if top:
        col = top[0][0]
        sub = m[[k + '_d' for k in keys][:1] + [col + '_d', col + '_e']]
        print('  列 %s 样例(D vs E):' % col)
        print(sub.head(6).to_string(index=False))


if __name__ == '__main__':
    for tbl, keys in CASES:
        audit(tbl, keys)
