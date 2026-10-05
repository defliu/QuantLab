# -*- coding: utf-8 -*-
"""裁决审计（只读）：确定财务表多版本的正确取舍规则。

产出三件事：
1. update_flag 归一化后的真实分布（数字/字符串/.0/NaN 统一）；
2. 对 D 已覆盖的多版本主键，D 行精确等于 E 的 flag=0 版还是 flag=1 版；
3. 全量日线 (trade_date, ts_code) 唯一性与 index 结构体检。
输出严格限量，避免打印超长键元组。
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
E_FULL_DIR = os.path.join(E_ROOT, '个股行情全量')
D_BASE = r'D:\astock'

FIN_KEYS = {
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
}


def norm_flag(series):
    def one(v):
        if v is None:
            return -1
        if isinstance(v, float) and np.isnan(v):
            return -1
        t = str(v).strip()
        if t == '' or t.lower() in ('nan', 'none', 'null'):
            return -1
        try:
            return int(round(float(t)))
        except Exception:
            return -1
    return series.map(one).astype('int8')


def eq_mask(a, b):
    both_na = a.isna() & b.isna()
    try:
        close = np.isclose(
            pd.to_numeric(a, errors='coerce'),
            pd.to_numeric(b, errors='coerce'),
            rtol=0, atol=1e-9,
        )
    except Exception:
        close = None
    if close is None:
        return both_na | (a.astype(str) == b.astype(str))
    both_num = a.notna() & b.notna()
    both_na = a.isna() & b.isna()
    return close | both_na


def audit_fin(tbl, keys):
    e_path = os.path.join(E_ROOT, tbl + '.parquet')
    d_path = os.path.join(D_BASE, 'finance', tbl + '.parquet')
    if not (os.path.isfile(e_path) and os.path.isfile(d_path)):
        print('[SKIP] %s 路径缺失' % tbl)
        return

    e = pd.read_parquet(e_path).reset_index(drop=True)
    d = pd.read_parquet(d_path).reset_index(drop=True)
    if 'index' in e.columns:
        e = e.drop(columns=['index'])

    print('=' * 92)
    print('### %s   keys=%s' % (tbl, keys))
    print('  D 行数=%d  E 行数=%d' % (len(d), len(e)))

    if 'update_flag' in e.columns:
        e['_flag'] = norm_flag(e['update_flag'])
        vc = e['_flag'].value_counts().sort_index().to_dict()
        print('  E flag 归一化分布(0/1/-1未知): %s' % vc)
        print('  D flag 分布: %s'
              % (norm_flag(d['update_flag']).value_counts().sort_index().to_dict()
                 if 'update_flag' in d.columns else 'D 无该列'))

    sizes = e.groupby(keys, dropna=False).size()
    multi_keys = sizes[sizes > 1].index
    n_multi = len(multi_keys)
    print('  E 基础主键唯一数=%d  多版本组=%d  冗余行=%d'
          % (len(sizes), n_multi, len(e) - len(sizes)))

    if n_multi == 0:
        return

    # 只在 D 已覆盖的多版本组上做裁决（避免为纯新增键花代价）
    d_keys = pd.MultiIndex.from_frame(d[keys])
    emulti = e.set_index(keys).loc[multi_keys].reset_index()
    emulti = emulti[pd.MultiIndex.from_frame(emulti[keys]).isin(d_keys)]

    print('  D 覆盖到的多版本组=%d' % len(emulti))
    if len(emulti) == 0:
        return

    payload = [c for c in emulti.columns
               if c in d.columns and c not in keys
               and c not in ('update_flag', '_flag')]

    m = emulti.merge(
        d[keys + payload],
        on=keys, how='inner', suffixes=('_e', '_d'),
    )
    if len(m) == 0:
        print('  [WARN] merge 后无匹配，检查主键 dtype/NaN')
        return

    same = pd.Series(True, index=m.index)
    for c in payload:
        same &= eq_mask(m[c + '_e'], m[c + '_d'])

    m['_hit'] = np.where(same, m['_flag'], -9)
    # 每个键取「首个匹配版本」→ 对应 D 实际存的那一版
    first_hit = m.groupby(keys, dropna=False)['_hit'].first()
    vc = first_hit.value_counts().to_dict()
    total = len(first_hit)
    label = {0: '等于 flag=0 版', 1: '等于 flag=1 版(修正版)',
             -1: '等于 flag 未知版', -9: '与 E 各版本都不等'}
    print('  >>> D 行 vs E 版本裁决（比对 %d 个载荷列, n=%d 组）:'
          % (len(payload), total))
    for k in (0, 1, -1, -9):
        if k in vc:
            print('      %-22s %7d  (%5.1f%%)'
                  % (label[k], vc[k], 100.0 * vc[k] / total))

    # 组内哪些列在变化 → 真正的版本标识
    g = emulti.groupby(keys, dropna=False)
    varying = sorted(
        [(c, int((g[c].nunique(dropna=False) > 1).sum())) for c in payload],
        key=lambda x: -x[1],
    )
    print('  组内差异字段 TOP6: %s' % varying[:6])


def audit_daily_full():
    e_path = os.path.join(E_FULL_DIR, 'stock_daily_full.parquet')
    d_path = os.path.join(D_BASE, 'daily', 'stock_daily.parquet')
    print('=' * 92)
    print('### 全量日线唯一性体检')
    keys = ['trade_date', 'ts_code']

    for tag, path in (('E全量', e_path), ('D现有', d_path)):
        df = pd.read_parquet(path)
        idx_names = list(df.index.names) if df.index.name or df.index.names[0] else []
        body = df.reset_index()
        keys_in_body = all(k in body.columns for k in keys)
        if keys_in_body:
            dup = int(body.duplicated(subset=keys).sum())
            dr = body['trade_date'].astype(str)
            print('  %-6s 行数=%d 主键重复=%d 交易日范围=%s~%s'
                  % (tag, len(df), dup, dr.min(), dr.max()))
        else:
            print('  %-6s 行数=%d 主键在列中不完整（键=%s，index=%s）'
                  % (tag, len(df), [k for k in keys if k not in body.columns],
                     list(df.index.names)))
        print('         index.names=%s 列数=%d' % (list(df.index.names), len(df.columns)))


def main():
    audit_daily_full()
    for tbl, keys in FIN_KEYS.items():
        audit_fin(tbl, keys)


if __name__ == '__main__':
    main()
