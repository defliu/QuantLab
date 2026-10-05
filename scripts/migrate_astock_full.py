# -*- coding: utf-8 -*-
"""migrate_astock_full.py

把 E:\\temp\\行情数据 的离线资产迁移到 D:\\astock（唯一权威数据源）。

迁移语义（对应用户指令「全量的整体迁移过去，原来的就删除，如果只是增量的数据就迁移到对应的目录」）：

  FULL 表（14 张）：整体替换 —— 目标文件被 E 全量版完整覆盖，D 独有行随之删除。
  INCR 文件（1 个）：不混入主库，归档到 D:\\astock\\_incremental\\<子目录>\\。

安全架构（缺一不可）：
  1. 默认 dry-run；--apply 才写盘。
  2. 两阶段：先对全部 14 张表跑 preflight（不写任何文件），产出删除清单 / 字段损失清单 /
     缺口清单；任一阻断项未获显式确认令牌则拒绝进入 apply。
  3. 写入走 staging 临时文件 -> 同卷 os.replace 原子替换，不做「就地覆盖」。
  4. 每张表写前生成完整备份并记录 SHA-256 + 字节数；备份校验失败即中止。
  5. 写后重新读取复核（行数 / 键唯一性 / 日期范围 / 索引契约 / 列清单）。
  6. 跨表事务：任一张表失败 -> 自动回滚全部已替换表（备份为原样拷贝，可精确还原）。
  7. 阻断项不得静默豁免，必须由 --accept-* 令牌显式承认。

用法：
  python migrate_astock_full.py                          # dry-run：全表预检 + 打印删除/损失清单
  python migrate_astock_full.py --apply \
      --accept-delete 419 --accept-colloss 65 --accept-daily-gap
  python migrate_astock_full.py --apply --rollback-only   # 仅回滚上一轮
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime

import pandas as pd
import pyarrow.dataset as pads
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_BASE = r'D:\astock'
E_ROOT = r'E:\temp\行情数据'
E_DAILY_FULL = os.path.join(E_ROOT, '个股行情全量', 'stock_daily_full.parquet')
E_DAILY_INCR = os.path.join(E_ROOT, 'stock_daily.parquet')
INCR_ROOT = os.path.join(D_BASE, '_incremental')
TXN_LOG = os.path.join(D_BASE, '_migration_txn.json')

PIT_FIELDS = ('ann_date', 'f_ann_date')

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
FIN_DATE_COL = {'share_change_event': 'change_date'}
FIN_ORDER = ['balancesheet', 'cashflow', 'dividend', 'express',
             'fina_indicator', 'forecast', 'income', 'share_change_event']

DAILY_DATE_COL = 'trade_date'
DAILY_KEYS = ['trade_date', 'ts_code']
DAILY_VALUE_COLS = [
    'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol',
    'amount', 'up_limit', 'down_limit', 'turnover_rate', 'turnover_rate_f',
    'volume_ratio', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm',
    'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv',
    'adj_factor', 'suspend_timing', 'suspend_type', 'is_st', 'listed_days',
]

DAILY_GAP_DATES = ['20260629', '20260630', '20260701', '20260702', '20260703']


def full_manifest():
    """14 张 FULL 表的迁移清单。index_mode='multiindex' 表示目标必须是 (trade_date, ts_code) 双层索引。"""
    m = []
    m.append(dict(
        name='daily/stock_daily', group='daily',
        d_path=os.path.join(D_BASE, 'daily', 'stock_daily.parquet'),
        e_path=E_DAILY_FULL,
        keys=DAILY_KEYS, date_col=DAILY_DATE_COL,
        index_mode='multiindex', pivot=True,
        pit=False, gap_aware=True,
    ))
    for t in FIN_ORDER:
        m.append(dict(
            name='finance/' + t, group='finance',
            d_path=os.path.join(D_BASE, 'finance', t + '.parquet'),
            e_path=os.path.join(E_ROOT, t + '.parquet'),
            keys=FIN_KEYS[t], date_col=FIN_DATE_COL.get(t, 'ann_date'),
            index_mode='range', pivot=False,
            pit=True, gap_aware=False,
        ))
    m.append(dict(
        name='lhb/top_list', group='lhb',
        d_path=os.path.join(D_BASE, 'lhb', 'top_list.parquet'),
        e_path=os.path.join(E_ROOT, 'top_list.parquet'),
        keys=['trade_date', 'ts_code'], date_col='trade_date',
        index_mode='multiindex', pivot=True, pit=False, gap_aware=False,
    ))
    m.append(dict(
        name='research/report_rc_daily', group='research',
        d_path=os.path.join(D_BASE, 'research', 'report_rc_daily.parquet'),
        e_path=os.path.join(E_ROOT, 'report_rc_daily.parquet'),
        keys=['ts_code', 'report_date', 'org_name', 'author_name',
              'report_title', 'quarter'],
        date_col='report_date',
        index_mode='multiindex', pivot=True, pit=False, gap_aware=False,
    ))
    m.append(dict(
        name='board/ths_daily', group='board',
        d_path=os.path.join(D_BASE, 'board', 'ths_daily.parquet'),
        e_path=os.path.join(E_ROOT, 'ths_daily.parquet'),
        keys=['trade_date', 'ts_code'], date_col='trade_date',
        index_mode='multiindex', pivot=True, pit=False, gap_aware=False,
    ))
    m.append(dict(
        name='board_fundflow/moneyflow_ind_ths', group='board_fundflow',
        d_path=os.path.join(D_BASE, 'board_fundflow', 'moneyflow_ind_ths.parquet'),
        e_path=os.path.join(E_ROOT, 'moneyflow_ind_ths.parquet'),
        keys=['trade_date', 'ts_code'], date_col='trade_date',
        index_mode='multiindex', pivot=True, pit=False, gap_aware=False,
    ))
    m.append(dict(
        name='basic/stock_basic', group='basic',
        d_path=os.path.join(D_BASE, 'basic', 'stock_basic.parquet'),
        e_path=os.path.join(E_ROOT, 'stock_basic_data.parquet'),
        keys=['ts_code'], date_col='list_date',
        index_mode='range', pivot=False, pit=False, gap_aware=False,
    ))
    return m


def incr_manifest():
    """INCR 文件清单：只归档，不进主库。"""
    return [dict(
        name='daily_incr/stock_daily',
        e_path=E_DAILY_INCR,
        target_dir=os.path.join(INCR_ROOT, 'daily'),
        date_col=DAILY_DATE_COL, keys=DAILY_KEYS,
    )]


def hr(title):
    print('')
    print('=' * 78)
    print(title)
    print('=' * 78)


def sha256_of(path, chunk=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def arrow_names(path):
    return list(pq.read_schema(path).names)


def arrow_rows(path):
    return int(pq.ParquetFile(path).metadata.num_rows)


def canon_date(s):
    """任意日期表示 -> YYYYMMDD 字符串，缺失统一 'NA'。"""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d').fillna('NA').astype(str)
    t = s.astype(str).str.strip()
    t = t.where(~t.isin(('nan', 'NaT', 'None', '', '--')), 'NA')
    dg = t.str.split(' ').str[0].str.replace(r'\D', '', regex=True)
    out = dg.where(dg.str.len() == 8, 'NA')
    need = (out == 'NA') & (t != 'NA')
    if need.any():
        p = pd.to_datetime(t.where(need), errors='coerce')
        out = out.where(~need, p.dt.strftime('%Y%m%d').fillna('NA'))
    return out.fillna('NA').astype(str)


def frame_of(path):
    """按目标库契约读出规范形态：multiindex 表把 trade_date 提到索引层。"""
    df = pd.read_parquet(path)
    return df


def key_series(df, keys, indexed):
    if indexed:
        return pd.MultiIndex.from_frame(df[keys])
    return pd.MultiIndex.from_frame(df[keys])


def read_filtered(path, date_col, lo, hi, columns=None):
    """用 pyarrow.dataset 按时间窗低成本取子集（D 日线 1465 万行时避免整表载入）。"""
    dset = pads.dataset(path, format='parquet')
    sch = dset.schema
    cols = None
    if columns:
        cols = [c for c in columns if c in sch.names]
    tbl = dset.to_table(
        columns=cols,
        filter=(pads.field(date_col) >= pd.Timestamp(lo))
               & (pads.field(date_col) <= pd.Timestamp(hi)))
    return tbl.to_pandas().reset_index()


def preflight_incr(spec):
    """INCR 文件预检：只读归档信息，不写主库。"""
    st = dict(name=spec['name'], kind='INCR', status='PENDING', error='')
    print('')
    print('--- [INCR] %s' % spec['name'])
    if not os.path.isfile(spec['e_path']):
        raise RuntimeError('E 增量源不存在: %s' % spec['e_path'])
    sub = pd.read_parquet(spec['e_path'])
    if spec['date_col'] in sub.columns:
        s = canon_date(sub[spec['date_col']])
        st['range'] = (s.min(), s.max())
    st['rows'] = int(len(sub))
    st['src'] = spec['e_path']
    st['target_dir'] = spec['target_dir']
    st['cols'] = list(sub.columns)
    print('  行数=%d  日期[%s ~ %s]  列数=%d'
          % (st['rows'], st['range'][0], st['range'][1], len(st['cols'])))
    print('  归档目标目录（不混入主库）: %s' % spec['target_dir'])
    st['status'] = 'PLAN_OK'
    return st


def preflight_fin(spec, opts):
    """财务表预检：统计 D 独有行（将被删除）与列值损失（E 空覆盖 D 非空）。"""
    name = spec['name']
    d_path, e_path = spec['d_path'], spec['e_path']
    keys, dcol = spec['keys'], spec['date_col']

    st = dict(name=name, kind='FULL', status='PENDING', error='',
              d_path=d_path, e_path=e_path, keys=keys)
    print('')
    print('--- [FULL] %s' % name)

    d = pd.read_parquet(d_path)
    e = pd.read_parquet(e_path)
    st['d_rows'] = int(len(d))
    st['e_rows'] = int(len(e))

    for k in keys:
        if k not in d.columns or k not in e.columns:
            raise RuntimeError('主键列缺失: %s' % k)

    dk = pd.MultiIndex.from_frame(d[keys])
    ek = pd.MultiIndex.from_frame(e[keys])
    d_only_mask = ~dk.isin(ek)
    st['d_only_rows'] = int(d_only_mask.sum())
    st['d_only_keys'] = int(dk[~dk.duplicated()][d_only_mask].nunique())
    st['d_dup_rows'] = int(len(dk) - len(dk.unique()))
    st['e_dup_rows'] = int(len(ek) - len(ek.unique()))

    dcols = list(d.columns)
    ecols = list(e.columns)
    st['e_only_cols'] = [c for c in ecols if c not in dcols]
    st['d_only_cols'] = [c for c in dcols if c not in ecols]

    common_keys = dk.unique().intersection(ek.unique())
    st['common_keys'] = int(len(common_keys))

    loss_cols = []
    for c in dcols:
        if c in keys or c not in e.columns:
            continue
        dsub = d.loc[d_only_mask.reindex(d.index, fill_value=False), c] if False else d[c]
        esub = e[c]
        if dsub.dtype.kind not in 'fi' or esub.dtype.kind not in 'fi':
            continue
        dn = int(dsub.notna().sum())
        en = int(esub.notna().sum())
        if dn == 0:
            continue
        ratio = en / float(dn)
        if ratio < 0.5:
            loss_cols.append((c, dn, en, ratio))
    st['col_loss'] = loss_cols

    pit_missing = [f for f in PIT_FIELDS if f in dcols and f not in ecols]
    st['pit_missing'] = pit_missing
    if pit_missing:
        raise RuntimeError('PIT 字段在 E 缺失，禁止迁移: %s' % pit_missing)

    if st['d_only_rows'] > 0:
        head = d.loc[d_only_mask, keys + [dcol]].head(8)
        st['d_only_sample'] = head.to_dict('records')

    print('  D=%d 行 / E=%d 行 / 共同键=%d' % (st['d_rows'], st['e_rows'], st['common_keys']))
    print('  D 独有（将被删除）=%d 行  D 内部重复=%d  E 内部重复=%d'
          % (st['d_only_rows'], st['d_dup_rows'], st['e_dup_rows']))
    print('  E 独有列=%s  D 独有列=%s'
          % (st['e_only_cols'] or '无', st['d_only_cols'] or '无'))
    print('  列值损失（E 非空/D 非空 < 50%%）=%d 列' % len(loss_cols))
    for c, dn, en, ratio in loss_cols:
        print('     %-26s D非空=%-10d E非空=%-10d 保留率=%.4f' % (c, dn, en, ratio))
    st['status'] = 'PLAN_OK'
    return st


def preflight_daily(spec, opts):
    """日线预检：三日线缺口 / is_st 掉值日 / 目标索引契约。"""
    name = spec['name']
    d_path, e_path = spec['d_path'], spec['e_path']

    st = dict(name=name, kind='FULL', status='PENDING', error='',
              d_path=d_path, e_path=e_path, keys=DAILY_KEYS)
    print('')
    print('--- [FULL] %s' % name)

    dnames, enames = arrow_names(d_path), arrow_names(e_path)
    st['d_physical_cols'] = dnames
    st['e_physical_cols'] = enames
    st['e_only_cols'] = [c for c in enames if c not in dnames]
    st['d_only_cols'] = [c for c in dnames if c not in enames]

    st['d_rows'] = arrow_rows(d_path)
    st['e_rows'] = arrow_rows(e_path)

    dset = pads.dataset(d_path, format='parquet')
    eset = pads.dataset(e_path, format='parquet')

    gap_rows = 0
    gap_detail = {}
    for dt in DAILY_GAP_DATES:
        lo = hi = pd.Timestamp(dt)
        n_d = dset.to_table(columns=['ts_code'],
                            filter=(pads.field(DAILY_DATE_COL) >= lo)
                                   & (pads.field(DAILY_DATE_COL) <= hi)).num_rows
        n_e = eset.to_table(columns=['ts_code'],
                            filter=(pads.field(DAILY_DATE_COL) >= lo)
                                   & (pads.field(DAILY_DATE_COL) <= hi)).num_rows
        if n_d > 0 and n_e == 0:
            gap_rows += n_d
            gap_detail[dt] = n_d
    st['gap_rows'] = gap_rows
    st['gap_detail'] = gap_detail

    is_st_loss = {}
    for dt in ('20260828', '20260914', '20260918'):
        lo = hi = pd.Timestamp(dt)
        ed = eset.to_table(columns=['is_st', 'listed_days'],
                           filter=(pads.field(DAILY_DATE_COL) >= lo)
                                  & (pads.field(DAILY_DATE_COL) <= hi)).to_pandas()
        if len(ed) == 0:
            continue
        nn_is_st = int(ed['is_st'].notna().sum())
        nn_ld = int(ed['listed_days'].notna().sum())
        if nn_is_st == 0 or nn_ld == 0:
            dd = dset.to_table(columns=['is_st'],
                               filter=(pads.field(DAILY_DATE_COL) >= lo)
                                      & (pads.field(DAILY_DATE_COL) <= hi)).to_pandas()
            is_st_loss[dt] = dict(e_is_st_nonnull=nn_is_st, e_ld_nonnull=nn_ld,
                                  e_rows=int(len(ed)),
                                  d_is_st_nonnull=int(dd['is_st'].notna().sum()) if len(dd) else 0)
    st['is_st_loss'] = is_st_loss

    print('  D=%d 行 / E=%d 行' % (st['d_rows'], st['e_rows']))
    print('  E 独有列=%s  D 独有列=%s'
          % (st['e_only_cols'] or '无', st['d_only_cols'] or '无'))
    print('  E 缺失的历史交易日（D 独有、将被删除）=%d 行  明细=%s'
          % (gap_rows, gap_detail or '无'))
    if is_st_loss:
        print('  E 中 is_st/listed_days 整日缺失：')
        for dt, v in sorted(is_st_loss.items()):
            print('     %s  E行=%d  E.is_st非空=%d  E.listed_days非空=%d  D.is_st非空=%d'
                  % (dt, v['e_rows'], v['e_is_st_nonnull'],
                     v['e_ld_nonnull'], v['d_is_st_nonnull']))
    st['status'] = 'PLAN_OK'
    return st


def assert_daily_contract(path, expect_rows=None):
    """写后复核日线目标文件的索引契约与键唯一性。"""
    v = pq.ParquetFile(path).read_row_group(0).to_pandas()
    if list(v.index.names) != DAILY_KEYS:
        raise RuntimeError('日线索引契约破坏: index.names=%s，期望 %s'
                           % (list(v.index.names), DAILY_KEYS))
    if str(v.index.get_level_values('trade_date').dtype) != 'datetime64[ns]':
        raise RuntimeError('trade_date 索引 dtype 非 datetime64[ns]: %s'
                           % v.index.get_level_values('trade_date').dtype)
    missing = [c for c in DAILY_VALUE_COLS if c not in v.columns]
    if missing:
        raise RuntimeError('日线值列缺失: %s' % missing)
    rows = arrow_rows(path)
    if expect_rows is not None and rows != expect_rows:
        raise RuntimeError('日线写后行数不符: %d != %d' % (rows, expect_rows))
    return rows


def stage_and_replace(df, d_path, bak_path, run_tag, indexed, expect_rows=None,
                      keep_dup_baseline=None):
    """staging -> 备份 -> os.replace -> 写后复核。返回 (新行数, 备份信息)。"""
    staging = d_path + '.staging_%s' % run_tag
    if os.path.exists(staging):
        os.remove(staging)

    df.to_parquet(staging, engine='pyarrow')

    if os.path.isfile(bak_path):
        raise RuntimeError('备份已存在，拒绝覆盖（换 --run-tag）: %s' % bak_path)
    shutil.copy2(d_path, bak_path)
    if os.path.getsize(bak_path) != os.path.getsize(d_path):
        raise RuntimeError('备份大小校验失败: %s' % bak_path)
    bak_sha = sha256_of(bak_path)

    os.replace(staging, d_path)

    if indexed:
        rows = assert_daily_contract(d_path, expect_rows=expect_rows)
    else:
        v = pq.ParquetFile(d_path)
        rows = int(v.metadata.num_rows)
        if expect_rows is not None and rows != expect_rows:
            raise RuntimeError('写后行数不符: %d != %d' % (rows, expect_rows))

    if keep_dup_baseline is not None:
        vv = pd.read_parquet(d_path)
        kk = pd.MultiIndex.from_frame(vv.reset_index()[keep_dup_baseline['keys']])
        dup = int(len(kk) - len(kk.unique()))
        if dup > keep_dup_baseline['max_dup']:
            raise RuntimeError('写后主键重复 %d 超过 D 基线 %d'
                               % (dup, keep_dup_baseline['max_dup']))

    return rows, dict(bak=os.path.basename(bak_path), bak_sha256=bak_sha,
                      bak_bytes=os.path.getsize(bak_path))


def rollback(applied):
    """按事务日志逆序回滚已替换的表。"""
    print('')
    print('!! [ROLLBACK] 开始回滚 %d 张表' % len(applied))
    for item in reversed(applied):
        bak = item['bak_path']
        tgt = item['d_path']
        if os.path.isfile(bak):
            shutil.copy2(bak, tgt)
            print('   已还原 %s  <- %s' % (item['name'], os.path.basename(bak)))
        else:
            print('   !! 备份缺失，无法还原: %s' % item['name'])
    print('   回滚完成。')


def load_txn():
    if os.path.isfile(TXN_LOG):
        with open(TXN_LOG, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'run_tag': None, 'applied': []}


def save_txn(txn):
    with open(TXN_LOG, 'w', encoding='utf-8') as f:
        json.dump(txn, f, ensure_ascii=False, indent=2)


def do_rollback_only():
    txn = load_txn()
    if not txn.get('applied'):
        print('无已记录的事务，无需回滚。')
        return 0
    rollback(txn['applied'])
    txn['applied'] = []
    save_txn(txn)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='E:\\temp\\行情数据 -> D:\\astock 全量整体迁移 / 增量归档')
    ap.add_argument('--apply', action='store_true', help='真正写盘（默认 dry-run）')
    ap.add_argument('--run-tag', default=time.strftime('%Y%m%d'),
                    help='备份标识，决定 .bak_<tag> 后缀')
    ap.add_argument('--accept-delete', type=int, default=-1,
                    help='承认将删除的 D 独有行总数（默认 -1 = 不承认）')
    ap.add_argument('--accept-colloss', type=int, default=-1,
                    help='承认将被置空的财务列数（默认 -1 = 不承认）')
    ap.add_argument('--accept-daily-gap', action='store_true',
                    help='承认 E 缺失的历史交易日将被删除')
    ap.add_argument('--backfill-gap-from-d', action='store_true',
                    help='用 D 补回 E 缺失的历史交易日（推荐：避免历史序列断裂）')
    ap.add_argument('--backfill-isst-from-d', action='store_true',
                    help='用 D 补回 E 中整日缺失的 is_st / listed_days')
    ap.add_argument('--rollback-only', action='store_true', help='仅回滚上一轮事务')
    ap.add_argument('--only', default=None, help='仅处理名字含该子串的表（调试用）')
    opts = ap.parse_args()

    if opts.rollback_only:
        return do_rollback_only()

    tag = ''.join(ch for ch in opts.run_tag if ch.isdigit())
    if not tag:
        raise SystemExit('--run-tag 需为数字日期')

    print('pandas %s / pyarrow %s' % (pd.__version__, __import__('pyarrow').version))
    print('run-tag=%s  模式=%s' % (tag, 'APPLY' if opts.apply else 'DRY-RUN'))

    specs = full_manifest()
    incrs = incr_manifest()
    if opts.only:
        specs = [s for s in specs if opts.only in s['name']]
        incrs = [s for s in incrs if opts.only in s['name']]

    hr('阶段一：INCR 文件预检（只归档，不进主库）')
    incr_results = []
    for isp in incrs:
        try:
            incr_results.append(preflight_incr(isp))
        except Exception as exc:
            incr_results.append(dict(name=isp['name'], kind='INCR',
                                     status='FAILED', error=str(exc)))

    hr('阶段二：FULL 表预检（整体替换，统计删除与字段损失）')
    fin_results = []
    daily_result = None
    for spec in specs:
        try:
            if spec['group'] == 'daily':
                daily_result = preflight_daily(spec, opts)
            else:
                fin_results.append(preflight_fin(spec, opts))
        except Exception as exc:
            fin_results.append(dict(name=spec['name'], kind='FULL',
                                   status='FAILED', error='%s: %s' % (type(exc).__name__, exc)))
            print('   !! %s' % exc)

    hr('阶段三：阻断项汇总')
    tot_delete = sum(r.get('d_only_rows', 0) for r in fin_results)
    tot_loss = sum(len(r.get('col_loss', [])) for r in fin_results)
    gap_rows = daily_result.get('gap_rows', 0) if daily_result else 0
    failed = [r for r in fin_results + incr_results if r['status'] == 'FAILED']

    print('  将被删除的 D 独有行合计 = %d' % tot_delete)
    print('  将被置空的财务列合计   = %d' % tot_loss)
    print('  E 缺失的历史交易日行数 = %d' % gap_rows)
    print('  预检失败表数           = %d' % len(failed))

    blockers = []
    if failed:
        blockers.append('存在预检失败表：%s' % [r['name'] for r in failed])
    if opts.accept_delete != tot_delete:
        blockers.append('未承认删除行数（当前 %d，需 --accept-delete %d）' % (tot_delete, tot_delete))
    if opts.accept_colloss != tot_loss:
        blockers.append('未承认字段损失列数（当前 %d，需 --accept-colloss %d）' % (tot_loss, tot_loss))
    if gap_rows > 0 and not (opts.accept_daily_gap or opts.backfill_gap_from_d):
        blockers.append('E 缺失 %d 行历史交易日，需 --backfill-gap-from-d（推荐）或 --accept-daily-gap' % gap_rows)

    if blockers:
        hr('阻断：未获确认令牌，拒绝进入 apply')
        for b in blockers:
            print('  !! %s' % b)
        print('')
        print('当前 D:\\astock 未被修改。')

    if not opts.apply:
        hr('DRY-RUN 结束（未写任何文件）')
        print('建议命令：')
        cmd = ('python migrate_astock_full.py --apply --run-tag %s '
               '--accept-delete %d --accept-colloss %d' % (tag, tot_delete, tot_loss))
        if gap_rows > 0:
            cmd += ' --backfill-gap-from-d --backfill-isst-from-d'
        print('  ' + cmd)
        return 0 if not blockers else 2

    if blockers:
        return 2

    hr('阶段四：执行（备份 -> staging -> 原子替换 -> 写后复核）')
    txn = dict(run_tag=tag, started=datetime.now().isoformat(timespec='seconds'),
               applied=[], incr=[])
    applied = []
    try:
        for isp in incrs:
            os.makedirs(isp['target_dir'], exist_ok=True)
            sub = pd.read_parquet(isp['e_path'])
            dst = os.path.join(isp['target_dir'],
                               'stock_daily_incr_%s.parquet' % tag)
            if os.path.isfile(dst):
                raise RuntimeError('增量归档已存在: %s' % dst)
            sub.to_parquet(dst, engine='pyarrow')
            txn['incr'].append(dict(name=isp['name'], src=isp['e_path'], dst=dst,
                                    rows=int(len(sub)), sha256=sha256_of(dst)))
            print('  [INCR] 归档 %s 行 -> %s' % (len(sub), dst))

        for spec in specs:
            t0 = time.time()
            name = spec['name']
            print('')
            print('--- 写入 %s' % name)
            indexed = spec['index_mode'] == 'multiindex'
            bak_path = spec['d_path'] + '.bak_%s' % tag

            if spec['group'] == 'daily':
                rows, bak = write_daily(spec, opts, tag, bak_path)
            else:
                rows, bak = write_table(spec, tag, bak_path)

            applied.append(dict(name=name, d_path=spec['d_path'],
                                bak_path=bak_path, bak_sha256=bak['bak_sha256']))
            txn['applied'].append(dict(name=name, d_path=spec['d_path'],
                                       bak_path=bak_path, bak_sha256=bak['bak_sha256'],
                                       rows=rows))
            save_txn(txn)
            print('  OK %s  行数=%d  备份=%s  耗时=%.1fs'
                  % (name, rows, bak['bak'], time.time() - t0))
    except Exception as exc:
        print('')
        print('!! [ABORT] %s: %s' % (type(exc).__name__, exc))
        traceback.print_exc()
        rollback(applied)
        txn['applied'] = []
        txn['aborted'] = '%s: %s' % (type(exc).__name__, exc)
        save_txn(txn)
        return 1

    txn['finished'] = datetime.now().isoformat(timespec='seconds')
    save_txn(txn)
    hr('全部完成')
    print('事务日志: %s（回滚请用 --rollback-only）' % TXN_LOG)
    return 0


def write_daily(spec, opts, tag, bak_path):
    """日线整体替换：E 全量 -> 规范索引形态 -> 可选补洞 -> staging -> 原子替换。"""
    e_path, d_path = spec['e_path'], spec['d_path']

    e = pd.read_parquet(e_path)
    if 'index' in e.columns:
        e = e.drop(columns=['index'])
    for c in DAILY_KEYS:
        if c not in e.columns:
            raise RuntimeError('E 缺日线键列: %s' % c)
    e['trade_date'] = pd.to_datetime(e['trade_date'], errors='coerce')
    if e['trade_date'].isna().any():
        raise RuntimeError('E trade_date 存在无法解析值: %d 行'
                           % int(e['trade_date'].isna().sum()))
    e = e.set_index(DAILY_KEYS).sort_index()

    if opts.backfill_gap_from_d or opts.backfill_isst_from_d:
        e = backfill_daily(e, d_path, opts)

    e = e[DAILY_VALUE_COLS]
    return stage_and_replace(e, d_path, bak_path, tag, indexed=True,
                             expect_rows=int(len(e)))


def backfill_daily(e, d_path, opts):
    """从 D 主库补回 E 缺失项：整段历史交易日 + is_st/listed_days 整日缺失。"""
    print('  [补洞] 启用：从 D 主库回补')
    added = 0

    if opts.backfill_gap_from_d:
        extra = []
        for dt in DAILY_GAP_DATES:
            sub = read_filtered(d_path, DAILY_DATE_COL, dt, dt)
            if len(sub) == 0:
                continue
            sub = sub.set_index(DAILY_KEYS).sort_index()
            sub = sub[~sub.index.isin(e.index)]
            if len(sub):
                extra.append(sub[DAILY_VALUE_COLS])
        if extra:
            add = pd.concat(extra)
            added += len(add)
            e = pd.concat([e, add]).sort_index()
            print('     补回历史交易日 %d 行' % len(add))
        if not e.index.is_unique:
            dup = int(e.index.duplicated().sum())
            raise RuntimeError('补洞后日线主键重复 %d 行，停止' % dup)

    if opts.backfill_isst_from_d:
        ed = pads.dataset(d_path, format='parquet')
        lo = pd.Timestamp('20260820')
        hi = pd.Timestamp('20260930')
        cols = [c for c in DAILY_VALUE_COLS if c in ed.schema.names]
        sub = ed.to_table(columns=cols,
                          filter=(pads.field(DAILY_DATE_COL) >= lo)
                                 & (pads.field(DAILY_DATE_COL) <= hi)).to_pandas()
        sub['trade_date'] = pd.to_datetime(sub['trade_date'])
        sub = sub.set_index(DAILY_KEYS).sort_index()
        common = sub.index.intersection(e.index)
        if len(common):
            tgt = e.loc[common]
            filled = 0
            for c in ('is_st', 'listed_days'):
                need = tgt[c].isna()
                if not need.any():
                    continue
                src = sub.loc[common, c]
                tgt.loc[common[need], c] = src[need]
                filled += int(need.sum())
            if filled:
                e.loc[common, ['is_st', 'listed_days']] = tgt[['is_st', 'listed_days']]
                added += 0
                print('     补回 is_st/listed_days %d 个空单元' % filled)

    if not e.index.is_unique:
        raise RuntimeError('补洞后日线主键重复，停止')
    return e


def write_table(spec, tag, bak_path):
    """非日线表：E 全量直接作为新主库内容（严格整体替换）。"""
    e = pd.read_parquet(spec['e_path'])
    if 'index' in e.columns and 'index' not in arrow_names(spec['d_path']):
        e = e.drop(columns=['index'])
    if spec['index_mode'] == 'multiindex':
        for c in spec['keys']:
            if c not in e.columns:
                raise RuntimeError('E 缺键列: %s' % c)
        e = e.set_index(spec['keys']).sort_index()
        baseline = dict(keys=spec['keys'], max_dup=0)
        ddf = pd.read_parquet(spec['d_path'])
        if len(ddf):
            kk = pd.MultiIndex.from_frame(ddf.reset_index()[spec['keys']])
            baseline['max_dup'] = int(len(kk) - len(kk.unique()))
        return stage_and_replace(e, spec['d_path'], bak_path, tag,
                                 indexed=True, expect_rows=int(len(e)),
                                 keep_dup_baseline=baseline)
    e = e.sort_values(spec['keys'], kind='mergesort').reset_index(drop=True)
    return stage_and_replace(e, spec['d_path'], bak_path, tag,
                             indexed=False, expect_rows=int(len(e)))


if __name__ == '__main__':
    sys.exit(main())
