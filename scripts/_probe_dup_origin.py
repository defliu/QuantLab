# coding=utf-8
"""只读探针：定位 FULL_KEY 重复是 E 自身、D 自身，还是 concat 引入。"""
import os
import sys

import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'
RESIDUAL = '__index_level_0__'

FULL_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
}
DATE_COLS = ['end_date', 'ann_date', 'f_ann_date', 'change_date']


def canon(s):
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d')
    return s.astype(str).str.replace(r'\.0$', '', regex=True)


def load(path):
    df = pd.read_parquet(path)
    if RESIDUAL in df.columns:
        df = df.drop(columns=[RESIDUAL])
    df = df.reset_index()
    for c in DATE_COLS:
        if c in df.columns:
            df[c] = canon(df[c])
    return df


def main():
    for tab, fk in FULL_KEYS.items():
        ep = os.path.join(E_ROOT, tab + '.parquet')
        dp = os.path.join(D_FIN, tab + '.parquet')
        if not (os.path.exists(ep) and os.path.exists(dp)):
            print('[SKIP] %s' % tab)
            continue
        e = load(ep)
        d = load(dp)
        de = int(e.duplicated(subset=fk).sum())
        dd = int(d.duplicated(subset=fk).sum())
        print('')
        print('=' * 84)
        print('%s  FULL_KEY=%s' % (tab, '+'.join(fk)))
        print('=' * 84)
        print('  E 自身重复=%-8d / %d 行' % (de, len(e)))
        print('  D 自身重复=%-8d / %d 行' % (dd, len(d)))

        # 抽一个重复键，看两行到底差在哪
        dupmask = e.duplicated(subset=fk, keep=False)
        if dupmask.any():
            sample_key = e.loc[dupmask, fk].iloc[0]
            m = pd.Series(True, index=e.index)
            for c in fk:
                m &= (e[c] == sample_key[c])
            g = e[m]
            print('  样本键 %s -> E 中 %d 行' % (dict(zip(fk, sample_key)), len(g)))
            if len(g) > 1:
                diff = []
                for c in g.columns:
                    nun = g[c].nunique(dropna=False)
                    if nun > 1:
                        diff.append('%s(%d种)' % (c, nun))
                print('    该键下取值不一致的列: %s'
                      % (', '.join(diff[:15]) if diff else '无（整行完全相同 -> 纯重复行）'))

        # D 独有行里，是否有与 E 重复的（理论上不该有）
        ek = pd.MultiIndex.from_frame(e[fk])
        d_only = ~pd.MultiIndex.from_frame(d[fk]).isin(ek)
        de_only = d[d_only]
        print('  D 独有行自身重复=%d' % int(de_only.duplicated(subset=fk).sum()))

    print('')
    print('DUP_ORIGIN_DIAG_DONE')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()