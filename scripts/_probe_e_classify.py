# -*- coding: utf-8 -*-
"""
_probe_e_classify.py
====================
只读探针：为 E:\\temp\\行情数据 的每个文件建立迁移分类矩阵。
判定维度（全部只读 D/E 元数据 + 仅主键列，不做全列加载）：
  1. 行数 / 日期范围 / 列结构
  2. 键空间：D独有 / E新增 / 交集 / 内部重复
  3. 资产性质：FULL（全量快照，可整体替换）vs INCR（增量，只能合并）
FULL 判定：E 起始日期 <= D 起始日期 且 E 结束日期 >= D 结束日期 且 E 行数 >= D 行数
INCR 判定：E 起始日期 > D 起始日期（只覆盖尾部）
"""
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
E_FULL = os.path.join(E_ROOT, '个股行情全量')
D_BASE = r'D:\astock'

# (标识, E 源, D 目标, 主键, 日期列, 是否 MultiIndex 契约)
MAP = [
    ('daily_full', os.path.join(E_FULL, 'stock_daily_full.parquet'),
     os.path.join(D_BASE, 'daily', 'stock_daily.parquet'),
     ['trade_date', 'ts_code'], 'trade_date', True),
    ('daily_incr', os.path.join(E_ROOT, 'stock_daily.parquet'),
     os.path.join(D_BASE, 'daily', 'stock_daily.parquet'),
     ['trade_date', 'ts_code'], 'trade_date', False),
    ('finance/balancesheet', os.path.join(E_ROOT, 'balancesheet.parquet'),
     os.path.join(D_BASE, 'finance', 'balancesheet.parquet'),
     ['ts_code', 'end_date', 'report_type', 'ann_date'], 'ann_date', False),
    ('finance/cashflow', os.path.join(E_ROOT, 'cashflow.parquet'),
     os.path.join(D_BASE, 'finance', 'cashflow.parquet'),
     ['ts_code', 'end_date', 'report_type', 'ann_date'], 'ann_date', False),
    ('finance/income', os.path.join(E_ROOT, 'income.parquet'),
     os.path.join(D_BASE, 'finance', 'income.parquet'),
     ['ts_code', 'end_date', 'report_type', 'ann_date'], 'ann_date', False),
    ('finance/dividend', os.path.join(E_ROOT, 'dividend.parquet'),
     os.path.join(D_BASE, 'finance', 'dividend.parquet'),
     ['ts_code', 'end_date', 'ann_date', 'div_proc'], 'ann_date', False),
    ('finance/express', os.path.join(E_ROOT, 'express.parquet'),
     os.path.join(D_BASE, 'finance', 'express.parquet'),
     ['ts_code', 'end_date', 'ann_date'], 'ann_date', False),
    ('finance/fina_indicator', os.path.join(E_ROOT, 'fina_indicator.parquet'),
     os.path.join(D_BASE, 'finance', 'fina_indicator.parquet'),
     ['ts_code', 'end_date', 'ann_date'], 'ann_date', False),
    ('finance/forecast', os.path.join(E_ROOT, 'forecast.parquet'),
     os.path.join(D_BASE, 'finance', 'forecast.parquet'),
     ['ts_code', 'end_date', 'ann_date', 'type'], 'ann_date', False),
    ('finance/share_change_event', os.path.join(E_ROOT, 'share_change_event.parquet'),
     os.path.join(D_BASE, 'finance', 'share_change_event.parquet'),
     ['ts_code', 'change_date', 'change_reason'], 'change_date', False),
    ('lhb/top_list', os.path.join(E_ROOT, 'top_list.parquet'),
     os.path.join(D_BASE, 'lhb', 'top_list.parquet'),
     ['trade_date', 'ts_code'], 'trade_date', True),
    ('research/report_rc_daily', os.path.join(E_ROOT, 'report_rc_daily.parquet'),
     os.path.join(D_BASE, 'research', 'report_rc_daily.parquet'),
     ['ts_code', 'report_date', 'org_name', 'author_name', 'report_title'],
     'report_date', False),
    ('board/ths_daily', os.path.join(E_ROOT, 'ths_daily.parquet'),
     os.path.join(D_BASE, 'board', 'ths_daily.parquet'),
     ['trade_date', 'ts_code'], 'trade_date', False),
    ('board_fundflow/moneyflow_ind_ths', os.path.join(E_ROOT, 'moneyflow_ind_ths.parquet'),
     os.path.join(D_BASE, 'board_fundflow', 'moneyflow_ind_ths.parquet'),
     ['trade_date', 'ts_code'], 'trade_date', False),
    ('basic/stock_basic', os.path.join(E_ROOT, 'stock_basic_data.parquet'),
     os.path.join(D_BASE, 'basic', 'stock_basic.parquet'),
     ['ts_code'], 'list_date', False),
]


