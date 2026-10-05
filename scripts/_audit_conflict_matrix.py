# -*- coding: utf-8 -*-
"""D/E 财务与业务表 字段级冲突矩阵审计（只读，不写任何数据文件）。

背景：update_astock_from_e.py 当前用「E 冲突键整行覆盖 D」+ duplicated(keep='last')
      做合并，两者都有风险：
        (a) keep='last' 依赖 Parquet 物理行序，E 同键多版本可能选错版本；
        (b) 整行覆盖会在 E 该字段为空时把 D 的非空值抹掉（字段级数据丢失）。

本脚本产出裁决所需的全部事实：
  1) 键层面：D 独有 / E 独有 / 共有
  2) 字段层面（仅共有键，按位置对齐）：双方空 / D非E空 / D空E非 / 双方非空同值 / 双方非空异值
  3) E 内部同键多版本的差异字段（判断 keep='last' 到底在选什么）

键统一转字符串拼接，规避 MultiIndex .loc 对 NaN 元组的不稳定选择。
"""
import os
import sys

import numpy as np
import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'
SEP = '\u0001'
NA = '<NA>'

FIN_TABLES = [
    'balancesheet', 'cashflow', 'dividend', 'express',
    'fina_indicator', 'forecast', 'income', 'share_change_event',
]
FIN_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'share_change_event': ['ts_code', 'change_date', 'change_reason'],
}

TABLES = [
    ('finance/' + t, FIN_KEYS[t], os.path.join('finance', t + '.parquet'), t + '.parquet')
    for t in FIN_TABLES
]
TABLES += [
    ('lhb/top_list', ['trade_date', 'ts_code'], os.path.join('lhb', 'top_list.parquet'), 'top_list.parquet'),
    ('research/report_rc_daily',
     ['ts_code', 'report_date', 'org_name', 'author_name', 'report_title'],
     os.path.join('research', 'report_rc_daily.parquet'), 'report_rc_daily.parquet'),
    ('board/ths_daily', ['trade_date', 'ts_code'], os.path.join('board', 'ths_daily.parquet'), 'ths_daily.parquet'),
    ('board_fundflow/moneyflow_ind_ths', ['trade_date', 'ts_code'],
     os.path.join('board_fundflow', 'moneyflow_ind_ths.parquet'), 'moneyflow_ind_ths.parquet'),
    ('basic/stock_basic', ['ts_code'], os.path.join('basic', 'stock_basic.parquet'), 'stock_basic_data.parquet'),
]


def isna_scalar(v):
    try:
        return bool(pd.isna(v))
    except Exception:
        return False


def norm_key_col(s):
    return s.map(lambda v: NA if isna_scalar(v) else str(v).strip())


def key_strings(df, keys):
    out = norm_key_col(df[keys[0]])
    for k in keys[1:]:
        out = out + SEP + norm_key_col(df[k])
    return out


def compare_col(a, b):
    """a/b 为已按位置对齐的 numpy 数组。返回 5 类计数。"""
    a_na = pd.isna(a)
    b_na = pd.isna(b)
    both_na = int((a_na & b_na).sum())
    d_ne = int((~a_na & b_na).sum())
    d_null_e = int((a_na & ~b_na).sum())
    m = ~a_na & ~b_na
    cnt = int(m.sum())
    if cnt == 0:
        return both_na, d_ne, d_null_e, 0, 0
    av = a[m]
    bv = b[m]
    eq = None
    try:
        af = pd.to_numeric(pd.Series(av), errors='coerce').to_numpy(dtype='float64')
        bf = pd.to_numeric(pd.Series(bv), errors='coerce').to_numpy(dtype='float64')
        if not np.isnan(af).any() and not np.isnan(bf).any():
            eq = np.isclose(af, bf, rtol=1e-9, atol=1e-9)
    except Exception:
        eq = None
    if eq is None:
        eq = pd.Series(av).astype(str).to_numpy() == pd.Series(bv).astype(str).to_numpy()
    same = int(eq.sum())
    return both_na, d_ne, d_null_e, same, cnt - same


def ensure_cols(df, keys, label):
    """键若在 index 里则 reset_index，保证 keys 都在 columns。"""
    missing = [k for k in keys if k not in df.columns]
    if missing:
        df = df.reset_index()
        print('    [%s] 键在 index 中，已 reset_index（原 index names=%s）' % (label, list(df.columns[:0])))
    return df


