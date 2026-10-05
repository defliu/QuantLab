# -*- coding: utf-8 -*-
"""_audit_critical_fields.py

下钻：fina_indicator / income 的值级损失是否命中「真实被代码消费」的字段。

背景：权威键级审计显示 fina_indicator 有 1520 万个「D非空->E空」单元格，
但列集合上 D 是 E 的子集（零列损失）。必须区分两种性质：

  类型1  字段级缺失：该列在 E 中整列为空 / 大面积为空 -> 迁移后因子直接失效
  类型2  版本差异：E 该记录本身未披露该字段（正常） -> 可接受

判定方法：对比 D 非空数 vs E 非空数 vs 共同键上的空缺数。
若 E 非空数远小于 D 非空数 -> 类型1（危险）。
若 E 非空数与 D 接近，空缺集中在少数记录 -> 类型2（正常）。

只读脚本。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _audit_fin_keys import LOGIC_KEYS, canon, pick_version  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_FIN = r'D:\astock\finance'
E_ROOT = r'E:\temp\行情数据'

# 已确认被下游真实消费的字段（来自前序代码审计）
CRITICAL = {
    'fina_indicator': ['eps', 'roe', 'roe_avg', 'gross_margin', 'netprofit_margin',
                       'bps', 'q_profit_yoy', 'dtprofit_to_profit', 'ocf_to_profit',
                       'ocf_to_or', 'q_gsprofit_margin', 'q_netprofit_margin',
                       'q_sales_qoq', 'q_netprofit_qoq', 'q_dtprofit_to_profit',
                       'q_profit_qoq', 'q_op_yoy', 'q_gr_yoy', 'q_netprofit_yoy'],
    'income': ['total_revenue', 'n_income', 'n_income_attr_p', 'basic_eps',
               'total_opcost', 'oth_income', 'credit_impa_loss',
               'asset_disp_income', 'continued_net_profit', 'operate_profit'],
    'balancesheet': ['total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int',
                     'money_cap', 'total_cur_assets', 'total_cur_liab',
                     'lease_liab', 'use_right_assets'],
    'cashflow': ['n_cashflow_act', 'n_cashflow_inv_act', 'n_cash_flows_fnc_act',
                 'free_cashflow', 'c_fr_sale_sg'],
}


def load(tab):
    d = pd.read_parquet(os.path.join(D_FIN, tab + '.parquet'))
    e = pd.read_parquet(os.path.join(E_ROOT, tab + '.parquet'))
    if '__index_level_0__' in e.columns:
        e = e.drop(columns=['__index_level_0__'])
    from _audit_fin_keys import DATE_COLS
    for c in DATE_COLS[tab]:
        if c in d.columns:
            d[c] = canon(d[c])
        if c in e.columns:
            e[c] = canon(e[c])
    lk = LOGIC_KEYS[tab]
    return pick_version(d, lk), pick_version(e, lk), lk


def main():
    print('=' * 96)
    print('关键消费字段的值级损失下钻（判定：字段级缺失 vs 版本差异）')
    print('=' * 96)

    for tab, cols in CRITICAL.items():
        try:
            d, e, lk = load(tab)
        except Exception as exc:
            print('\n--- %s  LOAD ERROR %s: %s' % (tab, type(exc).__name__, exc))
            continue

        print('')
        print('--- %s   D裁决后=%d行  E裁决后=%d行' % (tab, len(d), len(e)))
        print('  %-28s %10s %10s %10s %10s  %s'
              % ('字段', 'D非空', 'E非空', '共同空缺', 'E/D覆盖', '判定'))

        m = d.merge(e, on=lk, how='inner', suffixes=('_d', '_e'))
        for c in cols:
            if c not in d.columns or c not in e.columns:
                print('  %-28s %10s' % (c, '列不存在'))
                continue
            cd, ce = c + '_d', c + '_e'
            if cd not in m.columns or ce not in m.columns:
                print('  %-28s %10s' % (c, '合并后缺列'))
                continue
            dn = int(d[c].notna().sum())
            en = int(e[c].notna().sum())
            loss = int((m[cd].notna() & m[ce].isna()).sum())
            ratio = en / float(dn) if dn else float('nan')
            if dn == 0:
                verdict = 'D 本就全空'
            elif en == 0:
                verdict = '!! E 整列为空'
            elif ratio < 0.5:
                verdict = '!! 字段级大面积缺失'
            elif ratio < 0.9:
                verdict = '!  覆盖明显下降'
            elif loss and en >= dn:
                verdict = 'OK 覆盖未降(空缺为版本差异)'
            elif loss:
                verdict = '~ 轻微空缺'
            else:
                verdict = 'OK 无损失'
            print('  %-28s %10d %10d %10d %10.4f  %s'
                  % (c, dn, en, loss, ratio, verdict))

    print('')
    print('CRITICAL_DONE')


if __name__ == '__main__':
    main()