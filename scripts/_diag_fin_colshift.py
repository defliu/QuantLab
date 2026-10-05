# -*- coding: utf-8 -*-
"""_diag_fin_colshift.py

诊断：E 财务表的「大面积空值」是真实业务缺失，还是**列错位/读取契约错误**造成的假象。

怀疑点：
  E 的物理 schema 里带一列 `__index_level_0__`（pandas RangeIndex 的物理残留）。
  若写入时 index 未 drop，且后续 read_parquet 的 pandas metadata 把这列还原成
  RangeIndex / 错位列，则所有值列可能整体右移或错配，
  表现为「某些列非空率暴跌、另一些列虚高」。

判定方法（全部只读）：
  T1. 物理列序 vs pandas 列序：确认是否一一对应（错位会直接暴露）。
  T2. Row group 0 原始 Arrow 列名 vs to_pandas() 列名：确认 metadata 还原是否错位。
  T3. 取 E 单行，与 D 同键行逐列比对「数值是否对得上列名」。
      用语义自明的列做锚（total_assets 应 >> total_liab；n_cashflow_act 符号特征）。
  T4. 非空率矩阵：列出所有列的 E 非空率，排序找出断崖式下跌的列簇。
      若坏列集中在物理列序的某一段 -> 指向局部损坏；
      若坏列分散但有共同前缀命名（如 q_* / roe_avg）-> 指向业务/版本差异。
  T5. update_flag / f_ann_date 在坏列非空行上的取值分布。

输出：_diag_fin_colshift.out.txt
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
TAB = 'fina_indicator'

RESIDUAL = '__index_level_0__'


def hr(t):
    print('')
    print('=' * 92)
    print(t)
    print('=' * 92)


def phys(path):
    return list(pq.read_schema(path).names)


def main():
    dpath = os.path.join(D_FIN, TAB + '.parquet')
    epath = os.path.join(E_ROOT, TAB + '.parquet')

    hr('T1/T2. 物理列序 vs pandas 列序（错位检测）')
    ps_d, ps_e = phys(dpath), phys(epath)
    print('D 物理列数=%d  E 物理列数=%d' % (len(ps_d), len(ps_e)))
    print('')
    print('E 物理列序（前 12）: %s' % ps_e[:12])
    print('E 物理列序（末 12）: %s' % ps_e[-12:])
    print('E 中 __index_level_0__ 位置: %s'
          % (ps_e.index(RESIDUAL) if RESIDUAL in ps_e else '不存在'))

    e0 = pq.ParquetFile(epath).read_row_group(0).to_pandas()
    d0 = pq.ParquetFile(dpath).read_row_group(0).to_pandas()
    print('')
    print('E rg0 -> index.names=%s  pandas列数=%d' % (list(e0.index.names), len(e0.columns)))
    print('D rg0 -> index.names=%s  pandas列数=%d' % (list(d0.index.names), len(d0.columns)))
    print('E rg0 pandas列序（前 12）: %s' % list(e0.columns[:12]))
    print('')
    # 物理列去掉残留列后，pandas 列是否完全一致
    ps_e_biz = [c for c in ps_e if c != RESIDUAL]
    pan_e = list(e0.columns)
    print('E: 物理(去残留)=%d  pandas=%d  集合相同=%s  顺序相同=%s'
          % (len(ps_e_biz), len(pan_e),
             set(ps_e_biz) == set(pan_e), ps_e_biz == pan_e))

    hr('T3. 语义锚点比对（total_assets vs total_liab，识别整体错位）')

    def prep(path):
        df = pd.read_parquet(path)
        if RESIDUAL in df.columns:
            df = df.drop(columns=[RESIDUAL])
        for c in ('end_date', 'ann_date', 'f_ann_date'):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors='coerce').dt.strftime('%Y%m%d')
        if 'end_date' in df.columns:
            df = df[df['end_date'].astype(str).str.startswith(('20231231', '20230930', '20230630'))]
        return df

    d3, e3 = prep(dpath), prep(epath)
    print('D 锚定期数=%d  E 锚定期数=%d' % (len(d3), len(e3)))
    print('')
    anchors = ['total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int',
               'total_cur_assets', 'total_cur_liab', 'money_cap', 'inventories',
               'fixed_assets', 'intan_assets', 'goodwill']
    for c in anchors:
        if c not in d3.columns or c not in e3.columns:
            print('  %-32s 列缺失' % c)
            continue
        dn = float(pd.to_numeric(d3[c], errors='coerce').abs().mean())
        en = float(pd.to_numeric(e3[c], errors='coerce').abs().mean())
        flag = '   <== 量级异常' if (dn > 0 and (en / dn > 5 or en / dn < 0.2)) else ''
        print('  %-32s D均值(绝对)=%18.2f  E均值=%18.2f  比值=%8.4f%s'
              % (c, dn, en, (en / dn if dn else float('nan')), flag))

    hr('T4. 全列非空率排序（找断崖簇，判断坏列是否物理相邻）')
    e_all = pd.read_parquet(epath)
    if RESIDUAL in e_all.columns:
        e_all = e_all.drop(columns=[RESIDUAL])
    n = len(e_all)
    nn = e_all.notna().sum()
    rate = (nn / float(n)).sort_values()
    print('E 总行数=%d  列数=%d' % (n, len(nn)))
    print('')
    print('--- 非空率最低 40 列 ---')
    for c, r in rate.head(40).items():
        pos = ps_e_biz.index(c) if c in ps_e_biz else -1
        print('  %-30s 非空率=%8.4f  非空=%9d  物理列位置=%4d'
              % (c, r, int(nn[c]), pos))
    print('')
    print('--- 非空率最高 12 列（看是否有虚高/常数列） ---')
    for c, r in rate.tail(12).items():
        pos = ps_e_biz.index(c) if c in ps_e_biz else -1
        print('  %-30s 非空率=%8.4f  非空=%9d  物理列位置=%4d'
              % (c, r, int(nn[c]), pos))

    hr('T5. 坏列非空行的 update_flag / f_ann_date 分布')
    bad = ['roe_avg', 'q_profit_yoy', 'ocf_to_or', 'dtprofit_to_profit']
    for c in bad:
        if c not in e_all.columns:
            continue
        sub = e_all[e_all[c].notna()]
        print('')
        print('  [%s] E 非空行数=%d' % (c, len(sub)))
        for k in ('update_flag', 'ann_date', 'f_ann_date', 'end_date'):
            if k in sub.columns:
                vc = sub[k].value_counts(dropna=False).head(5)
                print('     %-12s top: %s' % (k, dict(vc)))

    hr('T5b. D 中同列在同样 end_date 上的非空率（对照：是否 D 才有）')
    d_all = pd.read_parquet(dpath)
    if RESIDUAL in d_all.columns:
        d_all = d_all.drop(columns=[RESIDUAL])
    for c in bad:
        if c not in d_all.columns:
            continue
        both = d_all[d_all['end_date'].astype(str).str[:4].isin(
            e_all[e_all[c].notna()]['end_date'].astype(str).str[:4].unique())]
        print('  %-30s D全表非空率=%8.4f' % (c, d_all[c].notna().mean()))

    print('')
    print('COLSHIFT_DIAG_DONE')


if __name__ == '__main__':
    main()