def audit_table(name, keys, d_rel, e_fname):
    d_path = os.path.join(D_BASE, d_rel)
    e_path = os.path.join(E_ROOT, e_fname)
    print('=' * 108)
    print('表：%s   主键：%s' % (name, keys))
    if not (os.path.isfile(d_path) and os.path.isfile(e_path)):
        print('  !! 文件缺失 D=%s(%s) E=%s(%s)' % (d_path, os.path.isfile(d_path), e_path, os.path.isfile(e_path)))
        return
    d = ensure_cols(pd.read_parquet(d_path), keys, 'D')
    e = ensure_cols(pd.read_parquet(e_path), keys, 'E')
    print('  D: rows=%-9d cols=%-4d | E: rows=%-9d cols=%-4d' % (len(d), d.shape[1], len(e), e.shape[1]))

    d_only_cols = [c for c in d.columns if c not in e.columns]
    e_only_cols = [c for c in e.columns if c not in d.columns]
    if d_only_cols:
        print('  D 独有列(%d): %s' % (len(d_only_cols), d_only_cols[:30]))
    if e_only_cols:
        print('  E 独有列(%d): %s' % (len(e_only_cols), e_only_cols[:30]))

    dk = key_strings(d, keys).to_numpy()
    ek = key_strings(e, keys).to_numpy()
    d_uniq = pd.unique(dk)
    e_uniq = pd.unique(ek)
    e_dupmask = pd.Index(ek).duplicated(keep=False)
    n_dup_rows = len(ek) - len(e_uniq)
    n_dup_groups = int(np.unique(ek[e_dupmask]).size) if bool(e_dupmask.any()) else 0
    n_d_rows = len(dk) - len(d_uniq)
    d_lookup = {}
    for i, k in enumerate(dk):
        if k not in d_lookup:
            d_lookup[k] = i
    e_lookup = set(e_uniq.tolist())
    in_d = np.fromiter((k in d_lookup for k in ek), dtype=bool, count=len(ek))
    in_e = np.fromiter((k in e_lookup for k in dk), dtype=bool, count=len(dk))
    print('  键层面: D唯一键=%-8d D冗余行=%-6d | E唯一键=%-8d E冗余行=%-7d(重复组=%d)'
          % (len(d_uniq), n_d_rows, len(e_uniq), n_dup_rows, n_dup_groups))
    print('         共有键(E侧行)=%-8d  E独有键=%-8d  D独有键=%-8d'
          % (int(in_d.sum()), int((~in_d).sum()), int((~in_e).sum())))

    common_cols = [c for c in e.columns if c in d.columns and c not in keys]
    epos = np.flatnonzero(in_d)
    dpos = np.fromiter((d_lookup[ek[i]] for i in epos), dtype='int64', count=len(epos))
    print('  字段级分类（共有键 %d 行 × %d 共有载荷列）：' % (len(epos), len(common_cols)))
    recs = []
    for c in common_cols:
        av = d[c].to_numpy()[dpos]
        bv = e[c].to_numpy()[epos]
        recs.append((c,) + compare_col(av, bv))
    res = pd.DataFrame(recs, columns=['col', 'both_na', 'd_ne', 'd_null_e', 'same', 'diff']).set_index('col')
    res['d_fill'] = res['same'] + res['diff']
    print('    双方都空      = %d 列次' % int(res['both_na'].sum()))
    print('    D非空而E空    = %d 列次   <-- 整行覆盖会丢失的部分' % int(res['d_ne'].sum()))
    print('    D空而E非空    = %d 列次   <-- E 能补齐的部分' % int(res['d_null_e'].sum()))
    print('    双方非空同值  = %d 列次' % int(res['same'].sum()))
    print('    双方非空异值  = %d 列次   <-- 需口径裁决' % int(res['diff'].sum()))
    nz = res[res['d_ne'] > 0].sort_values('d_ne', ascending=False)
    if len(nz):
        print('    -- D非空而E空 top20 --')
        for c, r in nz.head(20).iterrows():
            print('       %-30s D非空=%-7d 其中E空=%-7d | 双方非空: 同值=%-7d 异值=%d'
                  % (c, int(r['d_fill']), int(r['d_ne']), int(r['same']), int(r['diff'])))
    nd = res[res['diff'] > 0].sort_values('diff', ascending=False)
    if len(nd):
        print('    -- 双方非空异值 top15 --')
        for c, r in nd.head(15).iterrows():
            print('       %-30s 异值=%-7d / 双方非空=%-7d (异值率=%.1f%%)'
                  % (c, int(r['diff']), int(r['d_fill']), 100.0 * r['diff'] / max(r['d_fill'], 1)))

    if n_dup_groups:
        stat = {}
        grp_keys = np.unique(ek[e_dupmask])
        sub = e.loc[e_dupmask]
        sub_keys = ek[e_dupmask]
        cnt = 0
        seen = set()
        for i in range(len(sub)):
            kk = sub_keys[i]
            if kk in seen:
                continue
            seen.add(kk)
            cnt += 1
            if cnt > 250:
                break
            pos = np.flatnonzero(sub_keys == kk)
            if len(pos) < 2:
                continue
            a = sub.iloc[pos[0]]
            b = sub.iloc[pos[-1]]
            for c in sub.columns:
                if c in keys:
                    continue
                av, bv = a[c], b[c]
                if isna_scalar(av) and isna_scalar(bv):
                    continue
                same = False
                if not (isna_scalar(av) or isna_scalar(bv)):
                    try:
                        same = bool(np.isclose(float(av), float(bv), rtol=1e-9, atol=1e-9))
                    except Exception:
                        same = str(av) == str(bv)
                if not same:
                    stat[c] = stat.get(c, 0) + 1
        print('  E 内部同键多版本差异（抽样 %d 组，比较首行 vs 末行 = keep="first" vs keep="last"）：' % (cnt - 1))
        if stat:
            for c, n in sorted(stat.items(), key=lambda x: -x[1])[:18]:
                print('     %-30s 版本间有差异 %d 组' % (c, n))
        else:
            print('     抽样组内各载荷列完全一致（重复行仅索引/顺序差异）')
        print('  重复组键样例: %s' % (list(grp_keys[:3]),))
    del d, e
    return


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    failed = []
    for name, keys, d_rel, e_fname in TABLES:
        short = name.split('/')[-1]
        if only and short not in only:
            continue
        try:
            audit_table(name, keys, d_rel, e_fname)
        except Exception as exc:
            failed.append(short)
            print('  !! 审计异常 %s: %r' % (name, exc))
            import traceback
            traceback.print_exc()
    if failed:
        print('FAILED_TABLES=%s' % ','.join(failed))
        sys.exit(1)
    print('DONE')


if __name__ == '__main__':
    main()