def meta(path):
    f = pq.ParquetFile(path)
    sch = f.schema_arrow
    return f.metadata.num_rows, list(sch.names), sch


def norm_date_series(s):
    """把日期列/索引归一为 YYYYMMDD 字符串（兼容 int / ISO 串 / Timestamp）。"""
    if s.dtype.kind == 'M':
        return s.dt.strftime('%Y%m%d')
    if s.dtype.kind in 'iu':
        return s.astype('int64').astype(str)
    out = s.astype(str)
    return out.str.slice(0, 10).str.replace('-', '', regex=False).str.replace('/', '', regex=False)


def read_keys(path, keys, date_col):
    """只读主键列（+ 日期列若不在主键内）。兼容 MultiIndex 与平铺两种布局。"""
    need = list(keys)
    f = pq.ParquetFile(path)
    have = set(f.schema_arrow.names)
    extra = [date_col] if date_col not in need and date_col in have else []
    df = pd.read_parquet(path, columns=need + extra)
    df = df.reset_index() if isinstance(df.index, pd.MultiIndex) else df
    return df, [c for c in extra]


def key_hashes(df, keys):
    sub = df[keys].copy()
    for c in keys:
        if sub[c].dtype.kind in 'iu':
            sub[c] = sub[c].astype('int64').astype(str)
        elif sub[c].dtype.kind == 'M':
            sub[c] = sub[c].dt.strftime('%Y%m%d')
        else:
            s = sub[c].astype(str)
            if c.endswith('_date') or c == 'trade_date':
                s = s.str.slice(0, 10).str.replace('-', '', regex=False).str.replace('/', '', regex=False)
            sub[c] = s
    return pd.util.hash_pandas_object(sub, index=False).to_numpy()


def rng(s):
    if len(s) == 0:
        return ('EMPTY', 'EMPTY')
    return (str(s.min()), str(s.max()))


print('=' * 100)
print('E 盘 15 文件 × D 目标 迁移分类矩阵（只读）')
print('pandas %s / pyarrow %s' % (pd.__version__, __import__('pyarrow').__version__))
print('=' * 100)

