# -*- coding: utf-8 -*-
"""精确审计日期列的字符串形态分布（不做前8位截断），识别 ISO 混入。

背景：_audit_date_dtype.py 的形态统计只取前 8 位，会把 '2026-09-15' 和
'20260915' 都归为 99999999，从而掩盖 ISO 混入。若 E 的日期列混有 ISO 格式，
str() 归一化主键会导致 '2026-09-15' 与 '20260915' 永不匹配，
「E 独有键」被虚增、「共有键」被低估。

本脚本对每个日期列统计：
  1. 完整形态分布（不截断）
  2. 纯 YYYYMMDD 占比
  3. ISO 类（YYYY-MM-DD / YYYY-MM-DD HH:MM:SS）占比
  4. 规范化后（去分隔符取前8位）唯一值数 vs 原始唯一值数 —— 差额=格式重复量
"""
import os
import re
import sys

import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

PAIRS = [
    ('dividend', r'finance\dividend.parquet', 'dividend.parquet',
     ['end_date', 'ann_date', 'record_date', 'ex_date', 'pay_date', 'imp_ann_date']),
    ('express', r'finance\express.parquet', 'express.parquet', ['end_date', 'ann_date']),
    ('forecast', r'finance\forecast.parquet', 'forecast.parquet', ['end_date', 'ann_date']),
    ('balancesheet', r'finance\balancesheet.parquet', 'balancesheet.parquet',
     ['end_date', 'ann_date', 'f_ann_date']),
    ('cashflow', r'finance\cashflow.parquet', 'cashflow.parquet',
     ['end_date', 'ann_date', 'f_ann_date']),
    ('income', r'finance\income.parquet', 'income.parquet',
     ['end_date', 'ann_date', 'f_ann_date']),
    ('fina_indicator', r'finance\fina_indicator.parquet', 'fina_indicator.parquet',
     ['end_date', 'ann_date']),
    ('report_rc_daily', r'research\report_rc_daily.parquet', 'report_rc_daily.parquet',
     ['report_date', 'quarter']),
]

RE_PURE8 = re.compile(r'^\d{8}$')
RE_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?$')
RE_ANY8 = re.compile(r'(\d{4})-?(\d{2})-?(\d{2})')


def classify(v):
    s = str(v).strip()
    if s.lower() in ('nan', 'none', '<na>', ''):
        return 'NA'
    if RE_PURE8.match(s):
        return 'YYYYMMDD'
    if RE_ISO.match(s):
        return 'ISO'
    return 'OTHER'


def profile(tag, df, cols):
    for c in cols:
        if c not in df.columns:
            continue
        s = df[c]
        kinds = s.map(classify).value_counts().to_dict()
        nn = s.dropna()
        raw_uniq = nn.astype(str).nunique()
        norm_uniq = nn.astype(str).str.replace(r'[\s\-:T]', '', regex=True).str.slice(0, 8).nunique()
        dupfmt = raw_uniq - norm_uniq
        flag = '  <<< ISO/OTHER 混入' if (kinds.get('ISO', 0) or kinds.get('OTHER', 0)) else ''
        print('  %s %-14s dtype=%-12s %s%s'
              % (tag, c, str(s.dtype), kinds, flag))
        if dupfmt:
            print('  %s %-14s 原始唯一=%d 规范化唯一=%d 差=%d（格式重复）'
                  % (tag, c, raw_uniq, norm_uniq, dupfmt))
            bad = nn.astype(str)[~nn.astype(str).map(lambda v: RE_PURE8.match(str(v).strip()) is not None)]
            print('  %s %-14s 样例非YYYYMMDD: %s'
                  % (tag, c, list(dict.fromkeys(bad.tolist()))[:5]))


def main():
    only = sys.argv[1:] or None
    for name, d_rel, e_fname, cols in PAIRS:
        if only and name not in only:
            continue
        print('=' * 104)
        print('表：%s' % name)
        d = pd.read_parquet(os.path.join(D_BASE, d_rel))
        e = pd.read_parquet(os.path.join(E_ROOT, e_fname))
        profile('D', d, cols)
        profile('E', e, cols)
        del d, e


if __name__ == '__main__':
    main()
