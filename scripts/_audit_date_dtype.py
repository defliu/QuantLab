# -*- coding: utf-8 -*-
"""核查 E 盘财务表日期列是否混用类型/格式——若混用，str() 归一化主键会漏配。"""
import os
import sys

import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

PAIRS = [
    ('dividend', 'ann_date', r'finance\dividend.parquet', 'dividend.parquet', ['end_date', 'ann_date', 'record_date', 'ex_date', 'pay_date', 'imp_ann_date']),
    ('express', 'ann_date', r'finance\express.parquet', 'express.parquet', ['end_date', 'ann_date']),
    ('forecast', 'ann_date', r'finance\forecast.parquet', 'forecast.parquet', ['end_date', 'ann_date']),
    ('balancesheet', 'ann_date', r'finance\balancesheet.parquet', 'balancesheet.parquet', ['end_date', 'ann_date', 'f_ann_date']),
    ('cashflow', 'ann_date', r'finance\cashflow.parquet', 'cashflow.parquet', ['end_date', 'ann_date', 'f_ann_date']),
    ('income', 'ann_date', r'finance\income.parquet', 'income.parquet', ['end_date', 'ann_date', 'f_ann_date']),
    ('fina_indicator', 'ann_date', r'finance\fina_indicator.parquet', 'fina_indicator.parquet', ['end_date', 'ann_date']),
]


def profile(tag, df, cols):
    for c in cols:
        if c not in df.columns:
            continue
        s = df[c]
        kinds = s.map(lambda v: type(v).__name__).value_counts().to_dict()
        uniq = s.dropna().astype(str).str.replace(r'[\s\-:T]', '', regex=True).str.slice(0, 8)
        pat = uniq.str.replace(r'\d', '9', regex=True).value_counts().to_dict()
        print('  %s %-14s dtype=%-12s 类型分布=%s' % (tag, c, str(s.dtype), kinds))
        print('  %s %-14s 日期形态分布(前3)=%s' % (tag, c, dict(list(pat.items())[:3])))


def main():
    for name, _k, d_rel, e_fname, cols in PAIRS:
        d_path = os.path.join(D_BASE, d_rel)
        e_path = os.path.join(E_ROOT, e_fname)
        print('=' * 100)
        print('表：%s' % name)
        if not os.path.isfile(e_path):
            print('  !! E 缺失 %s' % e_path)
            continue
        d = pd.read_parquet(d_path)
        e = pd.read_parquet(e_path)
        profile('D', d, cols)
        profile('E', e, cols)
        del d, e


if __name__ == '__main__':
    main()
