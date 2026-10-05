# -*- coding: utf-8 -*-
"""_audit_fin_keys.py

权威财务载荷审计（键级值覆盖口径），用于替代此前不可信的列集合比较。

本脚本给出 4 组数字，全部基于 canonicalize 后的真实键空间：

  A. D-only 键/行   —— 严格整体替换会被删除的 D 独有记录
  B. 共同键上「D 非空 -> E 空」的单元格数量 —— 真正的值级损失
  C. E 多版本情况   —— 同一逻辑键 (ts_code,end_date[,report_type]) 下 E 有几行
  D. E 内部重复键   —— 裁决后是否仍重复

同时校验：PIT 字段是否齐全、__index_level_0__ 等残留索引列。

只读脚本，不写任何文件。
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

TABS = ['balancesheet', 'cashflow', 'income', 'dividend',
        'express', 'fina_indicator', 'forecast', 'share_change_event']

# 逻辑键（用于版本裁决，不含 ann_date/div_proc/type 等流程字段）
LOGIC_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type'],
    'cashflow': ['ts_code', 'end_date', 'report_type'],
    'income': ['ts_code', 'end_date', 'report_type'],
    'dividend': ['ts_code', 'end_date'],
    'express': ['ts_code', 'end_date'],
    'fina_indicator': ['ts_code', 'end_date'],
    'forecast': ['ts_code', 'end_date'],
    'share_change_event': ['ts_code', 'change_date'],
}

FULL_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'share_change_event': ['ts_code', 'change_date', 'change_reason'],
}

DATE_COLS = {
    'balancesheet': ['end_date', 'ann_date', 'f_ann_date'],
    'cashflow': ['end_date', 'ann_date', 'f_ann_date'],
    'income': ['end_date', 'ann_date', 'f_ann_date'],
    'dividend': ['end_date', 'ann_date'],
    'express': ['end_date', 'ann_date'],
    'fina_indicator': ['end_date', 'ann_date'],
    'forecast': ['end_date', 'ann_date'],
    'share_change_event': ['change_date'],
}

RESIDUAL = '__index_level_0__'


def canon(s):
    """任意日期表示 -> YYYYMMDD 字符串，缺失统一 'NA'。"""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d').fillna('NA').astype(str)
    if s.dtype == object:
        t = s.astype(str).str.strip()
        t = t.where(~t.isin(('nan', 'NaT', 'None', '', '--', 'nan ')), 'NA')
    else:
        t = s.astype(str)
    dg = t.str.split(' ').str[0].str.replace(r'\D', '', regex=True)
    out = dg.where(dg.str.len() == 8, 'NA')
    need = (out == 'NA') & (t != 'NA')
    if need.any():
        p = pd.to_datetime(t.where(need), errors='coerce')
        out = out.where(~need, p.dt.strftime('%Y%m%d').fillna('NA'))
    return out.fillna('NA').astype(str)


def prep(df, tab, keyname):
    """标准化：drop 残留索引列 -> 日期列 canonicalize -> 构造键。"""
    if RESIDUAL in df.columns:
        df = df.drop(columns=[RESIDUAL])
    for c in DATE_COLS[tab]:
        if c in df.columns:
            df[c] = canon(df[c])
    keys = FULL_KEYS[tab] if keyname == 'full' else LOGIC_KEYS[tab]
    missing = [k for k in keys if k not in df.columns]
    if missing:
        raise RuntimeError('%s 缺键列: %s' % (tab, missing))
    return df, keys


def audit(tab):
    dp = os.path.join(D_FIN, tab + '.parquet')
    ep = os.path.join(E_ROOT, tab + '.parquet')

    d = pd.read_parquet(dp)
    e = pd.read_parquet(ep)
    d, fk = prep(d, tab, 'full')
    e, _ = prep(e, tab, 'full')
    lk = LOGIC_KEYS[tab]

    # D 侧做裁决，得到「D 的唯一逻辑视图」，再与 E 比 —— 否则 D 自身多版本会虚增 D-only
    d_logic = pick_version(d, lk)
    e_logic = pick_version(e, lk)

    dk = pd.MultiIndex.from_frame(d_logic[lk])
    ek = pd.MultiIndex.from_frame(e_logic[lk])
    d_only_mask = ~dk.isin(ek)
    d_only_keys = int(dk[d_only_mask].nunique())

    common = int(dk.isin(ek).sum())

    # 值级损失：共同逻辑键上，D 非空而 E 空。
    # 用 merge 做键对齐（两侧逻辑键已唯一），避免布尔掩码与行序错位。
    d_common = d_logic.loc[dk.isin(ek)].reset_index(drop=True)
    m = d_common.merge(e_logic.reset_index(drop=True), on=lk,
                       how='inner', suffixes=('_d', '_e'))
    skip = set(lk)
    cell_loss = {}
    for c in d_common.columns:
        if c in skip:
            continue
        cd, ce = c + '_d', c + '_e'
        if cd not in m.columns or ce not in m.columns:
            continue
        if not pd.api.types.is_numeric_dtype(m[cd]):
            continue
        if not pd.api.types.is_numeric_dtype(m[ce]):
            continue
        n = int((m[cd].notna() & m[ce].isna()).sum())
        if n:
            cell_loss[c] = n

    # E 多版本 / 裁决后残留重复
    e_logic_dup = int(len(ek) - len(ek.unique()))
    lk_e_raw = pd.MultiIndex.from_frame(e[lk])
    e_multi = int(len(lk_e_raw) - len(lk_e_raw.unique()))

    pit_missing = [c for c in ('ann_date', 'f_ann_date')
                   if c in d.columns and c not in e.columns]

    return dict(
        tab=tab,
        d_rows=int(len(d)), e_rows=int(len(e)),
        d_logic_rows=int(len(d_logic)), e_logic_rows=int(len(e_logic)),
        d_only_keys=d_only_keys, d_only_rows=int(d_only_mask.sum()),
        common=common,
        e_multi=e_multi, e_logic_dup=e_logic_dup,
        cell_loss=cell_loss,
        pit_missing=pit_missing,
        d_only_cols=sorted(set(d.columns) - set(e.columns)),
        e_only_cols=sorted(set(e.columns) - set(d.columns)),
    )


def pick_version(df, lk):
    """确定性版本裁决：update_flag 大者优先 -> f_ann_date 晚者优先 -> 物理序稳定。"""
    out = df
    if 'update_flag' in out.columns:
        uf = pd.to_numeric(out['update_flag'], errors='coerce').fillna(-1)
    else:
        uf = pd.Series(0.0, index=out.index)
    if 'f_ann_date' in out.columns:
        fa = pd.to_numeric(out['f_ann_date'], errors='coerce').fillna(0)
    else:
        fa = pd.Series(0.0, index=out.index)
    out = out.assign(_uf=uf, _fa=fa)
    out = out.sort_values(['_uf', '_fa'], ascending=[False, False],
                          kind='mergesort')
    return out.drop_duplicates(subset=lk, keep='first').drop(columns=['_uf', '_fa'])


def main():
    print('=' * 90)
    print('财务载荷权威审计（canonical 键空间 + 确定性版本裁决）')
    print('=' * 90)
    tot_donly = 0
    tot_cells = 0
    all_loss_cols = {}
    for tab in TABS:
        try:
            r = audit(tab)
        except Exception as exc:
            print('')
            print('%-20s ERROR %s: %s' % (tab, type(exc).__name__, exc))
            continue
        tot_donly += r['d_only_rows']
        tot_cells += sum(r['cell_loss'].values())
        for c, n in r['cell_loss'].items():
            all_loss_cols[c] = all_loss_cols.get(c, 0) + n

        print('')
        print('--- %s' % tab)
        print('  行数: D=%d  E=%d   裁决后: D=%d  E=%d'
              % (r['d_rows'], r['e_rows'], r['d_logic_rows'], r['e_logic_rows']))
        print('  共同逻辑键=%d   D-only 键=%d 行=%d  (严格替换将删除)'
              % (r['common'], r['d_only_keys'], r['d_only_rows']))
        print('  E 多版本冗余行=%d   裁决后残留重复=%d' % (r['e_multi'], r['e_logic_dup']))
        print('  列集合: D_only=%s  E_only=%s'
              % (r['d_only_cols'] or '无', r['e_only_cols'] or '无'))
        print('  PIT 缺失=%s' % (r['pit_missing'] or '无'))
        if r['cell_loss']:
            print('  值级损失（D非空->E空）共 %d 单元格 / %d 列:'
                  % (sum(r['cell_loss'].values()), len(r['cell_loss'])))
            for c, n in sorted(r['cell_loss'].items(), key=lambda x: -x[1])[:12]:
                print('     %-28s %d' % (c, n))
        else:
            print('  值级损失：无')

    print('')
    print('=' * 90)
    print('合计：D-only 待删除行 = %d' % tot_donly)
    print('合计：值级损失单元格 = %d（涉及 %d 列）' % (tot_cells, len(all_loss_cols)))
    for c, n in sorted(all_loss_cols.items(), key=lambda x: -x[1])[:20]:
        print('   %-28s %d' % (c, n))
    print('')
    print('FINKEY_AUDIT_DONE')


if __name__ == '__main__':
    main()