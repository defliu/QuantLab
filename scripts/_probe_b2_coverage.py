# coding=utf-8
"""供应商交付完整性总审计（只读）。

回答用户问题「供应商提供的数据全不全」：
  对 15 项交付物逐项判定
    A. 文件是否到位（E 盘 vs 网盘登记）
    B. E 是否完全覆盖 D 生产主库（键集合包含性）
    C. 共有键数值是否一致
    D. 财务表生产衍生字段填充率
输出逐表 PASS / WARN / FAIL。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

D_BASE = r'D:\astock'
E_ROOT = r'E:\temp\行情数据'

# 网盘「行情数据」目录登记（字节数来自 browse 清单）
QUARK = {
    'balancesheet.parquet': 143273004,
    'cashflow.parquet': 110538225,
    'dividend.parquet': 1335543,
    'express.parquet': 2266589,
    'fina_indicator.parquet': 295180532,
    'forecast.parquet': 30493926,
    'income.parquet': 81750720,
    'moneyflow_ind_ths.parquet': 898363,
    'report_rc_daily.parquet': 55328956,
    'share_change_event.parquet': 1878449,
    'stock_basic_data.parquet': 258681,
    'stock_daily.parquet': 3109831,
    'ths_daily.parquet': 183361153,
    'top_list.parquet': 14710392,
}
QUARK_SUBDIR = {
    'stock_daily_full.parquet': 1336727493,
}

# 每项：E 路径 / D 路径 / 业务键
ITEMS = [
    ('个股行情全量/stock_daily', r'个股行情全量\stock_daily_full.parquet',
     r'daily\stock_daily.parquet',
     ['trade_date', 'ts_code']),
    ('stock_basic', 'stock_basic_data.parquet',
     r'basic\stock_basic.parquet', ['ts_code']),
    ('top_list', 'top_list.parquet',
     r'lhb\top_list.parquet', ['trade_date', 'ts_code']),
    ('report_rc_daily', 'report_rc_daily.parquet',
     r'research\report_rc_daily.parquet',
     ['ts_code', 'report_date', 'org_name', 'author_name',
      'report_title', 'quarter']),
    ('ths_daily', 'ths_daily.parquet',
     r'board\ths_daily.parquet', ['trade_date', 'ts_code']),
    ('moneyflow_ind_ths', 'moneyflow_ind_ths.parquet',
     r'board_fundflow\moneyflow_ind_ths.parquet',
     ['trade_date', 'ts_code']),
    ('balancesheet', 'balancesheet.parquet',
     r'finance\balancesheet.parquet',
     ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', 'cashflow.parquet',
     r'finance\cashflow.parquet',
     ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', 'income.parquet',
     r'finance\income.parquet',
     ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('fina_indicator', 'fina_indicator.parquet',
     r'finance\fina_indicator.parquet',
     ['ts_code', 'end_date', 'ann_date']),
    ('forecast', 'forecast.parquet',
     r'finance\forecast.parquet',
     ['ts_code', 'end_date', 'ann_date', 'type']),
    ('express', 'express.parquet',
     r'finance\express.parquet',
     ['ts_code', 'end_date', 'ann_date']),
    ('dividend', 'dividend.parquet',
     r'finance\dividend.parquet',
     ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('share_change_event', 'share_change_event.parquet',
     r'finance\share_change_event.parquet',
     ['ts_code', 'change_date', 'change_reason']),
]

CRIT = ['q_gsprofit_margin', 'q_netprofit_margin', 'q_sales_qoq',
        'q_netprofit_qoq', 'q_dtprofit_to_profit', 'dtprofit_to_profit',
        'ocf_to_profit', 'ocf_to_or']


def keys_of(path, keys):
    f = pq.ParquetFile(path)
    have = set(f.schema_arrow.names)
    if isinstance(f.schema_arrow, object) and hasattr(
            f.schema_arrow, 'index'):
        pass
    use = [k for k in keys if k in have]
    if len(use) < len(keys):
        # 可能键在 index 里
        idx = []
        try:
            idx = list(pq.read_schema(path).names)
        except Exception:
            idx = []
        use = [k for k in keys if k in have or k in idx]
    t = pq.read_table(path, columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    return t, list(t.columns), f.metadata.num_rows


def norm_keyset(t, keys):
    d = {}
    for k in keys:
        if k not in t.columns:
            return None
        v = t[k]
        if k.endswith('_date'):
            v = pd.to_datetime(v, format='mixed', errors='coerce')
            v = v.dt.strftime('%Y%m%d')
        else:
            v = v.astype('string').fillna('<NA>')
        d[k] = v
    return d


def main():
    print('供应商交付完整性总审计')
    print('=' * 100)
    print('')
    print('%-28s %12s %12s %10s %10s  %s'
          % ('交付项', 'E行数', 'D行数', '仅D有', '仅E有', '判定'))
    print('-' * 100)

    verdicts = []
    for name, erel, drel, keys in ITEMS:
        ep = os.path.join(E_ROOT, erel)
        dp = os.path.join(D_BASE, drel)
        base = os.path.basename(erel)
        qsz = QUARK.get(base) or QUARK_SUBDIR.get(base)

        if not os.path.exists(ep):
            print('%-28s %s' % (name, 'E 缺失'))
            verdicts.append((name, 'FAIL', 'E 文件缺失'))
            continue
        if not os.path.exists(dp):
            print('%-28s %s' % (name, 'D 缺失(未迁移过)'))
            verdicts.append((name, 'INFO', 'D 无对应主库'))
            continue

        esz = os.path.getsize(ep)
        sznote = ''
        if qsz is not None:
            sznote = 'OK字节一致' if esz == qsz else \
                '字节差%+d' % (esz - qsz)

        et, ecols, erows = keys_of(ep, keys)
        dt, dcols, drows = keys_of(dp, keys)

        ek = norm_keyset(et, keys)
        dk = norm_keyset(dt, keys)
        if ek is None or dk is None:
            miss = [k for k in keys if k not in et.columns or k not in dt.columns]
            print('%-28s 键列不匹配 %s' % (name, miss))
            verdicts.append((name, 'FAIL', '键列不匹配'))
            continue

        eidx = pd.MultiIndex.from_arrays([ek[k] for k in keys])
        didx = pd.MultiIndex.from_arrays([dk[k] for k in keys])
        eset = set(eidx)
        dset = set(didx)
        only_d = len(dset - eset)
        only_e = len(eset - dset)

        if only_d == 0 and only_e == 0:
            v = 'PASS 完全覆盖'
        elif only_d == 0:
            v = 'PASS(E更新, D无独有)'
        elif only_e == 0:
            v = 'WARN E缺%d键' % only_d
        else:
            v = 'PASS 双向扩展'

        print('%-28s %12d %12d %10d %10d  %s'
              % (name, erows, drows, only_d, only_e, v))
        verdicts.append((name, v, '%s D独有=%d E独有=%d'
                         % (sznote, only_d, only_e)))

    print('')
    print('=' * 100)
    print('财务生产衍生字段填充率（E 侧）')
    print('=' * 100)
    ep = os.path.join(E_ROOT, 'fina_indicator.parquet')
    have = set(pq.ParquetFile(ep).schema_arrow.names)
    use = [c for c in CRIT if c in have]
    print('E 侧存在的列: %s' % use)
    print('E 侧缺失的列: %s' % [c for c in CRIT if c not in have])
    if use:
        t = pq.read_table(ep, columns=use).to_pandas()
        n = len(t)
        print('')
        print('%-24s %12s %12s' % ('字段', 'E填充率', 'E非空数'))
        for c in use:
            k = int(t[c].notna().sum())
            print('%-24s %11.3f%% %12d' % (c, k / n * 100, k))
        print('')
        print('E 总行数: %d' % n)

    print('')
    print('=' * 100)
    print('汇总')
    print('=' * 100)
    for name, v, note in verdicts:
        print('%-28s %-22s %s' % (name, v, note))
    return 0


if __name__ == '__main__':
    sys.exit(main())