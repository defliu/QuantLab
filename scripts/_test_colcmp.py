# -*- coding: utf-8 -*-
"""_colcmp.col_diff_flags 的判定正确性单测（只读，不碰生产数据）。"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _colcmp import col_diff_flags, is_date_col  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = list(np.asarray(got).tolist()) == list(want)
    print('%-58s %s  got=%s want=%s'
          % (label, 'PASS' if ok else 'FAIL',
             list(np.asarray(got).tolist()), list(want)), flush=True)
    if not ok:
        FAILED.append(label)


def base_of(k):
    """给定键数组，返回每行所属组的首行下标。"""
    gcode, _ = pd.factorize(pd.Series(k, dtype=object), sort=False)
    first = {}
    return np.array([first.setdefault(g, i) for i, g in enumerate(gcode)])


def run(label, series, keys, want, date_cols=(), name='x'):
    s = pd.Series(series, name=name)
    got = col_diff_flags(s, base_of(keys), set(date_cols), name)
    check(label, got, want)


print('=' * 100, flush=True)
print('1) 缺失值语义（express 假阳性根因）', flush=True)
run('全 None object 列 -> 组内全相同',
    [None, None, None], ['a', 'a', 'a'], [False, False, False])
run('None/NaN/pd.NA 混合 -> 全相同',
    [None, float('nan'), pd.NA], ['a', 'a', 'a'], [False, False, False])
run('一个 None 一个有值 -> 差异',
    [None, 'x'], ['a', 'a'], [False, True])
# 空串与 None 判为「差异」是刻意的保守设计：本审计用于决定能否自动去重，
# 误判 DIFFERS 只会多送人工裁决（安全），误判 IDENTICAL 会静默丢数据（危险）。
run('空串 vs None -> 保守判差异（不静默去重）',
    ['', None], ['a', 'a'], [False, True])

print(flush=True)
print('2) 字符串数字的表示差异（数值兜底）', flush=True)
run('str 数字 vs float 数字 -> 相同',
    ['0.22275', 0.22275], ['a', 'a'], [False, False])
run('str 数字不同值 -> 差异',
    ['0.22275', '0.0328041'], ['a', 'a'], [False, True])
run('非数字字符串 -> 走精确比较',
    ['实施', '实施'], ['a', 'a'], [False, False])
run('非数字字符串不同 -> 差异',
    ['实施', '预案'], ['a', 'a'], [False, True])

print(flush=True)
print('3) 数值列容差', flush=True)
run('浮点尾差 -> 相同',
    [1.0, 1.0 + 1e-12], ['a', 'a'], [False, False])
run('真实数值差 -> 差异',
    [1.0, 1.5], ['a', 'a'], [False, True])
run('int 与 float 同值 -> 相同',
    [1, 1.0], ['a', 'a'], [False, False])
run('bool 列', [True, True], ['a', 'a'], [False, False])

print(flush=True)
print('4) 日期归一化', flush=True)
run('ISO vs YYYYMMDD -> 相同',
    ['2024-04-27 00:00:00', '20240427'], ['a', 'a'], [False, False],
    date_cols={'f_ann_date'}, name='f_ann_date')
run('不同日期 -> 差异',
    ['2024-04-27', '20240428'], ['a', 'a'], [False, True],
    date_cols={'f_ann_date'}, name='f_ann_date')
run('日期列缺失 -> 相同',
    [None, None], ['a', 'a'], [False, False],
    date_cols={'f_ann_date'}, name='f_ann_date')
# 非日期列不应被归一化：ISO 串保持原样比较（体现 name 匹配是 API 前提）
run('非日期列不做日期归一化',
    ['2024-04-27', '20240427'], ['a', 'a'], [False, True], name='remark')

print(flush=True)
print('5) 多组混合 + 首行豁免', flush=True)
run('两组各自内部相同',
    [None, None, 'x', 'x', 'y'],
    ['a', 'a', 'b', 'b', 'c'],
    [False, False, False, False, False])
run('第二组内部有差异',
    [None, None, 'x', 'z', 'y'],
    ['a', 'a', 'b', 'b', 'c'],
    [False, False, False, True, False])

print(flush=True)
print('6) update_flag 不被当日期列', flush=True)
for nm, want in [('update_flag', False), ('end_date', True),
                 ('f_ann_date', True), ('report_date', True),
                 ('cal_date', True), ('trade_cal', False),
                 ('update', False), ('list_date', True),
                 ('pay_date', True), ('month', True)]:
    got = is_date_col(nm)
    ok = got == want
    print('  is_date_col(%-14s) = %-5s %s'
          % (nm, got, 'PASS' if ok else 'FAIL'), flush=True)
    if not ok:
        FAILED.append('is_date_col(%s)' % nm)

print(flush=True)
print('FAILED=%d' % len(FAILED), flush=True)
for f in FAILED:
    print('  - %s' % f, flush=True)
sys.exit(1 if FAILED else 0)