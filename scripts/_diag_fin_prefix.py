# -*- coding: utf-8 -*-
"""_diag_fin_prefix.py

钉死 E 财务表「衍生列只在文件前 ~2578 行有值」的结构，并检查是否跨表普遍。

已确认（本轮）：
  - 列序集合与顺序完全一致 -> 排除列错位
  - 单 row group -> 排除分段写入
  - fina_indicator 坏列非空值 100% 落在物理行 [0..2578]

本脚本回答：
  P1. 边界是否精确（最大非空行号 / 第2579行起是否真的全空）
  P2. 前 2578 行是什么（ts_code 集合 / end_date 跨度 / 是否等于 D 的对应行）
  P3. 跨表普遍性：8 张财务表逐表算「坏列非空值的最大物理行号」
  P4. 好列是否也有同样的前缀边界（若好列不遵守 -> 前缀是"字段级"而非"行级"）
  P5. 若前 2578 行 == D 的子集，说明 E 的剩余行是"重抓且字段更少"
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_FIN = r'D:\astock\finance'
E_ROOT = r'E:\temp\行情数据'
RESIDUAL = '__index_level_0__'

TABLES = ['balancesheet', 'cashflow', 'income', 'dividend', 'express',
          'fina_indicator', 'forecast', 'share_change_event']

BAD_PROBE = {
    'fina_indicator': ['roe_avg', 'q_profit_yoy', 'ocf_to_or', 'dtprofit_to_profit',
                      'q_netprofit_margin', 'inv_turn', 'ebitda_to_debt', 'rd_exp'],
    'income': ['total_opcost', 'oth_income', 'credit_impa_loss',
               'asset_disp_income', 'continued_net_profit'],
    'balancesheet': ['lease_liab', 'use_right_assets'],
    'cashflow': ['free_cashflow', 'c_fr_sale_sg', 'n_cashflow_act'],
}

KEY = {'share_change_event': ['ts_code', 'change_date'],
       'dividend': ['ts_code', 'end_date']}
DEFAULT_KEY = ['ts_code', 'end_date']


def hr(t):
    print('')
    print('=' * 92)
    print(t)
    print('=' * 92)


def prep(path):
    df = pd.read_parquet(path)
    if RESIDUAL in df.columns:
        df = df.drop(columns=[RESIDUAL])
    return df


def prefix_profile(ser, n):
    """返回 (非空数, 最大非空行号, 末十分位之后是否全空)。"""
    m = ser.notna().values
    cnt = int(m.sum())
    if not cnt:
        return cnt, -1, True
    pos = np.flatnonzero(m)
    return cnt, int(pos[-1]), bool(pos[-1] < n * 0.01)


def main():
    hr('P3/P4. 跨表逐表：坏列与好列的非空值物理行边界')
    print('（若坏列 max_pos 极小而好列 max_pos≈总行数 -> 前缀型字段缺失）')
    print('')
    print('  %-18s %8s %-24s %-24s' % ('表', '总行数', '坏列 max_pos(列名=pos)', '好列 max_pos'))

    summary = {}
    for tab in TABLES:
        epath = os.path.join(E_ROOT, tab + '.parquet')
        if not os.path.exists(epath):
            print('  %-18s 源文件不存在' % tab)
            continue
        e = prep(epath)
        n = len(e)
        bad = [c for c in BAD_PROBE.get(tab, []) if c in e.columns]
        if not bad:
            # 取该表非空率最低的 6 列作为"疑似坏列"
            rate = e.notna().mean().sort_values()
            bad = list(rate.head(6).index)
        good_pool = [c for c in ('total_revenue', 'total_assets', 'n_cashflow_act',
                                 'total_hldr_eqy_exc_min_int', 'eps', 'roe',
                                 'n_income_attr_p', 'total_cur_assets') if c in e.columns]
        bad_bits = []
        for c in bad:
            cnt, mx, _ = prefix_profile(e[c], n)
            bad_bits.append('%s=%d/%d' % (c, mx, cnt))
        good_bits = []
        for c in good_pool[:3]:
            cnt, mx, _ = prefix_profile(e[c], n)
            good_bits.append('%s=%d' % (c, mx))
        print('  %-18s %8d %-24s %-24s'
              % (tab, n, ','.join(bad_bits[:4]), ','.join(good_bits)))
        summary[tab] = n

    hr('P1/P2/P5. fina_indicator 前缀结构精确定位')
    e = prep(os.path.join(E_ROOT, 'fina_indicator.parquet'))
    d = prep(os.path.join(D_FIN, 'fina_indicator.parquet'))
    n = len(e)
    probe = 'roe_avg'
    m = e[probe].notna().values
    pos = np.flatnonzero(m)
    print('  [%s] E 非空数=%d  最小行号=%d  最大行号=%d' % (probe, len(pos), pos[0], pos[-1]))
    print('  最大非空行号 / 总行数 = %.6f%%' % (100.0 * pos[-1] / n))
    for boundary in (2578, 2579, 2580, 2600, 3000):
        tail = e.iloc[boundary:]
        print('  行号>=%5d 之后：总行=%7d  %s非空=%6d  好列eps非空=%7d'
              % (boundary, len(tail), probe, int(tail[probe].notna().sum()),
                 int(tail['eps'].notna().sum())))

    print('')
    print('  --- 前 %d 行是什么 ---' % (pos[-1] + 1))
    head = e.iloc[:pos[-1] + 1]
    print('  ts_code 去重数=%d  去重清单(前 20)=%s'
          % (head['ts_code'].nunique(), sorted(head['ts_code'].unique())[:20]))
    print('  end_date 范围=%s ~ %s' % (head['end_date'].astype(str).min(),
                                       head['end_date'].astype(str).max()))
    print('  ts_code 分布是否高度集中: %s'
          % dict(head['ts_code'].value_counts().head(6)))
    tail = e.iloc[pos[-1] + 1:]
    print('  后 %d 行 ts_code 去重数=%d  end_date 范围=%s ~ %s'
          % (len(tail), tail['ts_code'].nunique(),
             tail['end_date'].astype(str).min(), tail['end_date'].astype(str).max()))

    print('')
    print('  --- 前缀行是否与 D 的对应行完全相同（同一 ts_code+end_date）---')
    d['end_date'] = d['end_date'].astype(str).str.replace('-', '', regex=False)
    d['end_date'] = d['end_date'].str.replace(r'\s.*$', '', regex=True)
    e['end_date'] = e['end_date'].astype(str).str.replace('-', '', regex=False)
    lk = ['ts_code', 'end_date']
    h2 = head.drop_duplicates(subset=lk).merge(
        d.drop_duplicates(subset=lk), on=lk, how='left', suffixes=('_e', '_d'),
        indicator=True)
    print('  前缀去重键=%d  在 D 中命中=%d  D独有=%d'
          % (len(h2), int((h2['_merge'] == 'both').sum()),
             int((h2['_merge'] == 'left_only').sum())))
    if (h2['_merge'] == 'both').any():
        cmp_cols = [c for c in ('eps', 'roe', 'gross_margin', 'netprofit_margin', 'bps')
                    if (c + '_e') in h2.columns and (c + '_d') in h2.columns]
        both = h2[h2['_merge'] == 'both']
        for c in cmp_cols:
            a = pd.to_numeric(both[c + '_e'], errors='coerce')
            b = pd.to_numeric(both[c + '_d'], errors='coerce')
            same = int((a.fillna(-9e18) == b.fillna(-9e18)).sum())
            print('    %-20s 双边有值且相等=%5d / 双边有值=%5d'
                  % (c, same, int((a.notna() & b.notna()).sum())))

    hr('结论用数据：D 独有逻辑键在 E 中是否存在（不可逆删除风险）')
    dk = set(map(tuple, d[lk].drop_duplicates().values))
    ek = set(map(tuple, e[lk].drop_duplicates().values))
    print('  D 逻辑键=%d  E 逻辑键=%d' % (len(dk), len(ek)))
    print('  D 有 E 无（严格替换后会消失）=%d 个报告期' % len(dk - ek))

    print('')
    print('PREFIX_DIAG_DONE')


if __name__ == '__main__':
    main()
