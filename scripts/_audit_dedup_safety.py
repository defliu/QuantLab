# -*- coding: utf-8 -*-
"""验证「日期规范化后按主键去重」是否安全（向量化版）。

前置发现：归一化后的撞键大部分来自同一业务行被写两遍
（一次 ISO 格式、一次 YYYYMMDD 格式），而非真实业务重复。
本脚本对每张财务表的每个撞键组逐字段比对，分类：
  IDENTICAL  载荷完全相同 -> 纯格式重复，可安全去重
  DIFFERS    载荷存在差异 -> 真实业务重复，必须人工裁决，不可自动去重

实现要点（性能）：
  只在「重复行子集」上做逐字段比较，不对全表建 Python 位置字典，
  避免 17 万行宽表上 O(n) Python 循环（前一版跑 700s+ CPU 未完）。
只读，不写盘。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(
    0,
    os.path.dirname(os.path.abspath(__file__))
)
from _colcmp import (  # noqa: E402
    NA,
    SEP,
    col_diff_flags,
    is_date_col,
    norm_date,
    norm_scalar,
)

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'
MAX_SHOW = 4

FIN_TABLES = [
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]
DATE_LIKE = ('date', 'month')
MAX_SHOW = 4


def key_strings(df, keys, date_cols):
    """把主键各列拼成 NaN 安全的复合键字符串数组。

    日期列走 norm_date（ISO/YYYYMMDD 统一），其余列走 strip 字符串，
    缺失一律用 NA 占位，避免 Python 元组比较与 Pandas NaN 语义不一致。
    """
    out = None
    for k in keys:
        col = df[k].map(norm_date) if k in date_cols else \
            df[k].map(norm_scalar)
        out = col if out is None else out + SEP + col
    return out.to_numpy()


def _diff_matrix(sub_all, payload, date_cols):
    """整块向量化算「每行是否与所在组首行载荷不同」。

    返回 (row_diff, base_of_row, gcode)：
      row_diff     形状 (n_rows, n_payload) 的 bool
      base_of_row  每行所属组的首行在 sub_all 中的下标
      gcode        每行所属组的整数编码（用于按组 any 聚合）

    逐列判定全部委托给 _colcmp.col_diff_flags（该模块有单测覆盖缺失值语义、
    日期归一化、数值兜底），这里只负责组编码与矩阵堆叠。
    """
    gcode, _ = pd.factorize(
        sub_all['__k'],
        sort=False
    )
    n = len(sub_all)
    # factorize 按出现顺序编码，故组 g 的首行就是 gcode 首次等于 g 的那行下标
    first_of_code = np.zeros(
        int(gcode.max()) + 1,
        dtype=np.int64
    )
    change = np.empty(n, dtype=bool)
    change[0] = True
    change[1:] = gcode[1:] != gcode[:-1]
    first_idx = np.flatnonzero(change)
    first_of_code[gcode[first_idx]] = first_idx
    base_of_row = first_of_code[gcode]

    row_diff = np.zeros(
        (n, len(payload)),
        dtype=bool
    )
    for j, c in enumerate(payload):
        row_diff[:, j] = col_diff_flags(
            sub_all[c],
            base_of_row,
            date_cols,
            c
        )
    return row_diff, base_of_row, gcode


def _read(path, keys):
    df = pd.read_parquet(path)
    if any(k not in df.columns for k in keys):
        df = df.reset_index()
    return df


def run(name, keys):
    d = _read(os.path.join(D_BASE, 'finance', name + '.parquet'), keys)
    e = _read(os.path.join(E_ROOT, name + '.parquet'), keys)
    date_cols = set(k for k in keys if is_date_col(k))
    # 载荷列取 D/E 并集：只看 D 侧列会漏掉「E 有、D 没有」的字段差异。
    all_cols = list(dict.fromkeys(list(d.columns) + list(e.columns)))
    payload = [c for c in all_cols if c not in keys]
    # 归一化范围要覆盖「所有日期型列」，不只主键里的：
    # f_ann_date 这类 PIT 字段同样混用 ISO/YYYYMMDD。
    all_date_cols = set(c for c in all_cols if is_date_col(c))
    print('  [归一化日期列] %s' % ', '.join(sorted(all_date_cols)), flush=True)
    only_e = [c for c in e.columns if c not in d.columns]
    only_d = [c for c in d.columns if c not in e.columns]
    if only_e or only_d:
        print('  [列差异] E独有=%s  D独有=%s' % (only_e or '无', only_d or '无'), flush=True)

    print('=' * 110, flush=True)
    print('表：finance/%s  主键：%s' % (name, ' + '.join(keys)), flush=True)
    for tag, df in (('D', d), ('E', e)):
        karr = key_strings(df, keys, date_cols)
        dup_mask = pd.Index(karr).duplicated(keep=False)
        if not dup_mask.any():
            print('  %s：无撞键' % tag, flush=True)
            continue
        sub_all = df.loc[dup_mask].copy()
        sub_all['__k'] = karr[dup_mask]
        # 本侧不存在的载荷列已在表级「列差异」中提示，逐组比较时跳过，
        # 否则整表都会被误判成 DIFFERS。
        side_payload = [c for c in payload if c in df.columns]
        n_groups = len(pd.unique(sub_all['__k'].to_numpy()))
        row_diff, base_of_row, gcode = _diff_matrix(
            sub_all, side_payload, all_date_cols)
        grp_diff = pd.DataFrame(row_diff).groupby(gcode, sort=False).any()
        grp_diff.columns = side_payload
        is_diff = grp_diff.any(axis=1)
        n_diff = int(is_diff.sum())
        n_ident = int(len(is_diff) - n_diff)
        if n_diff:
            col_counts = grp_diff[is_diff].sum().sort_values(ascending=False)
            col_counts = col_counts[col_counts > 0]
            print('  %s：撞键组=%d  载荷完全相同(可安全去重)=%d  载荷有差异(需裁决)=%d'
                  % (tag, n_groups, n_ident, n_diff), flush=True)
            print('       差异集中列：%s'
                  % ', '.join('%s=%d' % (c, int(n)) for c, n in col_counts.head(10).items()),
                  flush=True)
            # 样例：按「首现差异列」聚类，各取 1 组，避免同一列刷屏
            arr = grp_diff.to_numpy()
            has_diff = arr.any(axis=1)
            first_hit = np.where(has_diff, arr.argmax(axis=1), -1)
            seen = set()
            shown = 0
            kk = sub_all['__k'].to_numpy()
            for gi in np.flatnonzero(has_diff):
                hit = side_payload[first_hit[gi]]
                if hit in seen:
                    continue
                seen.add(hit)
                gcode_gi = int(grp_diff.index[gi])
                pos = np.flatnonzero(gcode == gcode_gi)
                print('       样例组 key=%r  首现差异列=%s'
                      % (str(kk[base_of_row[pos[0]]]).replace(SEP, '|'), hit), flush=True)
                for c in [k for k in keys if k in sub_all.columns][:2] + [hit]:
                    print('           %-22s %s'
                          % (c, [str(v)[:30] for v in sub_all[c].to_numpy()[pos]]), flush=True)
                shown += 1
                if shown >= MAX_SHOW:
                    break
        else:
            print('  %s：撞键组=%d  载荷完全相同(可安全去重)=%d  载荷有差异(需裁决)=0'
                  % (tag, n_groups, n_ident), flush=True)
    del d, e


def main():
    only = sys.argv[1:] or None
    failed = []
    for name, keys in FIN_TABLES:
        if only and name not in only:
            continue
        try:
            run(name, keys)
        except Exception as exc:
            failed.append(name)
            print('  !! 异常 %s: %r' % (name, exc), flush=True)
            import traceback
            traceback.print_exc()
    if failed:
        print('FAILED=%s' % ','.join(failed), flush=True)
        sys.exit(1)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
