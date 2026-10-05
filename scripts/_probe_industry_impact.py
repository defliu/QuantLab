# -*- coding: utf-8 -*-
"""stock_basic industry 列在 D / E 两侧的下游影响模拟（只读）。

关注点：
  industry_map.load_industry_map() 的 str(ind) if ind is not None 语义
  —— NaN 不是 None，NaN 会被转成字符串 "nan"，进入行业 cap 字典。
本脚本直接调用生产函数，验证替换前后返回值差异。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, r'D:\QuantLab')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DP = r'D:\astock\basic\stock_basic.parquet'
EP = r'E:\temp\行情数据\stock_basic_data.parquet'


def say(*a):
    print(*a)
    sys.stdout.flush()


def raw_industry_map(path):
    """复刻 industry_map.load_industry_map 的核心逻辑（不做缓存）。"""
    df = pd.read_parquet(path, columns=['ts_code', 'industry'])
    out = {}
    for _, row in df.iterrows():
        code = row.get('ts_code')
        if code is None:
            continue
        ind = row.get('industry')
        out[str(code)] = str(ind) if ind is not None else ''
    return out


def main():
    say('=' * 88)
    say('### 1. industry 列原始 dtype 与空值形态')
    for tag, p in (('D', DP), ('E', EP)):
        df = pd.read_parquet(p, columns=['ts_code', 'industry'])
        s = df['industry']
        say('  [%s] dtype=%s  行数=%d' % (tag, s.dtype, len(s)))
        say('       notna=%d  isna=%d  空串""=%d  字面"nan"=%d'
            % (int(s.notna().sum()), int(s.isna().sum()),
               int((s == '').sum()), int((s == 'nan').sum())))
    say('')

    say('=' * 88)
    say('### 2. 经 load_industry_map 逻辑后的取值形态（下游真实拿到什么）')
    dm = raw_industry_map(DP)
    em = raw_industry_map(EP)
    for tag, m in (('D', dm), ('E', em)):
        vals = pd.Series(list(m.values()))
        say('  [%s] 股票数=%d' % (tag, len(m)))
        say('       取值为"nan"字符串的股票数=%d' % int((vals == 'nan').sum()))
        say('       取值为空的股票数=%d' % int((vals == '').sum()))
    say('')

    say('=' * 88)
    say('### 3. 行业 cap 视角：映射结果发生变化的股票')
    diff = [k for k in dm if k in em and dm[k] != em[k]]
    say('  映射结果变化=%d 只' % len(diff))
    for k in diff[:20]:
        say('    %s : D=%r -> E=%r' % (k, dm[k], em[k]))
    say('')

    say('=' * 88)
    say('### 4. 若按 notna 判空（旧 D 口径）会怎样')
    d = pd.read_parquet(DP, columns=['ts_code', 'industry'])
    e = pd.read_parquet(EP, columns=['ts_code', 'industry'])
    say('  D: industry 非空占比 = %.4f' % (d['industry'].notna().mean()))
    say('  E: industry 非空占比 = %.4f' % (e['industry'].notna().mean()))
    dnull = set(d.loc[d['industry'].isna(), 'ts_code'])
    enull = set(e.loc[e['industry'].isna(), 'ts_code'])
    say('  D 判为空的股票数=%d  E 判为空的股票数=%d  差=%d'
        % (len(dnull), len(enull), len(dnull) - len(enull)))
    say('  E 新获得行业值的股票（%d 只）: %s'
        % (len(dnull - enull), sorted(dnull - enull)[:20]))
    say('')
    say('IND_DONE')


if __name__ == '__main__':
    main()
