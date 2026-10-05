# -*- coding: utf-8 -*-
"""list_status / delist_date 真实取值审计（只读，重要）。

背景：stock_basic 逐列对比显示 list_status 在 D/E 两侧 5904 行"全不同"
（D=NaN, E=''）。但 Project_10 退市排雷、Project_16 幸存者偏差审计
都把 list_status 当生产字段用。若两侧都空，这些逻辑其实从未生效。

必须查清：
  1. list_status 在 D / E 的实际分布（是否全空）
  2. delist_date 是否有任何非空值
  3. gen_qmt_csv.py 产出的 delist_info.csv 现状（下游实际拿到什么）
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DP = r'D:\astock\basic\stock_basic.parquet'
EP = r'E:\temp\行情数据\stock_basic_data.parquet'
CSV = r'D:/QMT_POOL/delist_info.csv'


def say(*a):
    print(*a)
    sys.stdout.flush()


def main():
    say('=' * 92)
    say('### 1. list_status / delist_date 实际分布')
    for tag, p in (('D', DP), ('E', EP)):
        df = pd.read_parquet(p, columns=['ts_code', 'list_status', 'delist_date'])
        ls = df['list_status']
        dd = pd.to_datetime(df['delist_date'], errors='coerce')
        say('')
        say('  [%s] 行数=%d' % (tag, len(df)))
        say('      list_status dtype=%s' % ls.dtype)
        say('      list_status 分布(dropna=False)=%s'
            % ls.value_counts(dropna=False).head(10).to_dict())
        say('      list_status 非空数=%d  空/NaN 数=%d'
            % (int(ls.notna().sum()), int(ls.isna().sum())))
        say('      delist_date 非空数=%d' % int(dd.notna().sum()))
        if dd.notna().any():
            say('      delist_date 范围=%s ~ %s' % (dd.min(), dd.max()))
            say('      退市样本=%s'
                % df.loc[dd.notna(), 'ts_code'].head(10).tolist())

    say('')
    say('=' * 92)
    say('### 2. 用 == "D" / == "L" 筛选会命中多少只（下游实际效果）')
    for tag, p in (('D', DP), ('E', EP)):
        df = pd.read_parquet(p, columns=['ts_code', 'list_status'])
        ls = df['list_status']
        say('  [%s] list_status=="D" 命中=%d   =="L" 命中=%d'
            % (tag, int((ls == 'D').sum()), int((ls == 'L').sum())))
        say('        (D 侧全 NaN 时两者必为 0；E 侧全 \'\' 时亦为 0)')

    say('')
    say('=' * 92)
    say('### 3. 下游 delist_info.csv 现状')
    if os.path.exists(CSV):
        c = pd.read_csv(CSV, dtype=str)
        say('  路径=%s  行数=%d  列=%s' % (CSV, len(c), list(c.columns)))
        for col in c.columns:
            vc = c[col].value_counts(dropna=False).head(6)
            say('    %-12s 分布=%s' % (col, vc.to_dict()))
        say('  文件修改时间=%s' % pd.Timestamp(
            os.path.getmtime(CSV), unit='s'))
    else:
        say('  [不存在] %s' % CSV)

    say('')
    say('=' * 92)
    say('### 4. 其他疑似空列交叉检查（D vs E）')
    cols = ['fullname', 'enname', 'curr_type', 'is_hs', 'fill_source',
            'exchange', 'area', 'industry', 'act_ent_type']
    d = pd.read_parquet(DP)
    e = pd.read_parquet(EP)
    say('  %-14s %-28s %-28s' % ('列', 'D 空/NaN 数', 'E 空/NaN 数'))
    for c in cols:
        if c not in d.columns or c not in e.columns:
            continue
        s1, s2 = d[c], e[c]
        n1 = int(s1.isna().sum() + (s1 == '').sum())
        n2 = int(s2.isna().sum() + (s2 == '').sum())
        say('  %-14s %-28s %-28s'
            % (c, '%d/%d' % (n1, len(s1)), '%d/%d' % (n2, len(s2))))
    say('')
    say('LS_DONE')


if __name__ == '__main__':
    main()
