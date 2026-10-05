# -*- coding: utf-8 -*-
"""定位归一化后撞键的具体行（share_change_event / dividend / forecast）。"""
import os
import re
import sys

import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'
SEP = '\u0001'
NA = '<NA>'
RE_DIGITS = re.compile(r'\d')

JOBS = [
    ('share_change_event', ['ts_code', 'change_date', 'change_reason'],
     'ts_code + change_date + change_reason'),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc'],
     'ts_code + end_date + ann_date + div_proc'),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type'],
     'ts_code + end_date + ann_date + type'),
    ('express', ['ts_code', 'end_date', 'ann_date'],
     'ts_code + end_date + ann_date'),
]

DATE_LIKE = ('date', 'month')


def isna_scalar(v):
    try:
        return bool(pd.isna(v))
    except Exception:
        return False


def norm_date(v):
    if isna_scalar(v):
        return NA
    if isinstance(v, pd.Timestamp):
        return v.strftime('%Y%m%d')
    s = str(v).strip()
    if not s or s.lower() in ('nan', 'none', 'nat', 'null'):
        return NA
    if s.isdigit() and len(s) == 8:
        return s
    m = RE_DIGITS.findall(s)
    return ''.join(m[:8]) if len(m) >= 8 else s


def key_strings(df, keys, date_cols):
    out = None
    for k in keys:
        col = df[k].map(norm_date) if k in date_cols else \
            df[k].map(lambda v: NA if isna_scalar(v) else str(v).strip())
        out = col if out is None else out + SEP + col
    return out.to_numpy()


def run(name, keys, label):
    d = pd.read_parquet(os.path.join(D_BASE, 'finance', name + '.parquet'))
    e = pd.read_parquet(os.path.join(E_ROOT, name + '.parquet'))
    date_cols = set(k for k in keys if any(t in k.lower() for t in DATE_LIKE))
    dk = key_strings(d, keys, date_cols)
    ek = key_strings(e, keys, date_cols)

    print('=' * 108)
    print('表：%s   主键：%s' % (name, label))

    dmask = pd.Index(dk).duplicated(keep=False)
    emask = pd.Index(ek).duplicated(keep=False)
    print('  D 撞键组=%d 冗余行=%d  |  E 撞键组=%d 冗余行=%d'
          % (len(pd.unique(dk[dmask])) if dmask.any() else 0,
             int(dmask.sum()) - (len(pd.unique(dk[dmask])) if dmask.any() else 0),
             len(pd.unique(ek[emask])) if emask.any() else 0,
             int(emask.sum()) - (len(pd.unique(ek[emask])) if emask.any() else 0)))

    for tag, df, karr, mask in (('D', d, dk, dmask), ('E', e, ek, emask)):
        if not mask.any():
            continue
        groups = pd.unique(karr[mask])
        print('  --- %s 撞键明细（前 3 组）---' % tag)
        for g in groups[:3]:
            pos = [i for i in range(len(karr)) if karr[i] == g]
            sub = df.iloc[pos]
            show = [c for c in keys if c in sub.columns]
            for c in show:
                print('      %-14s %s' % (c, list(sub[c].tolist())))
            others = [c for c in sub.columns if c not in keys]
            for c in others[:4]:
                vals = [str(v)[:26] for v in sub[c].tolist()]
                mark = '  <<< 不同' if len(set(vals)) > 1 else ''
                print('      %-14s %s%s' % (c, vals, mark))
            print('')
    del d, e


def main():
    only = sys.argv[1:] or None
    for name, keys, label in JOBS:
        if only and name not in only:
            continue
        try:
            run(name, keys, label)
        except Exception as exc:
            print('  !! %s 异常 %r' % (name, exc))
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