matrix = []
for tag, ep, dp, keys, dcol, need_multi in MAP:
    line = dict(tag=tag, e=ep, d=dp, keys=keys, date_col=dcol)
    print('')
    print('#' * 100)
    print('## %s' % tag)
    print('   E源 = %s' % ep)
    print('   D目标 = %s' % dp)
    if not os.path.isfile(ep):
        print('   [SKIP] E 源不存在')
        line['verdict'] = 'MISSING_E'
        matrix.append(line)
        continue
    if not os.path.isfile(dp):
        print('   [SKIP] D 目标不存在（首次落库）')
        line['verdict'] = 'MISSING_D'
        matrix.append(line)
        continue

    er, ec, esch = meta(ep)
    dr, dc, dsch = meta(dp)
    line.update(e_rows=er, d_rows=dr)
    print('   行数: D=%d  E=%d   列数: D=%d E=%d' % (dr, er, len(dc), len(ec)))
    d_only_c = [c for c in dc if c not in ec]
    e_only_c = [c for c in ec if c not in dc]
    if d_only_c or e_only_c or dc != ec:
        print('   列结构: D独有=%s | E独有=%s | 列序完全一致=%s' % (d_only_c, e_only_c, dc == ec))
    else:
        print('   列结构: 完全一致（%d 列，列序相同）' % len(dc))

    dk, dexp = read_keys(dp, keys, dcol)
    ek, _ = read_keys(ep, keys, dcol)
    missing = [k for k in keys if k not in dk.columns or k not in ek.columns]
    if missing:
        print('   [FAIL] 主键列缺失: %s' % missing)
        line['verdict'] = 'KEY_MISSING'
        matrix.append(line)
        continue

    ds = norm_date_series(dk[dcol]) if dcol in dk.columns else None
    es = norm_date_series(ek[dcol]) if dcol in ek.columns else None
    drng = rng(ds) if ds is not None else ('NA', 'NA')
    erng = rng(es) if es is not None else ('NA', 'NA')
    print('   日期范围[%s]: D=%s~%s  E=%s~%s' % (dcol, drng[0], drng[1], erng[0], erng[1]))
    line.update(d_range=drng, e_range=erng)

    dh = key_hashes(dk, keys)
    eh = key_hashes(ek, keys)
    d_uni = np.unique(dh)
    both = int(np.isin(eh, d_uni).sum())
    d_only = int(len(dh) - np.isin(dh, np.unique(eh)).sum())
    e_only = int(len(eh) - both)
    d_dup = int(len(dh) - len(d_uni))
    e_dup = int(len(eh) - len(np.unique(eh)))
    print('   键空间: 交集=%d  E新增=%d  D独有=%d | D内部重复=%d E内部重复=%d'
          % (both, e_only, d_only, d_dup, e_dup))
    print('   合并语义: D独有保留 + E全量 => %d 行；E整体替换 => %d 行（差 %d 行 = D独有）'
          % (d_only + len(ek), len(ek), d_only - e_dup))
    line.update(both=both, e_only=e_only, d_only=d_only, d_dup=d_dup, e_dup=e_dup)

    # 资产性质判定
    full_hist = (erng[0] != 'EMPTY' and drng[0] != 'EMPTY' and erng[0] <= drng[0])
    covers_tail = (erng[1] != 'EMPTY' and drng[1] != 'EMPTY' and erng[1] >= drng[1])
    if full_hist and covers_tail and er >= dr:
        verdict = 'FULL'
        why = 'E 起点(%s)<=D 起点(%s) 且 E 终点(%s)>=D 终点(%s) 且 E 行数 %d>=D 行数 %d' % (
            erng[0], drng[0], erng[1], drng[1], er, dr)
    elif not full_hist and covers_tail:
        verdict = 'INCR'
        why = 'E 起点(%s) > D 起点(%s)，仅覆盖尾部 => 增量' % (erng[0], drng[0])
    else:
        verdict = 'MIXED'
        why = '起点/终点覆盖不完整，需人工判定'
    line.update(verdict=verdict, why=why)
    print('   >>> 资产性质 = %s   (%s)' % (verdict, why))
    print('   >>> 整体替换将删除 D 独有键 %d 行；冲突键 %d 行由 E 覆盖' % (d_only, both))
    matrix.append(line)

print('')
print('=' * 100)
print('汇总矩阵')
print('=' * 100)
print('%-34s %-8s %12s %12s %10s %10s %10s  %s' % ('表', '性质', 'D行数', 'E行数', 'E新增', 'D独有', '交集', '日期(D→E)'))
for m in matrix:
    print('%-34s %-8s %12s %12s %10s %10s %10s  %s→%s' % (
        m['tag'], m.get('verdict', '-'), m.get('d_rows', '-'), m.get('e_rows', '-'),
        m.get('e_only', '-'), m.get('d_only', '-'), m.get('both', '-'),
        m.get('d_range', ('-', '-'))[1], m.get('e_range', ('-', '-'))[1]))
print('CLASSIFY_DONE')