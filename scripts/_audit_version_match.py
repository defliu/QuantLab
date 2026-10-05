# -*- coding: utf-8 -*-
"""决定性裁决探针：D 的某一行到底等于 E 同键多版本中的哪一个？（只读）

要回答的问题：
  对每个共有键，E 可能有多个版本（主要差在 update_flag）。D 只有一行。
  D 这一行是否精确等于 E 的：
      (a) keep='first'  版本？
      (b) keep='last'   版本？
      (c) update_flag 最大的版本？
      (d) update_flag 最小的版本？
  如果 (c) 命中率接近 100%，说明 D 就是「E 取 update_flag 最大」的结果，
  合并规则就应当是 update_flag 最大优先，而不是 keep='last'（依赖物理行序）。
  如果四种都命中很低，说明 D 与 E 是两套独立加工链，字段级合并是唯一安全解。

只读，不写任何数据文件。
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
SAMPLE_PER_TABLE = 3000


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


def norm_flag(series):
    def one(v):
        if v is None:
            return -1
        if isinstance(v, float) and np.isnan(v):
            return -1
        t = str(v).strip()
        if t == '' or t.lower() in ('nan', 'none', 'null'):
            return -1
        try:
            return int(round(float(t)))
        except Exception:
            return -1
    return series.map(one).astype('int8')


def rows_equal(drow, erow, cols):
    """逐列比较两行（均为 1-D object ndarray），NaN==NaN 视为相等。"""
    diff = []
    for j, c in enumerate(cols):
        a, b = drow[j], erow[j]
        a_na, b_na = isna_scalar(a), isna_scalar(b)
        if a_na and b_na:
            continue
        if a_na != b_na:
            diff.append(c)
            continue
        try:
            same = bool(np.isclose(float(a), float(b), rtol=1e-9, atol=1e-9))
        except Exception:
            same = str(a) == str(b)
        if not same:
            diff.append(c)
    return diff


def audit(name, keys, d_rel, e_fname):
    d_path = os.path.join(D_BASE, d_rel)
    e_path = os.path.join(E_ROOT, e_fname)
    print('=' * 108)
    print('表：%s' % name)
    if not (os.path.isfile(d_path) and os.path.isfile(e_path)):
        print('  !! 文件缺失')
        return
    d = pd.read_parquet(d_path)
    e = pd.read_parquet(e_path)
    for df in (d, e):
        miss = [k for k in keys if k not in df.columns]
        if miss:
            df.reset_index(inplace=True)

    dk = key_strings(d, keys).to_numpy()
    ek = key_strings(e, keys).to_numpy()

    d_lookup = {}
    for i, k in enumerate(dk):
        if k not in d_lookup:
            d_lookup[k] = i

    e_groups = {}
    order = []
    for i, k in enumerate(ek):
        if k not in e_groups:
            e_groups[k] = []
            order.append(k)
        e_groups[k].append(i)

    multi = [k for k in order if len(e_groups[k]) > 1]
    single = [k for k in order if len(e_groups[k]) == 1]
    common = [k for k in order if k in d_lookup]
    common_multi = [k for k in common if len(e_groups[k]) > 1]
    print('  E唯一键=%-8d 其中多版本键=%-7d | 共有键=%-7d 其中共有且多版本=%d'
          % (len(order), len(multi), len(common), len(common_multi)))
    if not common:
        return

    if 'update_flag' in e.columns:
        eflag = norm_flag(e['update_flag'])
    else:
        eflag = pd.Series(np.zeros(len(e), dtype='int8'), index=e.index)

    payload = [c for c in d.columns if c in e.columns and c not in keys]
    has_flag = 'update_flag' in payload
    cmp_cols = [c for c in payload if c != 'update_flag']

    rng = np.random.default_rng(20261001)
    if common_multi:
        pool = common_multi
    else:
        pool = common
    n = min(SAMPLE_PER_TABLE, len(pool))
    pick = rng.choice(np.asarray(pool, dtype=object), size=n, replace=False) if len(pool) > n else np.asarray(pool, dtype=object)

    dmat = d[cmp_cols].to_numpy(dtype=object)
    emat = e[cmp_cols].to_numpy(dtype=object)

    hit = {'first': 0, 'last': 0, 'flagmax': 0, 'flagmin': 0, 'single': 0}
    near = {'first': 0, 'last': 0, 'flagmax': 0, 'flagmin': 0}
    examined = 0
    diff_hist = {}
    for k in pick:
        drow = dmat[d_lookup[k]]
        poss = e_groups[k]
        flg = eflag.to_numpy()[poss]
        examined += 1
        cands = {
            'first': poss[0],
            'last': poss[-1],
        }
        if has_flag:
            cands['flagmax'] = poss[int(np.argmax(flg))]
            cands['flagmin'] = poss[int(np.argmin(flg))]
        elif len(poss) == 1:
            cands['flagmax'] = poss[0]
            cands['flagmin'] = poss[0]
        if len(poss) == 1:
            hit['single'] += 1
        for tag, p in cands.items():
            dcols = rows_equal(drow, emat[p], cmp_cols)
            if not dcols:
                hit[tag] += 1
            elif len(dcols) <= 3:
                near[tag] += 1
                for c in dcols:
                    diff_hist[c] = diff_hist.get(c, 0) + 1

    print('  抽样共有键 %d 个，E 多版本键占 %d/%d' % (examined, len(common_multi), len(common)))
    for tag in ('first', 'last', 'flagmax', 'flagmin'):
        print('    D == E[%-8s] : 精确命中 %-6d (%.1f%%) | 差<=3列 %-5d'
              % (tag, hit[tag], 100.0 * hit[tag] / max(examined, 1), near[tag]))
    if diff_hist:
        top = sorted(diff_hist.items(), key=lambda x: -x[1])[:12]
        print('    差<=3列时的差异字段 top12: %s' % (top,))
    del d, e


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    failed = []
    for t in FIN_TABLES:
        if only and t not in only:
            continue
        try:
            audit('finance/' + t, FIN_KEYS[t], os.path.join('finance', t + '.parquet'), t + '.parquet')
        except Exception as exc:
            failed.append(t)
            print('  !! 异常 %s: %r' % (t, exc))
            import traceback
            traceback.print_exc()
    if failed:
        print('FAILED=%s' % ','.join(failed))
        sys.exit(1)
    print('DONE')


if __name__ == '__main__':
    main()
