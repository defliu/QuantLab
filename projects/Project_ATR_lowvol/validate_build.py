#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ATR 等权 build 验证脚本 - 固化四道验证闸 + 常规闸
每次构建后必跑，确保产物质量。
"""
import ast
import sys
import re

BUILD_PATH = r'D:\QuantLab\projects\Project_ATR_lowvol\build\strategy_atr_lowvol_equalweight.py'

def main():
    with open(BUILD_PATH, 'rb') as f:
        data = f.read()

    # 1. GBK 可解码
    try:
        txt = data.decode('gbk')
    except Exception as e:
        print('FAIL: GBK decode failed:', e)
        return 1

    # 2. 首行 # coding=gbk
    if not txt.startswith('# coding=gbk'):
        print('FAIL: First line not # coding=gbk')
        return 1

    # 3. py3.6 语法检查
    try:
        ast.parse(txt, feature_version=(3, 6))
    except SyntaxError as e:
        print('FAIL: Python 3.6 syntax error:', e)
        return 1

    # 4. 无 f-string (排除 "%.2f" % ... 这种格式化字符串中的假阳性)
    fstring_pattern = re.compile(r'f["\'][^"\']*\{[^"\']*\}')
    if fstring_pattern.search(txt):
        print('FAIL: Found real f-string')
        return 1

    # 5. 无 walrus :=
    if ':=' in txt:
        print('FAIL: Found walrus operator')
        return 1

    # 6. 无 dict[str
    if 'dict[' in txt:
        print('FAIL: Found dict[type] annotation')
        return 1

    # 7. 无 MOCK/mock 残留
    if re.search(r'\b[Mm][Oo][Cc][Kk]\b', txt):
        print('FAIL: Found MOCK residue')
        return 1

    # 8. 关键字符串
    required = ['沪深A股', '风险警示板', '买入', '卖出', 'ATR_EW', '止损', '止盈']
    for s in required:
        if s not in txt:
            print('FAIL: Missing required string:', s)
            return 1

    # 9. 问号计数 < 10
    q_count = txt.count('?')
    if q_count >= 10:
        print('FAIL: Too many question marks:', q_count)
        return 1

    # 10. BUILD_TAG 格式
    m = re.search(r'BUILD_TAG = "(\d{8}-\d{6})"', txt)
    if not m:
        print('FAIL: BUILD_TAG format error')
        return 1
    build_tag = m.group(1)

    # 11. 验证 4a/4b/4c 逻辑存在
    if '仓位缩减' not in txt:
        print('FAIL: 4a position trim missing')
        return 1
    if not ('if selected:' in txt and '_g_last_rebalance_key = key' in txt):
        print('FAIL: 4b if selected fix missing')
        return 1
    if 'm_dAvailable' not in txt:
        print('FAIL: 4c three-level fallback missing')
        return 1

    print('ALL VALIDATIONS PASSED')
    print('BUILD_TAG:', build_tag)
    print('Question marks:', q_count)
    print('File size:', len(data), 'bytes')
    return 0

if __name__ == '__main__':
    sys.exit(main())