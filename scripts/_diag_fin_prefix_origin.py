# coding=utf-8
"""诊断 E 财务表"前缀型字段缺失"的根因。

待解问题：
  fina_indicator 的 roe_avg / q_* / ocf_to_* 等字段仅在物理行 0..2578 有值，
  2579 之后全空。究竟是：
    (a) 源文件由两个来源拼接（前 2578 行来自旧库/小表，后面接新全量）；
    (b) 该批字段在抓取时整体丢失（列存在但未回填）；
    (c) 分段抓取时首批请求带了这些字段、后续请求未带。

同时排查 E 盘目录内是否存在可用的完整副本。
"""
import os
import sys

import numpy as np
import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

RESIDUAL = '__index_level_0__'

BAD = {
    'fina_indicator': ['roe_avg', 'q_profit_yoy', 'q_profit_qoq', 'dtprofit_to_profit',
                       'ocf_to_profit', 'ocf_to_or', 'q_gsprofit_margin',
                       'q_netprofit_margin', 'q_sales_qoq', 'q_netprofit_qoq',
                       'q_dtprofit_to_profit', 'q_op_yoy', 'q_gr_yoy',
                       'q_netprofit_yoy'],
    'income': ['total_opcost', 'oth_income', 'credit_impa_loss',
               'asset_disp_income', 'continued_net_profit'],
    'balancesheet': ['lease_liab', 'use_right_assets'],
}


def dump(title):
    print('')
    print('=' * 78)
    print(title)
    print('=' * 78)


def canon(s):
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d')
    return s.astype(str).str.replace(r'\.0$', '', regex=True)


def load(path):
    df = pd.read_parquet(path)
    if RESIDUAL in df.columns:
        df = df.drop(columns=[RESIDUAL])
    return df.reset_index()


def rowprofile(name, ser):
    pos = np.flatnonzero(ser.notna().values)
    if pos.size == 0:
        return '%s: 全空' % name
    segs = np.split(pos, np.flatnonzero(np.diff(pos) > 1) + 1)
    longest = max(len(s) for s in segs)
    return ('%s: 非空=%d 段数=%d 最长段=%d 跨度=[%d..%d]'
            % (name, pos.size, len(segs), longest, pos.min(), pos.max()))


def main():
    dump('R1. E:\\temp\\行情数据 目录全量文件清单（找完整副本）')
    n = 0
    for dirpath, dirnames, filenames in os.walk(E_ROOT):
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(full)
            except OSError:
                sz = -1
            rel = os.path.relpath(full, E_ROOT)
            print('  %-64s %12.2f MB' % (rel, sz / 1048576.0))
            n += 1
    print('  文件总数=%d' % n)

    for tab, badcols in BAD.items():
        ep = os.path.join(E_ROOT, tab + '.parquet')
        dp = os.path.join(D_FIN, tab + '.parquet')
        if not os.path.exists(ep):
            print('')
            print('[SKIP] E 缺少 %s' % tab)
            continue

        e = load(ep)
        d = load(dp)

        dump('R2. %s  前缀边界解剖' % tab)
        print('  E 行数=%d   D 行数=%d' % (len(e), len(d)))

        # 全字段非空率的"断崖位置"：逐行计算非空字段数，找拐点
        cnt = e[list(badcols)].notna().sum(axis=1)
        anybad = cnt > 0
        if anybad.any():
            last = int(np.flatnonzero(anybad.values)[-1])
            print('  坏字段最后一个有值物理行 = %d' % last)
            print('  行区间 [0,%d] 坏字段非空行数=%d' % (last, int(anybad.values[:last + 1].sum())))
            print('  行区间 [%d,%d] 坏字段非空行数=%d'
                  % (last + 1, len(e) - 1, int(anybad.values[last + 1:].sum())))
        else:
            last = -1
            print('  坏字段全空')

        # 拐点前后，键的分布是否不同（判断是否拼接）
        for label, sub in (('前缀[0..%d]' % last, e.iloc[:last + 1]),
                           ('尾部[%d..end]' % (last + 1), e.iloc[last + 1:])):
            if len(sub) == 0:
                continue
            codes = sub['ts_code'].astype(str)
            print('  %-16s 行数=%-8d ts_code去重=%-6d 首=%s 末=%s'
                  % (label, len(sub), codes.nunique(), codes.iloc[0], codes.iloc[-1]))
            for c in ('end_date', 'ann_date', 'f_ann_date', 'update_flag'):
                if c in sub.columns:
                    v = canon(sub[c]) if 'date' in c else sub[c]
                    print('      %-12s 唯一值数=%-6d 范围=[%s .. %s]'
                          % (c, v.nunique(), v.min(), v.max()))

        dump('R3. %s  坏字段行跨度' % tab)
        for c in badcols:
            if c in e.columns:
                print('  E ' + rowprofile(c, e[c]))
            if c in d.columns:
                print('  D ' + rowprofile(c, d[c]))

        # 尾部区域正常字段是否仍饱满（证明不是整段截断）
        dump('R4. %s  尾部区域正常字段非空率对照' % tab)
        tail = e.iloc[max(last + 1, 0):]
        if len(tail):
            good = [c for c in ('ts_code', 'end_date') if c in e.columns]
            good += [c for c in ('eps', 'roe', 'gross_margin', 'netprofit_margin',
                                 'bps', 'total_revenue', 'n_income_attr_p',
                                 'total_assets', 'total_hldr_eqy_exc_min_int',
                                 'n_cashflow_act') if c in e.columns]
            rate = tail[good].notna().mean()
            print('  尾部行数=%d' % len(tail))
            for c, v in rate.items():
                print('      %-32s 非空率=%.4f' % (c, v))

    print('')
    print('PREFIX_ORIGIN_DIAG_DONE')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
