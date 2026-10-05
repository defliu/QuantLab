# -*- coding: utf-8 -*-
"""合并方案模拟：内存中跑完候选规则并量化真实影响，不写盘。

依据 _audit_version_match.py 的裁决探针结论，对每张表评估：
  R1 现状（危险）：E 冲突键整行覆盖 D（duplicated keep=last）
        -> 量化两类损失：A) D 非空而 E 空的列被抹成空；B) 双方非空异值被 E 静默改写
  R2 整行覆盖 + update_flag 最大优先
        -> 版本裁决对 fina_indicator/income 基本无效（命中率<2%），抹空问题仍在
  R3 字段级合并：键取并集；共有键 D 非空优先保留，仅当 D 为空时用 E 补
        -> D 非空保留是构造保证（恒等式），真正收益是「E 补上的空值数」

本脚本修正了上一版的关键错误：
  - in_d/in_e mask 方向颠倒导致「E 新增」与「共有」重复计数
  - 结果行数错误地按 d_keep + e_new + common 累加
现改为：total = len(D) + E 独有唯一键数，且对完全一致表设硬 sanity 断言。

输出：每表 D/E 规模、共有键、D 独有、E 独有、R1 抹空数、R1 静默改写数、
      R3 补空数、残留冲突列次 Top5。
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
OTHER = [
    ('lhb/top_list', ['trade_date', 'ts_code'], r'lhb\top_list.parquet', 'top_list.parquet'),
    ('research/report_rc_daily',
     ['ts_code', 'report_date', 'org_name', 'author_name', 'report_title', 'quarter'],
     r'research\report_rc_daily.parquet', 'report_rc_daily.parquet'),
    ('board/ths_daily', ['trade_date', 'ts_code'], r'board\ths_daily.parquet', 'ths_daily.parquet'),
    ('board_fundflow/moneyflow_ind_ths', ['trade_date', 'ts_code'],
     r'board_fundflow\moneyflow_ind_ths.parquet', 'moneyflow_ind_ths.parquet'),
    ('basic/stock_basic', ['ts_code'], r'basic\stock_basic.parquet', 'stock_basic_data.parquet'),
]

# 完全一致 / 纯新增表的期望结果行数（由冲突矩阵交叉确认），用于抓计数错误
SANITY = {
    'finance/share_change_event': 53892,
    'lhb/top_list': 230099,
    'board/ths_daily': 3002574 + 15595,
    'board_fundflow/moneyflow_ind_ths': 43560 + 810,
    'basic/stock_basic': 5904 + 9,
}

DATE_COLS = ('ann_date', 'f_ann_date', 'end_date', 'trade_date',
             'report_date', 'list_date', 'change_date')


def isna_scalar(v):
    try:
        return bool(pd.isna(v))
    except Exception:
        return False


def key_strings(df, keys):
    out = df[keys[0]].map(lambda v: NA if isna_scalar(v) else str(v).strip())
    for k in keys[1:]:
        out = out + SEP + df[k].map(lambda v: NA if isna_scalar(v) else str(v).strip())
    return out.to_numpy()


def as_flag_int(series):
    vals = []
    for v in series:
        try:
            t = str(v).strip()
            vals.append(-1 if t.lower() in ('nan', 'none', 'null', '') else int(round(float(t))))
        except Exception:
            vals.append(-1)
    return np.asarray(vals, dtype='int64')


def unequal_mask(dv, ev):
    """按列比较两个等长数组：优先数值 isclose，退化为字符串比较。"""
    try:
        af = pd.to_numeric(pd.Series(dv), errors='raise').to_numpy('float64')
        bf = pd.to_numeric(pd.Series(ev), errors='raise').to_numpy('float64')
        return ~np.isclose(af, bf, rtol=1e-9, atol=1e-9, equal_nan=True)
    except Exception:
        return pd.Series(dv).astype(str).to_numpy() != pd.Series(ev).astype(str).to_numpy()


def simulate(name, keys, d_rel, e_fname):
    d_path = os.path.join(D_BASE, d_rel)
    e_path = os.path.join(E_ROOT, e_fname)
    print('=' * 112)
    print('表：%s' % name)
    print('主键：%s' % ' + '.join(keys))
    if not (os.path.isfile(d_path) and os.path.isfile(e_path)):
        print('  !! 文件缺失 D=%s E=%s' % (os.path.isfile(d_path), os.path.isfile(e_path)))
        raise RuntimeError('文件缺失 %s' % name)

    d = pd.read_parquet(d_path)
    e = pd.read_parquet(e_path)

    for nm, df in (('D', d), ('E', e)):
        miss = [k for k in keys if k not in df.columns]
        if miss:
            df.reset_index(inplace=True)
            print('  .. %s 含 index，已 reset_index' % nm)

    cols = list(d.columns)
    miss_e = [c for c in cols if c not in e.columns]
    if miss_e:
        raise RuntimeError('E 缺少 D 的列：%s' % miss_e[:5])

    dk = key_strings(d, keys)
    ek = key_strings(e, keys)

    d_lookup = {}
    for i, k in enumerate(dk):
        if k not in d_lookup:
            d_lookup[k] = i
    n_d_redundant = len(dk) - len(d_lookup)

    e_groups = {}
    e_order = []
    for i, k in enumerate(ek):
        g = e_groups.get(k)
        if g is None:
            e_groups[k] = [i]
            e_order.append(k)
        else:
            g.append(i)
    n_e_redundant = len(ek) - len(e_order)
    if n_d_redundant:
        print('  !! D 自身存在 %d 个冗余行（主键不唯一），R3 前必须先定 D 去重规则' % n_d_redundant)

    e_set = set(e_order)
    # 掩码方向：d_common = D 行落在 E 唯一键集合内
    d_common_mask = np.fromiter((k in e_set for k in dk), dtype=bool, count=len(dk))
    d_common_pos = np.flatnonzero(d_common_mask)
    n_common = int(d_common_pos.size)
    n_d_only = int(len(d) - n_common)
    e_new_keys = [k for k in e_order if k not in d_lookup]
    n_e_new = len(e_new_keys)

    total_rows = len(d) + n_e_new
    print('  规模：D=%d（唯一键=%d，冗余=%d）  E=%d（唯一键=%d，冗余=%d）'
          % (len(d), len(d_lookup), n_d_redundant, len(e), len(e_order), n_e_redundant))
    print('  键集合：共有=%d   D独有=%d   E独有(新增)=%d' % (n_common, n_d_only, n_e_new))
    print('  R3 结果行数=%d   (= D %d + E新增 %d；冲突键不增行)' % (total_rows, len(d), n_e_new))

    if n_common == 0:
        print('  R1 抹空=0  R1 静默改写=0  R3 由E补空=0  （无共有键，合并退化为纯追加）')
    else:
        # E 侧版本选取：update_flag 最大优先，否则物理末行（仅用于量化对比）
        has_flag = 'update_flag' in cols
        eflag = as_flag_int(e['update_flag']) if has_flag else None
        if eflag is not None:
            e_pick_pos = np.fromiter(
                (max(e_groups[dk[i]], key=lambda p: eflag[p]) for i in d_common_pos),
                dtype='int64', count=n_common)
        else:
            e_pick_pos = np.fromiter(
                (e_groups[dk[i]][-1] for i in d_common_pos),
                dtype='int64', count=n_common)

        payload = [c for c in cols if c not in keys]
        r1_wiped = 0        # D 非空 / E 空 -> R1 会抹掉
        r1_overwrite = 0    # 双方非空异值 -> R1 会静默改写
        r3_filled = 0       # D 空 / E 非空 -> R3 的实际收益
        col_conflict = {}

        for c in payload:
            dcol = d[c].to_numpy()
            ecol = e[c].to_numpy()
            dv = dcol[d_common_pos]
            ev = ecol[e_pick_pos]
            dna = pd.isna(dv)
            ena = pd.isna(ev)
            wipe = ~dna & ena
            fill = dna & ~ena
            r1_wiped += int(wipe.sum())
            r3_filled += int(fill.sum())
            both = ~dna & ~ena
            if both.any():
                neq = unequal_mask(dv[both], ev[both])
                k = int(neq.sum())
                if k:
                    r1_overwrite += k
                    col_conflict[c] = k

        d_nonempty_total = int(sum(
            int((~pd.isna(d[c].to_numpy())).sum()) for c in payload))
        print('  R1 抹空风险：D非空而E空 = %d 个字段值会被覆盖成空' % r1_wiped)
        print('  R1 改写风险：双方非空异值 = %d 个字段值被E静默改写' % r1_overwrite)
        print('  R3 实际收益：E 补上的空值 = %d' % r3_filled)
        print('  R3 D非空保留 = %d / %d（构造保证 100%%，恒等式非实测）'
              % (d_nonempty_total, d_nonempty_total))
        if col_conflict:
            top = sorted(col_conflict.items(), key=lambda kv: -kv[1])[:5]
            print('  残留冲突列 Top5（R3 保留 D 值，需人工裁决）：')
            for c, n in top:
                print('      %-28s %d' % (c, n))
        else:
            print('  残留冲突：无（共有键载荷完全一致，可安全 D 优先）')

    if e_new_keys:
        new_pos = [e_groups[k][-1] for k in e_new_keys]
        sub = e.iloc[new_pos]
        for c in [x for x in DATE_COLS if x in sub.columns][:2]:
            try:
                print('     E新增键 %s 范围: %s ~ %s' % (c, sub[c].min(), sub[c].max()))
            except Exception:
                pass

    expect = SANITY.get(name)
    if expect is not None:
        ok = (total_rows == expect)
        print('  SANITY %s：期望 %d，实际 %d' % ('PASS' if ok else 'FAIL', expect, total_rows))
        if not ok:
            raise RuntimeError('SANITY FAIL %s 期望%d 实际%d' % (name, expect, total_rows))

    del d, e


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    failed = []
    jobs = [('finance/' + t, FIN_KEYS[t], os.path.join('finance', t + '.parquet'), t + '.parquet')
            for t in FIN_TABLES]
    jobs.extend(OTHER)
    for name, keys, d_rel, e_fname in jobs:
        short = name.split('/')[-1]
        if only and short not in only and name not in only and short.replace('.parquet', '') not in only:
            continue
        try:
            simulate(name, keys, d_rel, e_fname)
        except Exception as exc:
            failed.append(short)
            print('  !! 异常 %s: %r' % (short, exc))
            import traceback
            traceback.print_exc()
    if failed:
        print('FAILED=%s' % ','.join(failed))
        sys.exit(1)
    print('DONE')


if __name__ == '__main__':
    main()
