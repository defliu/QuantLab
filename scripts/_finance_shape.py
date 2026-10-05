import os
import pyarrow.parquet as pq
import pandas as pd

_OUT = open(r'D:\QuantLab\browse\_finance_shape.txt', 'w', encoding='utf-8')
_p = print


def print(*a, **k):
    k['file'] = _OUT
    _p(*a, **k)


def mib(n):
    return '%.1fMiB' % (n / 1024.0 ** 2)


def probe(path):
    try:
        f = pq.ParquetFile(path)
    except Exception as e:
        return {'err': str(e)}

    md = f.metadata
    sch = f.schema_arrow

    d = {
        'rows': md.num_rows,
        'rowgroups': md.num_row_groups,
        'cols': md.num_columns,
        'names': [c.name for c in sch],
    }

    keys = [c for c in ('ts_code', 'end_date', 'ann_date', 'f_ann_date',
                        'report_type', 'div_proc', 'type', 'change_date',
                        'change_reason', 'update_flag')
            if c in d['names']]

    datecols = [c for c in ('end_date', 'ann_date', 'f_ann_date',
                            'change_date')
                if c in d['names']]

    if not keys:
        return d

    try:
        tb = f.read(columns=keys)
        df = tb.to_pandas()
    except Exception as e:
        d['read_err'] = str(e)
        return d

    d['keys'] = keys
    d['n_codes'] = int(df['ts_code'].nunique()) if 'ts_code' in df else 0

    for c in datecols:
        s = df[c]
        try:
            sn = pd.to_datetime(s, errors='coerce')
            d['%s_min' % c] = str(sn.min())
            d['%s_max' % c] = str(sn.max())
            d['%s_na' % c] = int(sn.isna().sum())
            raw = s.astype(str).str.replace('-', '', regex=False)
            d['%s_nfmt' % c] = sorted(set(
                len(x) for x in raw.head(2000).unique()
            ))
        except Exception:
            pass

    if 'ann_date' in df and 'end_date' in df:
        try:
            ae = pd.to_datetime(df['ann_date'], errors='coerce')
            ee = pd.to_datetime(df['end_date'], errors='coerce')
            ahead = int((ae < ee).sum())
            d['ann_before_end'] = ahead
            d['pit_violation_rate'] = (
                '%.4f%%' % (100.0 * ahead / max(1, len(df)))
            )
        except Exception:
            pass

    if 'report_type' in df:
        d['report_type'] = df['report_type'].value_counts().to_dict()

    if 'f_ann_date' in df:
        try:
            fa = pd.to_datetime(df['f_ann_date'], errors='coerce')
            an = pd.to_datetime(df['ann_date'], errors='coerce')
            d['f_ann_gt_ann'] = int((fa > an).sum())
            d['f_ann_min'] = str(fa.min())
            d['f_ann_max'] = str(fa.max())
        except Exception:
            pass

    return d


FIN = ['balancesheet', 'cashflow', 'dividend', 'express',
       'fina_indicator', 'forecast', 'income', 'share_change_event']

print('=' * 100)
print('D-SIDE PRODUCTION  D:\\astock\\finance')
print('=' * 100)

dd = {}

for t in FIN:
    p = os.path.join(r'D:\astock\finance', t + '.parquet')
    if not os.path.exists(p):
        print('%-22s [MISSING]' % t)
        continue
    d = probe(p)
    dd[t] = d
    print('')
    print('--- %s  (%s, rows=%s, cols=%s, rowgroups=%s)'
          % (t, mib(os.path.getsize(p)), d.get('rows'), d.get('cols'),
             d.get('rowgroups')))
    if d.get('err') or d.get('read_err'):
        print('    ERR %s' % (d.get('err') or d.get('read_err')))
        continue
    print('    codes=%s' % d.get('n_codes'))
    for c in ('end_date', 'ann_date', 'f_ann_date', 'change_date'):
        if '%s_min' % c in d:
            print('    %-12s %s .. %s   na=%s  rawlen=%s'
                  % (c, d['%s_min' % c], d['%s_max' % c],
                     d.get('%s_na' % c), d.get('%s_nfmt' % c)))
    if 'f_ann_gt_ann' in d:
        print('    f_ann>ann  = %s' % d['f_ann_gt_ann'])
    if 'pit_violation_rate' in d:
        print('    ann<end    = %s  (rate %s)'
              % (d.get('ann_before_end'), d['pit_violation_rate']))
    if 'report_type' in d:
        print('    report_type= %s' % d['report_type'])

print('')
print('=' * 100)
print('E-SIDE DELIVERY  E:\\temp\\行情数据')
print('=' * 100)

ee = {}

for t in FIN:
    p = os.path.join(r'E:\temp\行情数据', t + '.parquet')
    if not os.path.exists(p):
        print('%-22s [MISSING]' % t)
        continue
    d = probe(p)
    ee[t] = d
    print('')
    print('--- %s  (%s, rows=%s, cols=%s, rowgroups=%s)'
          % (t, mib(os.path.getsize(p)), d.get('rows'), d.get('cols'),
             d.get('rowgroups')))
    if d.get('err') or d.get('read_err'):
        print('    ERR %s' % (d.get('err') or d.get('read_err')))
        continue
    print('    codes=%s' % d.get('n_codes'))
    for c in ('end_date', 'ann_date', 'f_ann_date', 'change_date'):
        if '%s_min' % c in d:
            print('    %-12s %s .. %s   na=%s  rawlen=%s'
                  % (c, d['%s_min' % c], d['%s_max' % c],
                     d.get('%s_na' % c), d.get('%s_nfmt' % c)))
    if 'f_ann_gt_ann' in d:
        print('    f_ann>ann  = %s' % d['f_ann_gt_ann'])
    if 'pit_violation_rate' in d:
        print('    ann<end    = %s  (rate %s)'
              % (d.get('ann_before_end'), d['pit_violation_rate']))
    if 'report_type' in d:
        print('    report_type= %s' % d['report_type'])

print('')
print('=' * 100)
print('D vs E  SIDE-BY-SIDE DELTA')
print('=' * 100)
print('%-22s %12s %12s %12s %10s %10s %12s %12s'
      % ('TABLE', 'D_rows', 'E_rows', 'DELTA', 'D_codes', 'E_codes',
         'D_ann_max', 'E_ann_max'))
print('-' * 100)

for t in FIN:
    a = dd.get(t) or {}
    b = ee.get(t) or {}
    if not a or not b:
        continue
    dr = a.get('rows') or 0
    er = b.get('rows') or 0
    print('%-22s %12s %12s %12s %10s %10s %12s %12s'
          % (t, dr, er, '%+d' % (er - dr), a.get('n_codes'), b.get('n_codes'),
             str(a.get('ann_date_max'))[:10], str(b.get('ann_date_max'))[:10]))

print('')
print('column-set identical?')
for t in FIN:
    a = dd.get(t) or {}
    b = ee.get(t) or {}
    if not a or not b:
        continue
    na = set(a.get('names') or [])
    nb = set(b.get('names') or [])
    only_d = sorted(na - nb)
    only_e = sorted(nb - na)
    tag = 'SAME' if not only_d and not only_e else 'DIFF'
    print('  %-22s %s  D_only=%s  E_only=%s'
          % (t, tag, only_d[:8], only_e[:8]))

_OUT.close()