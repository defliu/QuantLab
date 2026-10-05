# coding=utf-8
"""只读模拟：验证「E 为基表 + 并集 D 独有行 + 共同键单元格级回填」能否消除字段损失。

不写任何生产目录。输出三种口径的关键字段非空覆盖率对比：
    A = 严格替换（只写 E）        -> 会丢多少
    B = 并集（D 独有行保留）      -> 行覆盖如何
    C = 并集 + 单元格级回填       -> 字段覆盖能否恢复到 >= max(D, E)
"""
import os
import sys

import pandas as pd

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

RESIDUAL = '__index_level_0__'

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

DATE_COLS = ['end_date', 'ann_date', 'f_ann_date', 'change_date']

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

CRITICAL = {
    'fina_indicator': ['roe_avg', 'q_profit_yoy', 'q_profit_qoq', 'dtprofit_to_profit',
                       'ocf_to_profit', 'ocf_to_or', 'q_gsprofit_margin',
                       'q_netprofit_margin', 'q_sales_qoq', 'q_netprofit_qoq',
                       'q_dtprofit_to_profit', 'q_op_yoy', 'q_gr_yoy',
                       'q_netprofit_yoy', 'eps', 'roe', 'gross_margin',
                       'netprofit_margin', 'bps'],
    'income': ['total_opcost', 'oth_income', 'credit_impa_loss',
               'asset_disp_income', 'continued_net_profit',
               'total_revenue', 'n_income_attr_p', 'operate_profit'],
    'balancesheet': ['lease_liab', 'use_right_assets', 'total_assets', 'total_liab',
                     'money_cap', 'total_hldr_eqy_exc_min_int'],
    'cashflow': ['n_cashflow_act', 'n_cashflow_inv_act', 'n_cash_flows_fnc_act',
                 'free_cashflow', 'c_fr_sale_sg'],
}


def canon(s):
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d')
    return s.astype(str).str.replace(r'\.0$', '', regex=True)


def load(path):
    df = pd.read_parquet(path)
    if RESIDUAL in df.columns:
        df = df.drop(columns=[RESIDUAL])
    df = df.reset_index()
    for c in DATE_COLS:
        if c in df.columns:
            df[c] = canon(df[c])
    return df


def dump(t):
    print('')
    print('=' * 84)
    print(t)
    print('=' * 84)


def main():
    for tab in ('fina_indicator', 'income', 'balancesheet', 'cashflow'):
        ep = os.path.join(E_ROOT, tab + '.parquet')
        dp = os.path.join(D_FIN, tab + '.parquet')
        if not (os.path.exists(ep) and os.path.exists(dp)):
            print('[SKIP] %s' % tab)
            continue

        fk = FULL_KEYS[tab]
        e = load(ep)
        d = load(dp)

        dump('%s  行覆盖与日期跨度' % tab)
        print('  D 行数=%-8d E 行数=%-8d' % (len(d), len(e)))
        for c in ('end_date', 'ann_date'):
            if c in d.columns:
                print('  D %-9s 范围=[%s .. %s]' % (c, d[c].min(), d[c].max()))
            if c in e.columns:
                print('  E %-9s 范围=[%s .. %s]' % (c, e[c].min(), e[c].max()))

        ek = pd.MultiIndex.from_frame(e[fk])
        dk = pd.MultiIndex.from_frame(d[fk])

        d_only = ~dk.isin(ek)
        e_only = ~ek.isin(dk)
        n_common = int(len(ek) - e_only.sum())
        print('  FULL_KEY(%s)' % '+'.join(fk))
        print('    D-only 行数=%-8d  E-only 行数=%-8d  共同键=%-8d'
              % (int(d_only.sum()), int(e_only.sum()), n_common))

        # B/C 口径：以 E 为基表，并入 D 独有行
        d_extra = d[d_only].reset_index(drop=True)
        common = ek.isin(dk)

        cols = [c for c in CRITICAL[tab] if c in e.columns and c in d.columns]
        out = e.copy()

        # 单元格级回填：E 为空且 D 同键有值 -> 取 D 值（回填源必须是完整 D）
        filled = 0
        idx_keys = pd.MultiIndex.from_frame(e.loc[common, fk])
        d_full_idx = d.set_index(fk).reindex(idx_keys)
        for c in cols:
            ev = out.loc[common, c]
            dv = d_full_idx[c].reset_index(drop=True)
            dv.index = ev.index
            mask = ev.isna() & dv.notna()
            filled += int(mask.sum())
            out.loc[common, c] = ev.where(~mask, dv)

        print('')
        print('  共同键单元格级回填: 补回单元格数=%d' % filled)
        print('')
        print('  %-24s %10s %10s %10s %10s  %s'
              % ('字段', 'D非空', 'E非空', 'A替换后', 'C回填后', '判定'))
        for c in cols:
            nd = int(d[c].notna().sum())
            ne = int(e[c].notna().sum())
            na = ne
            nc = int(out[c].notna().sum())
            if nc >= max(nd, ne):
                verdict = 'OK(>=max)'
            elif nc > na:
                verdict = '部分恢复'
            else:
                verdict = '退化'
            print('  %-24s %10d %10d %10d %10d  %s'
                  % (c, nd, ne, na, nc, verdict))

        # ---- C 口径收尾：并入 D 独有行，校验并集结果的行级完整性 ----
        dump('%s  C 口径并集校验（E 基表 + D 独有行 + 单元格回填）' % tab)
        out = pd.concat([out, d_extra], ignore_index=True)
        print('  并集后总行数=%-10d （E %d + D独有 %d）' % (len(out), len(e), len(d_extra)))

        dup_full = int(out.duplicated(subset=fk).sum())
        print('  FULL_KEY 重复行数=%d  %s'
              % (dup_full, 'OK 唯一' if dup_full == 0 else 'WARN 需去重'))

        lk = [c for c in LOGIC_KEYS[tab] if c in out.columns]
        if lk:
            dup_logic = int(out.duplicated(subset=lk).sum())
            print('  LOGIC_KEY(%s) 重复行数=%d -> pick_version 裁决后应剩 %d 行'
                  % ('+'.join(lk), dup_logic, len(out) - dup_logic))

        miss = [c for c in d.columns if c not in out.columns]
        extra_c = [c for c in out.columns if c not in d.columns]
        print('  相对 D 缺列=%s  多列=%s' % (miss if miss else '无',
                                            extra_c if extra_c else '无'))

        # 行数守恒：并集行数 == |E| + |D-only|，且不得少于 D 与 E 任一方
        ok_rows = len(out) == len(e) + int(d_only.sum())
        print('  行数守恒=%s  并集(%d) >= max(D %d, E %d)=%s'
              % ('OK' if ok_rows else 'FAIL', len(out), len(d), len(e),
                 'OK' if len(out) >= max(len(d), len(e)) else 'FAIL'))

    print('')
    print('MERGE_FILL_SIM_DONE')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
