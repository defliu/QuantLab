# -*- coding: utf-8 -*-
"""抽样打印 forecast E 侧 flag 版本对的原文对照，人工确认 flag=1 是新版。

只读，不写盘。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _audit_dedup_safety import E_ROOT, _read  # noqa: E402
from _keynorm import isna_scalar  # noqa: E402


def _eq(a, b):
    if isna_scalar(a) and isna_scalar(b):
        return True
    if isna_scalar(a) or isna_scalar(b):
        return False
    return str(a).strip() == str(b).strip()

TBL = sys.argv[1] if len(sys.argv) > 1 else 'forecast'
KEYS = {
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
}[TBL]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6

df = _read(os.path.join(E_ROOT, TBL + '.parquet'), KEYS)
k = df[KEYS].astype(str).agg('|'.join, axis=1)
dup = k.duplicated(keep=False)
sub = df.loc[dup].copy()
sub['__k'] = k[dup]
g = pd.factorize(sub['__k'], sort=False)[0]
flag = sub['update_flag'].to_numpy()
cols = [c for c in sub.columns if c not in KEYS and c != '__k']
seen = set()
shown = 0
for gi in np.unique(g):
    pos = np.flatnonzero(g == gi)
    if len(pos) != 2:
        continue
    diff = [c for c in cols
            if not _eq(sub[c].iloc[pos[0]], sub[c].iloc[pos[1]])]
    if not diff:
        continue
    key = str(sub['__k'].iloc[pos[0]])
    top = diff[0]
    sig = (top, )
    if sig in seen:
        continue
    seen.add(sig)
    lo = pos[np.argmin(flag[pos])]
    hi = pos[np.argmax(flag[pos])]
    print('=' * 100)
    print('key=%s  flag: row%d=%s  row%d=%s' % (key, lo, flag[lo], hi, flag[hi]))
    for c in diff[:4]:
        print('  %-18s flag=%s -> %r' % (c, flag[lo], str(sub[c].iloc[lo])[:110]))
        print('  %-18s flag=%s -> %r' % ('', flag[hi], str(sub[c].iloc[hi])[:110]))
    shown += 1
    if shown >= N:
        break
