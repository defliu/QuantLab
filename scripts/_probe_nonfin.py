# -*- coding: utf-8 -*-
"""非财务全量表审计（只读）。

结构事实：
  parquet Arrow schema 里 key 列是普通字段，但 pandas 依 pandas_metadata
  把它们还原成 index 层 -> 必须先 materialize 才能按列访问。

性能要点（本版修正）：
  - canon_date 向量化，避免逐元素 Python 回调（百万行级热点）
  - 覆盖率一律用 notna()，不对文本列做 to_numeric（原实现对 report_title
    这类长中文做数值转换，既慢又语义错误）
  - 键集合运算走 pandas Index，而非 python set + isin
  - 逐表 flush，便于中途观察进度

只读：不写任何生产数据。
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

# (显示名, D 子目录, E 文件名, D 文件名, 业务主键)
TABLES = [
    ('top_list', 'lhb', 'top_list.parquet', 'top_list.parquet',
     ['trade_date', 'ts_code']),
    ('report_rc_daily', 'research', 'report_rc_daily.parquet',
     'report_rc_daily.parquet',
     ['ts_code', 'report_date', 'org_name', 'author_name',
      'report_title', 'quarter']),
    ('ths_daily', 'board', 'ths_daily.parquet', 'ths_daily.parquet',
     ['trade_date', 'ts_code']),
    ('moneyflow_ind_ths', 'board_fundflow', 'moneyflow_ind_ths.parquet',
     'moneyflow_ind_ths.parquet', ['trade_date', 'ts_code']),
    ('stock_basic', 'basic', 'stock_basic_data.parquet', 'stock_basic.parquet',
     ['ts_code']),
]

DATE_KEYS = {'trade_date', 'report_date', 'ann_date', 'end_date',
             'list_date', 'delist_date', 'f_ann_date'}

BAD = ('nan', 'NaT', 'None', '', '--')


def say(*a):
    print(*a)
    sys.stdout.flush()


def materialize(df, keys):
    """把落在 index 层的主键列搬回普通列。返回 (df, 缺失列列表)。"""
    missing = [c for c in keys if c not in df.columns]
    if not missing:
        return df, []
    have = [c for c in keys if c in (df.index.names or [])]
    if have:
        df = df.reset_index()
    return df, [c for c in keys if c not in df.columns]


def canon_date(s):
    """向量化日期归一化 -> YYYYMMDD 字符串，缺失统一 'NA'。"""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d').fillna('NA').astype(str)
    t = s.astype(str).str.strip()
    t = t.where(~t.isin(BAD), 'NA')
    dg = t.str.split(' ').str[0].str.replace(r'\D', '', regex=True)
    out = dg.where(dg.str.len() == 8, 'NA')
    need = (out == 'NA') & (t != 'NA')
    if need.any():
        p = pd.to_datetime(t.where(need), errors='coerce')
        out = out.where(~need, p.dt.strftime('%Y%m%d').fillna('NA'))
    return out.fillna('NA').astype(str)


def build_key(df, keys):
    parts = []
    for c in keys:
        s = df[c]
        if c in DATE_KEYS:
            s = canon_date(s)
        else:
            s = s.astype(str).str.strip()
            s = s.where(~s.isin(BAD), 'NA')
        parts.append(s.astype(str))
    return parts[0].str.cat(others=parts[1:], sep='|')


def rng(df, keys):
    """只对确属日期语义的主键列报范围。

    不能对所有 key 列跑 canon_date：report_title / org_name / author_name /
    quarter 是文本，dateutil 会把它们解析成伪日期（实测 report_title 输出
    [00202447~97629241]），既无意义又对百万行长文本极慢。
    """
    out = []
    for c in keys:
        if c not in df.columns or c not in DATE_KEYS:
            continue
        s = canon_date(df[c])
        v = s[s != 'NA']
        if len(v):
            out.append('%s[%s~%s]' % (c, v.min(), v.max()))
    return ' '.join(out) if out else '(主键内无日期语义列)'


def coverage(df):
    """每列非空覆盖率，纯 notna 语义（不做数值转换）。"""
    n = max(len(df), 1)
    return {c: int(df[c].notna().sum()) / n for c in df.columns}


def probe(name, sub, efile, dfile, keys):
    ep = os.path.join(E_ROOT, efile)
    dp = os.path.join(D_BASE, sub, dfile)
    say('')
    say('=' * 100)
    say('### %s   D:%s\\%s   E:%s' % (name, sub, dfile, efile))
    if not os.path.exists(ep):
        say('  [E 文件不存在，跳过] %s' % ep)
        return
    if not os.path.exists(dp):
        say('  [D 文件不存在，跳过] %s' % dp)
        return
    e = pd.read_parquet(ep)
    d = pd.read_parquet(dp)
    say('  D index_names=%s' % (list(d.index.names),))
    say('  E index_names=%s' % (list(e.index.names),))
    for x in (e, d):
        if 'index' in x.columns:
            x.drop(columns=['index'], inplace=True)

    e, emiss = materialize(e, keys)
    d, dmiss = materialize(d, keys)
    if dmiss or emiss:
        say('  [主键列缺失 D=%s E=%s，无法做键空间比对]' % (dmiss, emiss))
        return

    say('  D 行=%d  E 行=%d' % (len(d), len(e)))
    say('  D 列=%d  E 列=%d' % (len(d.columns), len(e.columns)))
    only_e = [c for c in e.columns if c not in d.columns]
    only_d = [c for c in d.columns if c not in e.columns]
    say('  E 独有列=%s' % (only_e if only_e else '无'))
    say('  D 独有列=%s' % (only_d if only_d else '无'))

    diff = []
    for c in d.columns:
        if c in e.columns and str(d[c].dtype) != str(e[c].dtype):
            diff.append((c, str(d[c].dtype), str(e[c].dtype)))
    say('  dtype 差异=%s' % (diff if diff else '无'))
    say('  D 键日期范围: %s' % rng(d, keys))
    say('  E 键日期范围: %s' % rng(e, keys))

    d['_k'] = build_key(d, keys)
    e['_k'] = build_key(e, keys)
    di = pd.Index(d['_k'].unique())
    ei = pd.Index(e['_k'].unique())
    common = di.intersection(ei)
    donly = di.difference(ei)
    eonly = ei.difference(di)
    cmask = d['_k'].isin(common)
    dmrow = int(d['_k'].isin(donly).sum())
    emrow = int(e['_k'].isin(eonly).sum())
    say('  主键=%s' % '+'.join(keys))
    say('  共同键=%d  D-only=%d(行%d)  E-only=%d(行%d)'
        % (len(common), len(donly), dmrow, len(eonly), emrow))
    say('  主键重复行: D=%d  E=%d'
        % (len(d) - d['_k'].nunique(), len(e) - e['_k'].nunique()))

    if len(donly):
        say('  [D-only 样本]')
        for rec in d.loc[d['_k'].isin(donly)].head(2).to_dict('records'):
            say('    %s' % (rec,))
    else:
        say('  [D-only 样本] 无')

    dv = d.loc[cmask]
    ev = e.loc[e['_k'].isin(common)]
    dc = coverage(dv)
    ec = coverage(ev)
    destroyed, lost = [], []
    for c in dv.columns:
        if c == '_k' or c not in ev.columns:
            continue
        a, b = dc[c], ec[c]
        if a >= 0.30 and b < 0.05:
            destroyed.append((c, a, b, int(a * len(dv)), int(b * len(ev))))
        elif a >= 0.30 and b < 0.5 * a:
            lost.append((c, a, b, int(a * len(dv)), int(b * len(ev))))
    say('  [摧毁列 D高覆盖/E近空] %d 个' % len(destroyed))
    for c, a, b, x, y in destroyed:
        say('    %-24s %.3f -> %.3f   (%d -> %d)' % (c, a, b, x, y))
    say('  [覆盖率腰斩列] %d 个' % len(lost))
    for c, a, b, x, y in lost:
        say('    %-24s %.3f -> %.3f   (%d -> %d)' % (c, a, b, x, y))
    del e, d, dv, ev


def main():
    for name, sub, efile, dfile, keys in TABLES:
        probe(name, sub, efile, dfile, keys)
    say('')
    say('NONFIN_DONE')


if __name__ == '__main__':
    main()
