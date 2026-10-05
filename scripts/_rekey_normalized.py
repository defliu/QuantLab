# -*- coding: utf-8 -*-
"""日期归一化的键匹配复核 —— 修正 ISO 混入导致的假「独有键」。

发现（_audit_date_exact.py）：D 盘部分财务表的日期列混入 ISO 字符串
（如 '2026-06-30 00:00:00' / '2026-06-30'），而 E 盘全为 YYYYMMDD。
用 str(v) 归一化会让同一行匹配不上，表现为虚增的「D 独有键」：
  dividend  D独有=1054 == D侧ISO行数 1054   （完全吻合 -> 全是假的）
  express   D独有=5    == D侧ISO行数 5      （完全吻合 -> 全是假的）
  forecast  D独有=448   > D侧ISO行数 34     （部分真实）

本脚本把日期列统一归一化为 YYYYMMDD 后重算键集合，并给出：
  - 归一化后的共有 / D独有 / E独有
  - 归一化前后「E 新增键数」的差额（= 被格式问题虚增的数量）
  - D 侧 ISO 行的精确条数与样例
只读，不写盘。
"""
import os
import re
import sys

import numpy as np
import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'
SEP = '\u0001'
NA = '<NA>'

RE_DIGITS = re.compile(r'\d')

FIN_TABLES = [
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]

DATE_LIKE = ('date', 'month')


def isna_scalar(v):
    try:
        return bool(pd.isna(v))
    except Exception:
        return False


def norm_date(v):
    """把日期值压成 YYYYMMDD；无法解析则原样返回字符串。"""
    if isna_scalar(v):
        return NA
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime('%Y%m%d')
    s = str(v).strip()
    if not s or s.lower() in ('nan', 'none', 'nat', 'null'):
        return NA
    if s.isdigit() and len(s) == 8:
        return s
    m = RE_DIGITS.findall(s)
    if len(m) >= 8:
        cand = ''.join(m[:8])
        if len(cand) == 8:
            return cand
    return s


def key_strings(df, keys, date_cols):
    out = None
    for k in keys:
        if k in date_cols:
            col = df[k].map(norm_date)
        else:
            col = df[k].map(lambda v: NA if isna_scalar(v) else str(v).strip())
        out = col if out is None else out + SEP + col
    return out.to_numpy()


def analyse(name, keys):
    d_rel = os.path.join('finance', name + '.parquet')
    d_path = os.path.join(D_BASE, d_rel)
    e_path = os.path.join(E_ROOT, name + '.parquet')
    print('=' * 108)
    print('表：finance/%s   主键：%s' % (name, ' + '.join(keys)))
    if not (os.path.isfile(d_path) and os.path.isfile(e_path)):
        print('  !! 文件缺失 D=%s E=%s' % (os.path.isfile(d_path), os.path.isfile(e_path)))
        raise RuntimeError('缺文件 ' + name)

    d = pd.read_parquet(d_path)
    e = pd.read_parquet(e_path)
    for df in (d, e):
        if any(k not in df.columns for k in keys):
            df.reset_index(inplace=True)
    date_cols = set(k for k in keys if any(t in k.lower() for t in DATE_LIKE))

    # D 侧 ISO 混入精确计数
    iso_rows = 0
    iso_samples = []
    for k in keys:
        if k not in date_cols:
            continue
        s = d[k].dropna().astype(str).str.strip()
        bad = s[~s.str.match(r'^\d{8}$')]
        if len(bad):
            iso_rows = max(iso_rows, len(bad))
            iso_samples = list(dict.fromkeys(bad.tolist()))[:4]
    if iso_rows:
        print('  D 侧日期列非 YYYYMMDD 行数(取最大列)=%d  样例=%s' % (iso_rows, iso_samples))

    dk = key_strings(d, keys, date_cols)
    ek = key_strings(e, keys, date_cols)

    def uniq_info(arr):
        u = pd.unique(arr)
        return u, len(arr) - len(u)

    d_u, d_red = uniq_info(dk)
    e_u, e_red = uniq_info(ek)

    d_set = set(d_u.tolist())
    e_set = set(e_u.tolist())
    d_only = len(d_set - e_set)
    e_only = len(e_set - d_set)
    common = len(d_set & e_set)

    # 归一化前（str 直连）的对照：d_u_naive / e_u_naive 各自去重后求集合差，
    # d_only_naive = D 独有（不是 E 独有），e_only_naive = E 独有。
    dk_naive = key_strings(d, keys, set())
    ek_naive = key_strings(e, keys, set())
    d_set_naive = set(pd.unique(dk_naive).tolist())
    e_set_naive = set(pd.unique(ek_naive).tolist())
    d_only_naive = len(d_set_naive - e_set_naive)
    e_only_naive = len(e_set_naive - d_set_naive)
    common_naive = len(d_set_naive & e_set_naive)

    print('  归一化前：共有=%-8d D独有=%-7d E独有=%-7d' % (common_naive, d_only_naive, e_only_naive))
    print('  归一化后：共有=%-8d D独有=%-7d E独有=%-7d' % (common, d_only, e_only))
    print('  E独有被格式虚增=%d   D独有被格式虚增=%d'
          % (e_only_naive - e_only, d_only_naive - d_only))
    print('  D 唯一键=%d(冗余%d)  E 唯一键=%d(冗余%d)'
          % (len(d_u), d_red, len(e_u), e_red))
    print('  >>> 归一化后 R3 结果行数 = %d + %d = %d'
          % (len(d), e_only, len(d) + e_only))

    # 归一化后 E 新增键的日期范围（用归一化列，可读）
    new_keys = [k for k in e_u.tolist() if k not in d_set]
    if new_keys:
        idx = {k: i for i, k in enumerate(ek)}
        pos = [idx[k] for k in new_keys]
        sub = e.iloc[pos]
        for k in keys:
            if k in date_cols:
                v = sub[k].map(norm_date)
                print('     E新增键 %s 范围: %s ~ %s' % (k, v.min(), v.max()))
                break
    del d, e


def main():
    only = sys.argv[1:] or None
    failed = []
    for name, keys in FIN_TABLES:
        if only and name not in only:
            continue
        try:
            analyse(name, keys)
        except Exception as exc:
            failed.append(name)
            print('  !! 异常 %s: %r' % (name, exc))
            import traceback
            traceback.print_exc()
    if failed:
        print('FAILED=%s' % ','.join(failed))
        sys.exit(1)
    print('DONE')


if __name__ == '__main__':
    main()
