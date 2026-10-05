# -*- coding: utf-8 -*-
"""_diag_fin_rowgroup.py

决定性诊断：E 财务表里「非空率暴跌」的列，是否只在**部分 row group** 内有值。

背景（_diag_fin_colshift 已排除列错位）：
  fina_indicator 在 E 中有一半列非空率 ~99%，另一半 ~0.05%，
  且坏列物理位置分散。若坏列只在末尾若干 row group 有值 -> 文件写入时
  该列只写了一段 = **数据损坏，不可用于迁移**；
  若各 row group 均匀 -> 真实业务/版本缺失，另行处理。

同时做：取具体 (ts_code, end_date) 打印 D/E 全列值，人眼核对语义。

输出：_diag_fin_rowgroup.out.txt
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_FIN = r'D:\astock\finance'
E_ROOT = r'E:\temp\行情数据'
RESIDUAL = '__index_level_0__'

PROBE_COLS = ['eps', 'roe', 'roe_avg', 'gross_margin', 'netprofit_margin',
              'bps', 'q_profit_yoy', 'ocf_to_or', 'dtprofit_to_profit',
              'op_income', 'profit_to_gr', 'update_flag', 'end_date']


def hr(t):
    print('')
    print('=' * 92)
    print(t)
    print('=' * 92)


def rg_scan(path, label):
    pf = pq.ParquetFile(path)
    schema = list(pf.schema_arrow.names)
    cols = [c for c in PROBE_COLS if c in schema]
    nrg = pf.num_row_groups
    print('')
    print('--- [%s] row_group 总数=%d  文件大小=%.1f MB ---'
          % (label, nrg, os.path.getsize(path) / 1048576.0))
    hdr = '  rg   rows      ' + '  '.join('%-13s' % c[:13] for c in cols)
    print(hdr)
    sample = list(range(nrg)) if nrg <= 14 else \
        list(range(min(6, nrg))) + [-1] + list(range(max(0, nrg - 6), nrg))
    seen = set()
    for i in sample:
        if i in seen:
            continue
        seen.add(i)
        t = pf.read_row_group(i, columns=cols).to_pandas()
        n = len(t)
        parts = []
        for c in cols:
            if c in ('end_date',):
                parts.append('%-13s' % '-')
            else:
                parts.append('%-13s' % ('%d/%d' % (int(t[c].notna().sum()), n)))
        print('  %-4d %-9d %s' % (i, n, '  '.join(parts)))


def main():
    print('=' * 92)
    print('fina_indicator row-group 级非空率扫描（判断坏列是否只写在部分 row group）')
    print('=' * 92)
    for label, path in (('D', os.path.join(D_FIN, 'fina_indicator.parquet')),
                        ('E', os.path.join(E_ROOT, 'fina_indicator.parquet'))):
        try:
            rg_scan(path, label)
        except Exception as exc:
            print('[%s] 扫描失败: %s: %s' % (label, type(exc).__name__, exc))

    hr('取证：同一 (ts_code, end_date) 的 D vs E 全列值（坏列是否语义对得上）')

    def prep(path):
        df = pd.read_parquet(path)
        if RESIDUAL in df.columns:
            df = df.drop(columns=[RESIDUAL])
        return df

    d_all = prep(os.path.join(D_FIN, 'fina_indicator.parquet'))
    e_all = prep(os.path.join(E_ROOT, 'fina_indicator.parquet'))
    for c in ('end_date', 'ann_date'):
        if c in d_all.columns:
            d_all[c] = d_all[c].astype(str).str.replace('-', '', regex=False)
        if c in e_all.columns:
            e_all[c] = e_all[c].astype(str).str.replace('-', '', regex=False)

    # 挑一个 roe_avg 在 D/E 都有值的报告期
    lk = ['ts_code', 'end_date']
    d1 = d_all.drop_duplicates(subset=lk)
    e1 = e_all.drop_duplicates(subset=lk)
    m = d1.merge(e1, on=lk, how='inner', suffixes=('_d', '_e'))
    print('D/E 共同 (ts_code,end_date) 键数=%d' % len(m))

    # 找一个 D 有 roe_avg、E 也有的
    cand = m[(m.get('roe_avg_d', pd.Series(dtype=object)).notna())
             & (m.get('roe_avg_e', pd.Series(dtype=object)).notna())]
    print('其中 roe_avg 双边都有的键数=%d' % len(cand))
    if len(cand):
        row = cand.iloc[0]
        print('')
        print('样本键: ts_code=%s end_date=%s' % (row['ts_code'], row['end_date']))
        show = ['eps', 'roe', 'roe_avg', 'gross_margin', 'netprofit_margin',
                'bps', 'ocf_to_or', 'dtprofit_to_profit', 'q_profit_yoy',
                'op_income', 'profit_to_gr', 'update_flag']
        print('  %-22s %22s %22s' % ('字段', 'D', 'E'))
        for c in show:
            cd, ce = c + '_d', c + '_e'
            print('  %-22s %22s %22s'
                  % (c, row.get(cd, '<无>'), row.get(ce, '<无>')))

    hr('坏列在 E 中非空行的 end_date 年份分布（业务缺失 vs 分段写入）')
    for c in ['roe_avg', 'q_profit_yoy', 'ocf_to_or']:
        if c not in e_all.columns:
            continue
        sub = e_all[e_all[c].notna()]
        yrs = sub['end_date'].astype(str).str[:4].value_counts().sort_index()
        print('')
        print('  [%s] 非空=%d  按报告年份: %s' % (c, len(sub), dict(yrs)))

    print('')
    print('ROWGROUP_DIAG_DONE')


if __name__ == '__main__':
    main()
