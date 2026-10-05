# -*- coding: utf-8 -*-
"""migrate_astock_full_v2.py

把 E:\\temp\\行情数据 的离线资产迁移到 D:\\astock（唯一权威数据源）。

迁移语义（严格遵循用户指令「全量的整体迁移过去，原来的就删除，
如果只是增量的数据就迁移到对应的目录」）：

  FULL 表（14 张）：整体替换 —— 目标文件被 E 全量版完整覆盖，D 独有行随之删除。
  INCR 文件（1 个）：不混入主库，归档到 D:\\astock\\_incremental\\<子目录>\\。

严格语义是**默认且唯一**语义。任何「保留 D 独有行 / 用 D 回填 E 空值 /
裁决 E 多版本」的��为都改变「整体迁移」的定义，因此一律为显式 opt-in
开关，默认关闭，且必须伴随对应确认令牌。

安全架构（缺一不可）：
  1. 默认 dry-run；--apply 才写盘。
  2. 阶段一预检**不写任何文件**，覆盖全部 FULL + INCR 表，产出结构化计划。
  3. 计划指纹（PLAN_FP）：把「将删除行数 / 值级损失单元格数 / 缺口行数 /
     重复行数 / 空间预算」等不可逆后果哈希成一个短指纹；--apply 必须用
     --accept-plan <指纹> 精确承认本次计划，计划一变指纹即变。
  4. 财务共同键值级损失用 LOGIC KEY 对齐后逐单元格比对（旧脚本只比全表
     非空数，会漏掉 1600 万级同键掉值）。
  5. 写入走同卷 staging -> 备份(校验 SHA-256) -> os.replace 原子替换。
  6. 写后完整重读复核（行数 / 索引契约 / 列清单 / 日期范围）。
  7. 跨表事务：**当前表在 os.replace 之后、复核之前即登记为已应用**，
     确保「刚替换但复核失败」也能回滚（旧脚本在此处有漏洞）。
  8. 回滚本身也走 staging + os.replace，不做 copy2 就地覆盖。
  9. INCR 归档同样登记进事务并可回滚。
 10. 事务日志原子写入（临时文件 + os.replace）。

用法：
  # 1) 只读预检（默认，不写任何文件）
  python migrate_astock_full_v2.py

  # 2) 看过报告后，用 --accept-plan <指纹> 精确承认本次计划再写盘
  python migrate_astock_full_v2.py --apply --accept-plan <指纹>

  # 3) 仅回滚上一轮
  python migrate_astock_full_v2.py --rollback-only
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
import pyarrow as pa
import pyarrow.compute as pc
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
TXN_LOG = os.path.join(D_BASE, '_migration_txn_v2.json')
REPORT_JSON = os.path.join(D_BASE, '_migration_plan_v2.json')

PIT_FIELDS = ('ann_date', 'f_ann_date')

# FULL KEY：物理去重口径（两文件是否存在完全相同的一条物理记录）
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
# LOGIC KEY：业务对象口径（同一报告期的多个公告版本）
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
DAILY_NULL_DAYS = ['20260828', '20260914', '20260918']
DAILY_NULL_COLS = ['is_st', 'listed_days']

# 下游代码真实消费的财务衍生字段（factor_defs_pilot._fin / run_screen.FIN_FIELDS）
DOWNSTREAM_CRITICAL = {
    'fina_indicator': [
        'dtprofit_to_profit', 'ocf_to_profit', 'ocf_to_or', 'q_gsprofit_margin',
        'q_netprofit_margin', 'q_sales_qoq', 'q_netprofit_qoq',
        'q_dtprofit_to_profit', 'roe_avg', 'q_profit_yoy',
    ],
    'income': ['total_opcost', 'oth_income', 'credit_impa_loss',
               'asset_disp_income', 'continued_net_profit'],
    'balancesheet': ['lease_liab', 'use_right_assets'],
    'cashflow': [],
}


def full_manifest():
    """14 张 FULL 表。group 决定走哪条预检/写入路径。"""
    m = []
    m.append(dict(
        name='daily/stock_daily', group='daily',
        d_path=os.path.join(D_BASE, 'daily', 'stock_daily.parquet'),
        e_path=E_DAILY_FULL,
        keys=DAILY_KEYS, date_col=DAILY_DATE_COL,
        index_mode='multiindex', pit=False,
    ))
    for t in FIN_ORDER:
        m.append(dict(
            name='finance/' + t, group='finance',
            d_path=os.path.join(D_BASE, 'finance', t + '.parquet'),
            e_path=os.path.join(E_ROOT, t + '.parquet'),
            keys=FULL_KEYS[t], logic_keys=LOGIC_KEYS[t],
            date_col=FIN_DATE_COL.get(t, 'ann_date'),
            fin_name=t,
            index_mode='range', pit=True,
        ))
    m.append(dict(
        name='lhb/top_list', group='lhb',
        d_path=os.path.join(D_BASE, 'lhb', 'top_list.parquet'),
        e_path=os.path.join(E_ROOT, 'top_list.parquet'),
        keys=['trade_date', 'ts_code'], date_col='trade_date',
        index_mode='multiindex', pit=False,
    ))
    m.append(dict(
        name='research/report_rc_daily', group='research',
        d_path=os.path.join(D_BASE, 'research', 'report_rc_daily.parquet'),
        e_path=os.path.join(E_ROOT, 'report_rc_daily.parquet'),
        keys=['ts_code', 'report_date', 'org_name', 'author_name',
              'report_title', 'quarter'],
        date_col='report_date',
        index_mode='multiindex', pit=False,
    ))
    m.append(dict(
        name='board/ths_daily', group='board',
        d_path=os.path.join(D_BASE, 'board', 'ths_daily.parquet'),
        e_path=os.path.join(E_ROOT, 'ths_daily.parquet'),
        keys=['trade_date', 'ts_code'], date_col='trade_date',
        index_mode='multiindex', pit=False,
    ))
    m.append(dict(
        name='board_fundflow/moneyflow_ind_ths', group='board_fundflow',
        d_path=os.path.join(D_BASE, 'board_fundflow', 'moneyflow_ind_ths.parquet'),
        e_path=os.path.join(E_ROOT, 'moneyflow_ind_ths.parquet'),
        keys=['trade_date', 'ts_code'], date_col='trade_date',
        index_mode='multiindex', pit=False,
    ))
    m.append(dict(
        name='basic/stock_basic', group='basic',
        d_path=os.path.join(D_BASE, 'basic', 'stock_basic.parquet'),
        e_path=os.path.join(E_ROOT, 'stock_basic_data.parquet'),
        keys=['ts_code'], date_col='list_date',
        index_mode='range', pit=False,
    ))
    return m


def incr_manifest():
    return [dict(
        name='daily_incr/stock_daily',
        e_path=E_DAILY_INCR,
        target_dir=os.path.join(INCR_ROOT, 'daily'),
        date_col=DAILY_DATE_COL, keys=DAILY_KEYS,
    )]


# ---------------------------------------------------------------- 基础工具

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


def free_gb(path):
    p = os.path.dirname(path) or path
    drive = os.path.splitdrive(p)[0] + '\\'
    try:
        return shutil.disk_usage(drive).free / (1024.0 ** 3)
    except Exception:
        return float('nan')


def to_flat(df, keys):
    """把 pandas 索引层摊平成列，保证键列可直接取用。

    Parquet 读回后键可能在 df.columns，也可能在 df.index.names（Pandas
    metadata 会把 (trade_date, ts_code) 恢复成 MultiIndex）。旧脚本直接
    df[keys] 取键，遇到索引层键会 KeyError 或取到错列。
    """
    missing = [k for k in keys if k not in df.columns]
    if not missing:
        return df
    idx_names = list(getattr(df.index, 'names', []) or [])
    can_reset = all(k in idx_names for k in missing)
    if can_reset:
        df = df.reset_index()
    else:
        raise RuntimeError('键列既不在数据列也不在索引层: %s' % missing)
    still = [k for k in missing if k not in df.columns]
    if still:
        raise RuntimeError('摊平后仍缺键列: %s' % still)
    return df


def canon_date(s):
    """任意日期表示 -> YYYYMMDD 字符串，缺失统一 'NA'。

    D 存 timestamp(2023-12-31 00:00:00)，E 存 20231231。不统一成同一
    口径就直接比键，同一条业务记录会被误判成 D 独有（会被误删）。
    """
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


def canon_key_frame(df, keys, date_cols):
    """产出用于比键的 canonical 副本（原 df 不动，绝不回写生产语义）。"""
    out = pd.DataFrame(index=df.index)
    for k in keys:
        if k in date_cols:
            out[k] = canon_date(df[k])
        else:
            out[k] = df[k].astype(str)
    return out


def date_cols_of(keys, extra=()):
    """把形如 *_date / change_date 的键列挑出来做日期规范化。"""
    out = set(extra)
    for k in keys:
        if k.endswith('date') or k.endswith('_dt'):
            out.add(k)
    return out


# ---------------------------------------------------------------- 预检

def preflight_incr(spec):
    st = dict(name=spec['name'], kind='INCR', status='PENDING', error='')
    print('')
    print('--- [INCR] %s' % spec['name'])
    if not os.path.isfile(spec['e_path']):
        raise RuntimeError('E 增量源不存在: %s' % spec['e_path'])
    sub = pd.read_parquet(spec['e_path'])
    st['rows'] = int(len(sub))
    st['cols'] = int(len(sub.columns))
    st['bytes'] = os.path.getsize(spec['e_path'])
    st['src'] = spec['e_path']
    st['e_path'] = spec['e_path']
    st['target_dir'] = spec['target_dir']
    dcol = spec['date_col']
    if dcol in sub.columns:
        s = canon_date(sub[dcol])
        st['range'] = [s.min(), s.max()]
    else:
        st['range'] = ['(无 %s 列)' % dcol, '(无 %s 列)' % dcol]
    print('  行数=%d  列数=%d  日期[%s ~ %s]'
          % (st['rows'], st['cols'], st['range'][0], st['range'][1]))
    print('  归档目标目录（不混入主库）: %s' % spec['target_dir'])
    st['status'] = 'PLAN_OK'
    return st


def preflight_daily(spec, opts):
    """日线预检：五日缺口 / is_st+listed_days 整日缺失 / 索引与列契约。"""
    name = spec['name']
    d_path, e_path = spec['d_path'], spec['e_path']
    st = dict(name=name, kind='FULL', group='daily', status='PENDING', error='',
              d_path=d_path, e_path=e_path, keys=DAILY_KEYS)
    print('')
    print('--- [FULL] %s' % name)

    dnames, enames = arrow_names(d_path), arrow_names(e_path)
    st['d_rows'] = arrow_rows(d_path)
    st['e_rows'] = arrow_rows(e_path)
    st['e_only_cols'] = [c for c in enames if c not in dnames]
    st['d_only_cols'] = [c for c in dnames if c not in enames]
    miss_val = [c for c in DAILY_VALUE_COLS if c not in enames]
    if miss_val:
        raise RuntimeError('E 日线缺值列: %s' % miss_val)
    st['missing_value_cols'] = miss_val

    dset = pads.dataset(d_path, format='parquet')
    eset = pads.dataset(e_path, format='parquet')

    gap_rows, gap_detail = 0, {}
    for dt in DAILY_GAP_DATES:
        n_d = day_count(dset, DAILY_DATE_COL, dt)
        n_e = day_count(eset, DAILY_DATE_COL, dt)
        if n_d > 0 and n_e == 0:
            gap_rows += int(n_d)
            gap_detail[dt] = int(n_d)
    st['gap_rows'] = gap_rows
    st['gap_detail'] = gap_detail
    if gap_rows:
        print('  !! E 缺失的历史交易日（D 独有、严格替换将删除）= %d 行' % gap_rows)
        for dt, n in sorted(gap_detail.items()):
            print('       %s  D=%d 行  E=0 行' % (dt, n))

    null_loss = {}
    for dt in DAILY_NULL_DAYS:
        ef = pc.field(DAILY_DATE_COL)
        df_ = pc.field(DAILY_DATE_COL)
        lo = hi = pd.Timestamp(dt)
        ed = eset.to_table(
            columns=DAILY_NULL_COLS + ['ts_code'],
            filter=(ef >= lo) & (ef <= hi)).to_pandas()
        if len(ed) == 0:
            continue
        stat = dict(e_rows=int(len(ed)))
        for c in DAILY_NULL_COLS:
            stat['e_%s_nonnull' % c] = int(ed[c].notna().sum())
        if all(stat['e_%s_nonnull' % c] == 0 for c in DAILY_NULL_COLS):
            dd = dset.to_table(
                columns=DAILY_NULL_COLS + ['ts_code'],
                filter=(df_ >= lo) & (df_ <= hi)).to_pandas()
            stat['d_rows'] = int(len(dd))
            stat['d_is_st_nonnull'] = int(dd['is_st'].notna().sum()) if len(dd) else 0
            stat['d_cover'] = bool(len(dd) > 0 and dd['is_st'].notna().sum() > 0)
            null_loss[dt] = stat
    st['null_day_loss'] = null_loss
    if null_loss:
        print('  !! E 中 %s 整日缺失：D 可覆盖=%s'
              % ('/'.join(DAILY_NULL_COLS),
                 {k: v['d_cover'] for k, v in null_loss.items()}))
        for dt, v in sorted(null_loss.items()):
            note = '可从 D 精确回填' if v['d_cover'] else 'D 无此日期，只能前向填充（近似）'
            print('       %s  E行=%d  %s' % (dt, v['e_rows'], note))

    print('  D=%d 行 / E=%d 行' % (st['d_rows'], st['e_rows']))
    print('  E 独有列=%s  D 独有列=%s'
          % (st['e_only_cols'] or '无', st['d_only_cols'] or '无'))
    st['status'] = 'PLAN_OK'
    return st


def preflight_fin(spec, opts):
    """财务预检：canonical 键 + D 独有行 + **共同键逐单元格**值级损失。"""
    name = spec['name']
    d_path, e_path = spec['d_path'], spec['e_path']
    keys, lkeys = spec['keys'], spec['logic_keys']
    fname = spec['fin_name']
    dcol = spec['date_col']

    st = dict(name=name, kind='FULL', group='finance', status='PENDING', error='',
              d_path=d_path, e_path=e_path, keys=keys, logic_keys=lkeys)
    print('')
    print('--- [FULL] %s' % name)

    d = to_flat(pd.read_parquet(d_path), keys)
    e = to_flat(pd.read_parquet(e_path), keys)
    st['d_rows'], st['e_rows'] = int(len(d)), int(len(e))

    for k in keys:
        if k not in d.columns:
            raise RuntimeError('D 缺主键列 %s' % k)
        if k not in e.columns:
            raise RuntimeError('E 缺主键列 %s' % k)

    dcols, ecols = list(d.columns), list(e.columns)
    st['e_only_cols'] = [c for c in ecols if c not in dcols]
    st['d_only_cols'] = [c for c in dcols if c not in ecols]

    # ---- canonical 键（D timestamp vs E YYYYMMDD 必须先统一口径）
    dcols_c = date_cols_of(keys, extra=PIT_FIELDS + (dcol,))
    dkey = canon_key_frame(d, keys, dcols_c)
    ekey = canon_key_frame(e, keys, dcols_c)
    dk = pd.MultiIndex.from_frame(dkey)
    ek = pd.MultiIndex.from_frame(ekey)

    d_only_mask = ~dk.isin(ek)
    st['d_only_rows'] = int(d_only_mask.sum())
    st['d_dup_rows'] = int(len(dk) - len(dk.unique()))
    st['e_dup_rows'] = int(len(ek) - len(ek.unique()))
    st['common_keys'] = int(len(dk) - st['d_only_rows'])
    st['e_only_rows'] = int((~ek.isin(dk)).sum())

    # ---- LOGIC KEY 重复（E 自身多版本）
    lkc = date_cols_of(lkeys)
    elk = pd.MultiIndex.from_frame(canon_key_frame(e, lkeys, lkc))
    st['e_logic_dup_rows'] = int(len(elk) - len(elk.unique()))

    pit_missing = [f for f in PIT_FIELDS if f in dcols and f not in ecols]
    st['pit_missing'] = pit_missing
    if pit_missing:
        raise RuntimeError('PIT 字段在 E 缺失，禁止迁移: %s' % pit_missing)

    # ---- 共同业务对象（LOGIC KEY）逐单元格值级损失
    #
    # 为什么必须按 LOGIC KEY 而不是 FULL KEY 比：
    # E 自身有 54 万行重复 FULL 键（同一业务记录多份拷贝）。按 FULL KEY 对齐时，
    # 只要某一份拷贝该列为空就会被记成「掉值」，而同一业务对象在 E 的另一份拷贝
    # 里其实带着值 —— 这是假阳性，会把决策依据的噪声放大。
    # 这里采用「确定掉值」口径：D 侧任一版本有值、且 E 侧该业务对象的所有版本
    # 都无值，才计一次损失。
    common = ek.isin(dk)
    loss_map = {}
    logic_common_rows = 0
    if len(e) > 0 and len(d) > 0:
        val_cols = [c for c in dcols if c in ecols and c not in keys]
        if val_cols:
            lkc = date_cols_of(lkeys)
            dlf = canon_key_frame(d, lkeys, lkc).reset_index(drop=True)
            elf = canon_key_frame(e, lkeys, lkc).reset_index(drop=True)
            d_has = pd.DataFrame(
                {c: d[c].notna().to_numpy() for c in val_cols})
            e_has = pd.DataFrame(
                {c: e[c].notna().to_numpy() for c in val_cols})

            dagg = pd.concat([dlf, d_has], axis=1).groupby(
                lkeys, sort=False).any().reset_index()
            eagg = pd.concat([elf, e_has], axis=1).groupby(
                lkeys, sort=False).any().reset_index()

            j = dagg.merge(eagg, on=lkeys, how='inner',
                           suffixes=('_d', '_e'))
            logic_common_rows = int(len(j))
            for c in val_cols:
                if c + '_d' not in j.columns or c + '_e' not in j.columns:
                    continue
                lost = int((j[c + '_d'] & (~j[c + '_e'])).sum())
                if lost > 0:
                    loss_map[c] = lost

    st['logic_common_objects'] = logic_common_rows
    st['cell_loss_total'] = int(sum(loss_map.values()))
    st['cell_loss_cols'] = len(loss_map)
    st['cell_loss_basis'] = 'LOGIC_KEY_ANY_VERSION'
    st['cell_loss_top'] = [[c, int(n)] for c, n in
                           sorted(loss_map.items(), key=lambda kv: -kv[1])[:25]]

    crit = {}
    for c in DOWNSTREAM_CRITICAL.get(fname, []):
        if c not in ecols:
            crit[c] = dict(kind='E_MISSING_COL')
        elif c in loss_map:
            crit[c] = dict(kind='LOST', cells=loss_map[c])
    st['downstream_critical'] = crit

    if st['d_only_rows'] > 0:
        dd = dkey.loc[d_only_mask, keys].head(8)
        st['d_only_sample'] = [
            {k: str(v) for k, v in rec.items()} for rec in dd.to_dict('records')]

    print('  D=%d 行 / E=%d 行 / 共同键=%d'
          % (st['d_rows'], st['e_rows'], st['common_keys']))
    print('  D 独有（严格替换将删除）=%d 行   D 自身重复=%d  E 自身重复=%d'
          % (st['d_only_rows'], st['d_dup_rows'], st['e_dup_rows']))
    print('  E 按 LOGIC KEY 多版本重复 = %d 行' % st['e_logic_dup_rows'])
    print('  确定掉值(LOGIC KEY 任一版本口径) = %d 个单元格，涉及 %d 列'
          % (st['cell_loss_total'], st['cell_loss_cols']))
    for c, n in st['cell_loss_top'][:8]:
        print('     %-26s 掉值 %d' % (c, n))
    if crit:
        print('  !! 命中下游真实消费字段：')
        for c, v in sorted(crit.items()):
            if v['kind'] == 'LOST':
                print('     %-26s 掉值 %d 个单元格（因子研究会受影响）' % (c, v['cells']))
            else:
                print('     %-26s E 无此列' % c)
    print('  E 独有列=%s  D 独有列=%s'
          % (st['e_only_cols'] or '无', st['d_only_cols'] or '无'))
    st['status'] = 'PLAN_OK'
    return st


def preflight_generic(spec, opts):
    """非财务非日线表：按各自键做 D 独有行 / 重复 / 列差异统计。"""
    name = spec['name']
    d_path, e_path = spec['d_path'], spec['e_path']
    keys = spec['keys']
    st = dict(name=name, kind='FULL', group=spec['group'], status='PENDING',
              error='', d_path=d_path, e_path=e_path, keys=keys)
    print('')
    print('--- [FULL] %s' % name)

    d = to_flat(pd.read_parquet(d_path), keys)
    e = to_flat(pd.read_parquet(e_path), keys)
    st['d_rows'], st['e_rows'] = int(len(d)), int(len(e))
    dcols, ecols = list(d.columns), list(e.columns)
    st['e_only_cols'] = [c for c in ecols if c not in dcols]
    st['d_only_cols'] = [c for c in dcols if c not in ecols]

    miss = [k for k in keys if k not in d.columns or k not in e.columns]
    if miss:
        st['d_only_rows'] = -1
        st['note'] = '键列不全，无法比键: %s' % miss
        print('  !! %s' % st['note'])
        st['status'] = 'PLAN_OK'
        return st

    dc = date_cols_of(keys, extra=PIT_FIELDS)
    dk = pd.MultiIndex.from_frame(canon_key_frame(d, keys, dc))
    ek = pd.MultiIndex.from_frame(canon_key_frame(e, keys, dc))
    d_only = ~dk.isin(ek)
    st['d_only_rows'] = int(d_only.sum())
    st['e_only_rows'] = int((~ek.isin(dk)).sum())
    st['d_dup_rows'] = int(len(dk) - len(dk.unique()))
    st['e_dup_rows'] = int(len(ek) - len(ek.unique()))
    st['common_keys'] = int(len(dk) - st['d_only_rows'])
    st['cell_loss_total'] = 0
    st['cell_loss_cols'] = 0

    if st['d_only_rows'] > 0:
        st['d_only_sample'] = [
            {k: str(v) for k, v in rec.items()}
            for rec in dkey_sample(d, d_only, keys)]
    print('  D=%d 行 / E=%d 行 / 共同键=%d'
          % (st['d_rows'], st['e_rows'], st['common_keys']))
    print('  D 独有（严格替换将删除）=%d 行  D 重复=%d  E 重复=%d'
          % (st['d_only_rows'], st['d_dup_rows'], st['e_dup_rows']))
    print('  E 独有列=%s  D 独有列=%s'
          % (st['e_only_cols'] or '无', st['d_only_cols'] or '无'))

    # MultiIndex 表写后校验要求主键唯一。与其替换完 14 张表才发现重复、
    # 再整体回滚（一次回滚就是十几 GB 的 SHA 校验 + 还原），不如预检阶段
    # 就把「写进去必然违约」的情况拦下来。
    if spec.get('index_mode') == 'multiindex' and st['e_dup_rows'] > 0:
        st['status'] = 'FAILED'
        st['error'] = ('E 主键重复 %d 行，写后校验必然失败（索引契约要求唯一）'
                       % st['e_dup_rows'])
        print('  !! %s' % st['error'])
        return st

    st['status'] = 'PLAN_OK'
    return st


def dkey_sample(d, mask, keys, n=8):
    sub = canon_key_frame(d, keys, date_cols_of(keys)).loc[mask, keys].head(n)
    return [{k: str(v) for k, v in rec.items()} for rec in sub.to_dict('records')]


# ---------------------------------------------------------------- 写后复核

def assert_daily_contract(path, expect_rows=None, expect_cols=None):
    """完整重读复核日线契约（旧脚本只读 row_group(0)，无法证明全表）。"""
    t = pq.ParquetFile(path).read().to_pandas()
    if list(t.index.names) != DAILY_KEYS:
        raise RuntimeError('日线索引契约破坏: index.names=%s，期望 %s'
                           % (list(t.index.names), DAILY_KEYS))
    ldt = t.index.get_level_values('trade_date')
    if str(ldt.dtype) != 'datetime64[ns]':
        raise RuntimeError('trade_date 索引 dtype 非 datetime64[ns]: %s' % ldt.dtype)
    missing = [c for c in DAILY_VALUE_COLS if c not in t.columns]
    if missing:
        raise RuntimeError('日线值列缺失: %s' % missing)
    if not t.index.is_unique:
        raise RuntimeError('日线主键重复 %d 行' % int(t.index.duplicated().sum()))
    rows = arrow_rows(path)
    if expect_rows is not None and rows != expect_rows:
        raise RuntimeError('日线写后行数不符: %d != %d' % (rows, expect_rows))
    return rows, [str(ldt.min()), str(ldt.max())]


def assert_flat_contract(path, expect_rows, keys, index_mode, expect_cols=None):
    """非日线表写后复核：行数 / 索引形态 / 键存在性 / **全表**主键唯一性。

    旧实现只读 read_row_group(0) —— 那只能证明第一个 row group 正常，
    写坏在中后段的表会被漏过。这里改为完整重读并做全表唯一性校验。
    """
    rows = arrow_rows(path)
    if expect_rows is not None and rows != expect_rows:
        raise RuntimeError('写后行数不符: %d != %d' % (rows, expect_rows))
    t = pq.ParquetFile(path).read().to_pandas()
    if index_mode == 'multiindex':
        if list(t.index.names) != list(keys):
            raise RuntimeError('索引契约破坏: %s，期望 %s'
                               % (list(t.index.names), list(keys)))
        if not t.index.is_unique:
            raise RuntimeError('写后主键重复 %d 行' % int(t.index.duplicated().sum()))
    else:
        names = list(getattr(t.index, 'names', []) or [])
        if len(names) != 1 or names[0] is not None:
            raise RuntimeError('期望 RangeIndex，实际: %s' % names)
        miss = [k for k in keys if k not in t.columns]
        if miss:
            raise RuntimeError('键列缺失: %s' % miss)
    if expect_cols is not None:
        miss_c = [c for c in expect_cols if c not in t.columns]
        if miss_c:
            raise RuntimeError('写后列缺失: %s' % miss_c)
    return rows


# ---------------------------------------------------------------- 事务

def atomic_write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_txn():
    if os.path.isfile(TXN_LOG):
        try:
            with open(TXN_LOG, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'run_tag': None, 'applied': [], 'incr': []}


def save_txn(txn):
    atomic_write_json(TXN_LOG, txn)


def make_backup(d_path, bak_path, run_tag):
    """备份 + SHA-256 双向校验（原目标 vs 备份）。"""
    if os.path.isfile(bak_path):
        raise RuntimeError('备份已存在，拒绝覆盖（换 --run-tag）: %s' % bak_path)
    src_sha = sha256_of(d_path)
    src_bytes = os.path.getsize(d_path)
    btmp = bak_path + '.tmp_%s' % run_tag
    if os.path.exists(btmp):
        os.remove(btmp)
    shutil.copy2(d_path, btmp)
    if os.path.getsize(btmp) != src_bytes:
        os.remove(btmp)
        raise RuntimeError('备份字节数校验失败: %s' % bak_path)
    bak_sha = sha256_of(btmp)
    if bak_sha != src_sha:
        os.remove(btmp)
        raise RuntimeError('备份 SHA-256 与原目标不一致: %s' % bak_path)
    os.replace(btmp, bak_path)
    return dict(bak=os.path.basename(bak_path), bak_sha256=bak_sha, bak_bytes=src_bytes,
                src_sha256=src_sha)


def stage_and_replace(df, d_path, bak_path, run_tag, verify, txn_entry, txn):
    """staging -> 备份(SHA 校验) -> 登记事务 -> os.replace -> 写后复核。

    关键顺序：**在 os.replace 之前**就把本表登记进 txn.applied 并落盘事务
    日志。这样「已替换但复核失败」也能被回滚覆盖（旧脚本在复核后才 append，
    导致当前表逃逸出回滚集合）。
    """
    staging = d_path + '.staging_%s' % run_tag
    if os.path.exists(staging):
        os.remove(staging)
    df.to_parquet(staging, engine='pyarrow')

    try:
        bak = make_backup(d_path, bak_path, run_tag)
    except Exception:
        if os.path.exists(staging):
            os.remove(staging)
        raise

    txn_entry['bak_sha256'] = bak['bak_sha256']
    txn_entry['bak_bytes'] = bak['bak_bytes']
    txn_entry['src_sha256'] = bak['src_sha256']
    txn_entry['phase'] = 'REPLACED_PENDING_VERIFY'
    if txn is not None:
        save_txn(txn)

    os.replace(staging, d_path)

    info = verify(d_path)
    txn_entry['phase'] = 'VERIFIED'
    return info, bak


def rollback(applied, incr=None):
    """逆序回滚；恢复同样走 staging + os.replace，不用 copy2 就地覆盖。

    返回 (ok_count, failures)。**失败必须如实上报**：调用方据此决定能否清空
    事务记录。若回滚没做完就把 txn 清空，等于销毁唯一的重试依据 ——
    生产文件停在半迁移状态且再也无法自动还原。
    """
    print('')
    print('!! [ROLLBACK] 开始回滚 %d 张表' % len(applied))
    ok = 0
    failures = []
    # 只有真正替换过目标文件的阶段才需要还原。
    #   PREPARED                 = 已登记事务，但备份/replace 都还没发生
    #   REPLACED_PENDING_VERIFY  = 已 os.replace，复核未过或后续表中止
    #   VERIFIED                 = 已替换且复核通过，但本轮整体中止
    # PREPARED 阶段目标文件仍是原版，若也去 os.replace 备份，
    # 会因「备份从未创建」报 BACKUP_MISSING —— 假故障，且会让
    # close_txn_if_clean 永远清不掉事务记录，把一次干净的空跑
    # 变成需要人工干预的悬挂状态。
    needs_restore = ('REPLACED_PENDING_VERIFY', 'VERIFIED')
    for item in reversed(applied):
        phase = item.get('phase')
        if phase is None:
            # 兼容早期无 phase 字段的事务日志：以是否带备份 SHA 判定
            phase = ('REPLACED_PENDING_VERIFY'
                     if item.get('bak_sha256') else 'PREPARED')
        if phase not in needs_restore:
            print('   跳过未替换表（phase=%s）: %s' % (phase, item['name']))
            continue
        bak, tgt = item['bak_path'], item['d_path']
        if not os.path.isfile(bak):
            print('   !! 备份缺失，无法还原: %s' % item['name'])
            failures.append((item['name'], 'BACKUP_MISSING:%s' % bak))
            continue
        rtmp = tgt + '.rollback_%s' % item.get('run_tag', 'x')
        try:
            if os.path.exists(rtmp):
                os.remove(rtmp)
            shutil.copy2(bak, rtmp)
            if sha256_of(rtmp) != item.get('bak_sha256'):
                os.remove(rtmp)
                print('   !! 回滚副本 SHA 不匹配，已放弃还原: %s' % item['name'])
                failures.append((item['name'], 'ROLLBACK_SHA_MISMATCH'))
                continue
            os.replace(rtmp, tgt)
            print('   已还原 %s  <- %s' % (item['name'], os.path.basename(bak)))
            ok += 1
        except Exception as exc:
            if os.path.exists(rtmp):
                try:
                    os.remove(rtmp)
                except Exception:
                    pass
            print('   !! 还原异常 %s: %s' % (item['name'], exc))
            failures.append((item['name'], 'ROLLBACK_ERROR:%s' % exc))
    for rec in (incr or []):
        # 归档文件回滚即删除。删之前必须确认它确实是我们写进去的那一份：
        # 若 sha 不符，说明该路径已被其它程序占用/改写，误删会毁掉别人的数据。
        dst = rec.get('dst')
        want = rec.get('sha256')
        if not dst or not os.path.isfile(dst):
            continue
        if want:
            try:
                cur = sha256_of(dst)
            except Exception as exc:
                failures.append((rec.get('name', 'incr'), 'INCR_SHA_READ_FAIL:%s' % exc))
                continue
            if cur != want:
                print('   !! 增量归档内容已变，拒绝删除: %s' % dst)
                failures.append((rec.get('name', 'incr'), 'INCR_SHA_CHANGED'))
                continue
        try:
            os.remove(dst)
            print('   已撤销增量归档 %s' % os.path.basename(dst))
            ok += 1
        except Exception as exc:
            print('   !! 增量归档撤销失败 %s: %s' % (dst, exc))
            failures.append((rec.get('name', 'incr'), 'INCR_REMOVE_FAIL:%s' % exc))
    if failures:
        print('   !! 回滚未完成：%d 项失败，事务记录保留待重试。' % len(failures))
    else:
        print('   回滚完成。')
    return ok, failures


def close_txn_if_clean(txn, failures, phase):
    """只有回滚全绿才清空 applied/incr；否则保留记录供 --rollback-only 重试。"""
    if failures:
        txn['rollback_incomplete'] = [
            [str(n), str(r)] for n, r in failures]
        save_txn(txn)
        return False
    txn['applied'] = []
    txn['incr'] = []
    txn[phase] = datetime.now().isoformat(timespec='seconds')
    txn.pop('rollback_incomplete', None)
    save_txn(txn)
    return True


def sweep_temp(d_path, run_tag, phases=('staging', 'rollback')):
    """清理上次异常中断残留的临时文件（只碰本脚本自己的命名模式）。"""
    for tag in run_tag if isinstance(run_tag, (list, tuple)) else [run_tag]:
        for ph in phases:
            for suf in ('.staging_%s', '.rollback_%s'):
                p = d_path + (suf % tag)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        print('   已清理残留 %s' % os.path.basename(p))
                    except Exception as exc:
                        print('   !! 残留清理失败 %s: %s' % (p, exc))


def do_rollback_only():
    txn = load_txn()
    if not txn.get('applied') and not txn.get('incr'):
        print('无已记录的事务，无需回滚。')
        return 0
    _, failures = rollback(txn.get('applied', []), txn.get('incr', []))
    if close_txn_if_clean(txn, failures, 'rolled_back'):
        return 0
    print('')
    print('!! 事务记录已保留在 %s，修复后重跑 --rollback-only。' % TXN_LOG)
    return 3


# ---------------------------------------------------------------- 计划指纹

def source_identity(results):
    """为每个源/目标文件登记身份（字节数 + mtime），供指纹绑定。

    为什么不用 SHA-256：E FULL 日线 1400 万行，全量哈希十几 GB 会让预检从
    分钟级掉到小时级。字节数 + mtime 足以在「源文件被换过」时改变指纹，
    而真正的内容完整性由写入阶段的备份 SHA-256 双向校验把关。
    """
    ident = []
    for r in results:
        for side in ('e_path', 'd_path'):
            p = r.get(side)
            if not p or not os.path.isfile(p):
                continue
            ident.append([p, os.path.getsize(p),
                          int(os.path.getmtime(p))])
    ident.sort()
    return ident


def plan_fingerprint(plan):
    """把不可逆后果哈希成短指纹；计划变则指纹变，令牌即失效。

    指纹必须绑定三样东西，缺一即形同虚设：
      1. 不可逆后果（删多少行 / 掉多少值 / 缺多少天 / 空间够不够）；
      2. **语义开关**（--keep-d-only 等）—— 否则用户承认的是「严格替换」的
         后果，实际执行的却是开了偏离开关的另一个语义，令牌张冠李戴；
      3. **源文件身份** —— 否则 E 盘文件被换过，用户凭旧指纹授权了新内容；
      4. **本轮表清单** —— 否则「只迁 6 张」的授权会被拿去执行「迁 14 张」。
    """
    payload = dict(
        tables=sorted(plan.get('selected_tables') or []),
        delete_rows=plan['totals']['d_only_rows'],
        gap_rows=plan['totals']['gap_rows'],
        cell_loss=plan['totals']['cell_loss_total'],
        e_dup=plan['totals']['e_dup_rows'],
        logic_dup=plan['totals']['e_logic_dup_rows'],
        null_days=sorted(plan['totals']['null_day_loss'].keys()),
        free_gb=round(plan['space']['free_gb'], 2),
        deviations=sorted(plan.get('strict_deviations') or []),
        sources=plan.get('source_identity') or [],
    )
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16], payload


# ---------------------------------------------------------------- 写入

def write_daily(spec, opts, tag, bak_path, txn_entry, txn):
    e_path, d_path = spec['e_path'], spec['d_path']
    e = pd.read_parquet(e_path)
    if 'index' in e.columns and 'index' not in arrow_names(e_path):
        e = e.drop(columns=['index'])
    for c in DAILY_KEYS:
        if c not in e.columns:
            raise RuntimeError('E 缺日线键列: %s' % c)
    e['trade_date'] = pd.to_datetime(e['trade_date'], errors='coerce')
    if e['trade_date'].isna().any():
        raise RuntimeError('E trade_date 无法解析: %d 行' % int(e['trade_date'].isna().sum()))
    e = e.set_index(DAILY_KEYS).sort_index()

    if opts.backfill_gap_from_d or opts.backfill_isst_from_d:
        e = backfill_daily(e, d_path, opts)

    e = e[DAILY_VALUE_COLS]
    if not e.index.is_unique:
        raise RuntimeError('日线主键重复 %d 行' % int(e.index.duplicated().sum()))

    def verify(p):
        return assert_daily_contract(p, expect_rows=int(len(e)))

    info, bak = stage_and_replace(e, d_path, bak_path, tag, verify, txn_entry, txn)
    return info[0], bak, dict(range=info[1])


def backfill_daily(e, d_path, opts):
    """从 D 回补：整段历史交易日 + is_st/listed_days 整日缺失（均为 opt-in）。"""
    print('  [补洞] 启用：从 D 主库回补')
    if opts.backfill_gap_from_d:
        extra = []
        for dt in DAILY_GAP_DATES:
            sub = read_filtered(d_path, DAILY_DATE_COL, dt, dt)
            if len(sub) == 0:
                continue
            sub = to_flat(sub, DAILY_KEYS)
            sub['trade_date'] = pd.to_datetime(sub['trade_date'])
            sub = sub.set_index(DAILY_KEYS).sort_index()
            sub = sub[~sub.index.isin(e.index)]
            if len(sub):
                extra.append(sub[DAILY_VALUE_COLS])
        if extra:
            add = pd.concat(extra)
            e = pd.concat([e, add]).sort_index()
            print('     补回历史交易日 %d 行' % len(add))

    if opts.backfill_isst_from_d:
        ed = pads.dataset(d_path, format='parquet')
        cols = [c for c in DAILY_VALUE_COLS if c in ed.schema.names]
        # 必须投影键列全集：只取 ts_code 会让 to_pandas 无法复原
        # (trade_date, ts_code) 索引层，键列随之丢失，补洞直接抛错。
        proj = [DAILY_DATE_COL] + [c for c in cols if c != DAILY_DATE_COL] + ['ts_code']
        sub = ed.to_table(columns=proj,
                          filter=pc.field(DAILY_DATE_COL) >= pd.Timestamp('20260101')).to_pandas()
        sub = to_flat(sub, DAILY_KEYS)
        sub['trade_date'] = pd.to_datetime(sub['trade_date'])
        sub = sub.set_index(DAILY_KEYS).sort_index()
        common = sub.index.intersection(e.index)
        if len(common):
            filled = 0
            tgt = e.loc[common].copy()
            for c in DAILY_NULL_COLS:
                if c not in tgt.columns or c not in sub.columns:
                    continue
                need = tgt[c].isna()
                if not need.any():
                    continue
                src = sub.loc[common, c]
                tgt.loc[need, c] = src[need]
                filled += int(need.sum())
            if filled:
                e.loc[common, DAILY_NULL_COLS] = tgt[DAILY_NULL_COLS]
                print('     补回 %s 空单元 %d 个' % ('/'.join(DAILY_NULL_COLS), filled))
    if not e.index.is_unique:
        raise RuntimeError('补洞后日线主键重复，停止')
    return e


def read_filtered(path, date_col, lo, hi, columns=None):
    dset = pads.dataset(path, format='parquet')
    sch = dset.schema
    cols = [c for c in columns if c in sch.names] if columns else None
    f = pc.field(date_col)
    tbl = dset.to_table(columns=cols,
                        filter=(f >= pd.Timestamp(lo))
                               & (f <= pd.Timestamp(hi)))
    return tbl.to_pandas()


def day_count(dset, date_col, day):
    """统计某个交易日的行数，用列式投影避免把整表读进内存。

    过滤表达式必须用 pyarrow.compute.field()：pyarrow 25 的 Dataset 上
    已无 .field() 方法（AttributeError），而 dset.schema.field(name) 返回的
    是元数据 Field 对象，跟 Timestamp 比较会报类型不支持。
    """
    f = pc.field(date_col)
    lo = hi = pd.Timestamp(day)
    return int(dset.to_table(columns=[date_col],
                             filter=(f >= lo) & (f <= hi)).num_rows)


def pick_version(df, lkeys):
    """财务多版本裁决（仅 --dedupe-fin 启用）：update_flag 大 -> f_ann_date 晚。"""
    out = df
    if 'update_flag' in out.columns:
        uf = pd.to_numeric(out['update_flag'], errors='coerce').fillna(-1)
    else:
        uf = pd.Series(0.0, index=out.index)
    if 'f_ann_date' in out.columns:
        fa = canon_date(out['f_ann_date'])
        fa = pd.to_numeric(fa.where(fa != 'NA', 0), errors='coerce').fillna(0)
    else:
        fa = pd.Series(0.0, index=out.index)
    out = out.assign(_uf=uf, _fa=fa)
    out = out.sort_values(['_uf', '_fa'], ascending=[False, False], kind='mergesort')
    return out.drop_duplicates(subset=lkeys, keep='first').drop(columns=['_uf', '_fa'])


def write_fin(spec, opts, tag, bak_path, txn_entry, txn):
    e_path, d_path = spec['e_path'], spec['d_path']
    e = to_flat(pd.read_parquet(e_path), spec['keys'])
    if opts.dedupe_fin:
        before = len(e)
        e = pick_version(e, spec['logic_keys'])
        print('  [裁决] 多版本去重 %d -> %d 行' % (before, len(e)))
    if opts.keep_d_only:
        e = merge_d_only(e, d_path, spec)
    if opts.backfill_common_cells:
        e, filled = backfill_common_cells(e, d_path, spec)
        print('  [回填] 共同键空单元回填 %d 个' % filled)
    e = e.sort_values(spec['keys'], kind='mergesort').reset_index(drop=True)
    expect = int(len(e))
    expect_cols = list(e.columns)

    def verify(p):
        return assert_flat_contract(p, expect, spec['keys'],
                                    spec['index_mode'], expect_cols)

    info, bak = stage_and_replace(e, d_path, bak_path, tag, verify, txn_entry, txn)
    return info, bak


def merge_d_only(e, d_path, spec):
    """opt-in：保留 D 独有行（改变严格整体替换语义）。"""
    d = to_flat(pd.read_parquet(d_path), spec['keys'])
    dc = date_cols_of(spec['keys'], extra=PIT_FIELDS)
    dk = pd.MultiIndex.from_frame(canon_key_frame(d, spec['keys'], dc))
    ek = pd.MultiIndex.from_frame(canon_key_frame(e, spec['keys'], dc))
    extra = d.loc[~dk.isin(ek)]
    print('  [并集] 保留 D 独有行 %d 行' % len(extra))
    if len(extra) == 0:
        return e
    common = [c for c in e.columns if c in extra.columns]
    return pd.concat([e, extra[common]], ignore_index=True)


def backfill_common_cells(e, d_path, spec):
    """opt-in：E 基表共同键上的空单元用 D 回填（E 为基表，只填空不改值）。"""
    import numpy as np
    keys = spec['keys']
    d = to_flat(pd.read_parquet(d_path), keys)
    dc = date_cols_of(keys, extra=PIT_FIELDS)
    dk = pd.MultiIndex.from_frame(canon_key_frame(d, keys, dc))
    ek = pd.MultiIndex.from_frame(canon_key_frame(e, keys, dc))
    common = ek.isin(dk)
    if not common.any():
        return e, 0
    idx_keys = pd.MultiIndex.from_frame(canon_key_frame(e.loc[common], keys, dc))
    dsub = d.set_index(dk)
    filled = 0
    for c in [x for x in d.columns if x in e.columns and x not in keys]:
        need = e.loc[common, c].isna().to_numpy()
        n = int(need.sum())
        if n == 0:
            continue
        src = dsub[c].reindex(idx_keys)
        tgt = e.loc[common, c].to_numpy().copy()
        ok = need & pd.notna(src.to_numpy())
        if not ok.any():
            continue
        tgt[ok] = src.to_numpy()[ok]
        e.loc[common, c] = tgt
        filled += int(ok.sum())
    return e, filled


def write_generic(spec, opts, tag, bak_path, txn_entry, txn):
    e_path, d_path = spec['e_path'], spec['d_path']
    e = to_flat(pd.read_parquet(e_path), spec['keys'])
    if spec['index_mode'] == 'multiindex':
        for c in spec['keys']:
            if c not in e.columns:
                raise RuntimeError('E 缺键列: %s' % c)
        e = e.set_index(spec['keys']).sort_index()
        expect = int(len(e))
        # 写后要按原契约重建同形态索引，所以列清单必须是「除键列外的值列」
        expect_cols = [c for c in e.columns]

        def verify(p):
            return assert_flat_contract(p, expect, spec['keys'],
                                        'multiindex', expect_cols)
    else:
        order = [k for k in spec['keys'] if k in e.columns]
        e = e.sort_values(order, kind='mergesort').reset_index(drop=True) if order else e
        expect = int(len(e))
        expect_cols = list(e.columns)

        def verify(p):
            return assert_flat_contract(p, expect, spec['keys'],
                                        'range', expect_cols)

    info, bak = stage_and_replace(e, d_path, bak_path, tag, verify, txn_entry, txn)
    return info, bak


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description='E:\\temp\\行情数据 -> D:\\astock 全量整体迁移 / 增量归档（严格语义版）')
    ap.add_argument('--apply', action='store_true', help='真正写盘（默认 dry-run）')
    ap.add_argument('--run-tag', default=time.strftime('%Y%m%d'), help='备份标识（数字）')
    ap.add_argument('--accept-plan', default=None,
                    help='精确承认本次计划指纹（dry-run 报告末尾给出）')
    ap.add_argument('--rollback-only', action='store_true', help='仅回滚上一轮事务')
    ap.add_argument('--only', default=None, help='仅处理名字含该子串的表（调试）')
    ap.add_argument('--tables', default=None,
                    help='逗号分隔的表名白名单（精确名，如 '
                         'daily/stock_daily,board/ths_daily）；缺省=全部 14 张')
    # 以下全部为「改变严格整体替换语义」的显式 opt-in，默认关闭
    ap.add_argument('--keep-d-only', action='store_true',
                    help='[opt-in] 保留 D 独有行（不再是纯整体替换）')
    ap.add_argument('--backfill-common-cells', action='store_true',
                    help='[opt-in] E 共同键空单元用 D 回填')
    ap.add_argument('--dedupe-fin', action='store_true',
                    help='[opt-in] 财务多版本按 update_flag/f_ann_date 裁决去重')
    ap.add_argument('--backfill-gap-from-d', action='store_true',
                    help='[opt-in] 用 D 补回 E 缺失的历史交易日')
    ap.add_argument('--backfill-isst-from-d', action='store_true',
                    help='[opt-in] 用 D 补回 is_st/listed_days 整日缺失')
    opts = ap.parse_args()

    if opts.rollback_only:
        return do_rollback_only()

    tag = ''.join(ch for ch in opts.run_tag if ch.isdigit())
    if not tag:
        raise SystemExit('--run-tag 需为数字日期')

    print('pandas %s / pyarrow %s' % (pd.__version__, pa.__version__))
    print('run-tag=%s  模式=%s' % (tag, 'APPLY' if opts.apply else 'DRY-RUN'))
    strict_violations = [n for n, v in (
        ('--keep-d-only', opts.keep_d_only),
        ('--backfill-common-cells', opts.backfill_common_cells),
        ('--dedupe-fin', opts.dedupe_fin),
        ('--backfill-gap-from-d', opts.backfill_gap_from_d),
        ('--backfill-isst-from-d', opts.backfill_isst_from_d)) if v]
    print('严格语义偏离开关: %s' % (', '.join(strict_violations) or '无（纯整体替换）'))

    specs = full_manifest()
    incrs = incr_manifest()
    if opts.only:
        specs = [s for s in specs if opts.only in s['name']]
        incrs = [s for s in incrs if opts.only in s['name']]
    if opts.tables:
        want = set(x.strip() for x in opts.tables.split(',') if x.strip())
        allnames = set(s['name'] for s in specs) | set(s['name'] for s in incrs)
        unknown = sorted(want - allnames)
        if unknown:
            raise SystemExit('--tables 含未知表名: %s' % unknown)
        specs = [s for s in specs if s['name'] in want]
        incrs = [s for s in incrs if s['name'] in want]
        if not specs and not incrs:
            raise SystemExit('--tables 未选中任何表')
    selected = sorted([s['name'] for s in specs] + [s['name'] for s in incrs])
    print('本轮表清单(%d): %s' % (len(selected), ', '.join(selected)))

    hr('阶段一：INCR 文件预检（只归档，不进主库）')
    incr_results = []
    for isp in incrs:
        try:
            incr_results.append(preflight_incr(isp))
        except Exception as exc:
            incr_results.append(dict(name=isp['name'], kind='INCR', status='FAILED',
                                     error='%s: %s' % (type(exc).__name__, exc)))
            print('   !! %s' % exc)

    hr('阶段二：FULL 表预检（表级路由，严格整体替换）')
    full_results = []
    for spec in specs:
        try:
            if spec['group'] == 'daily':
                full_results.append(preflight_daily(spec, opts))
            elif spec['group'] == 'finance':
                full_results.append(preflight_fin(spec, opts))
            else:
                full_results.append(preflight_generic(spec, opts))
        except Exception as exc:
            full_results.append(dict(name=spec['name'], kind='FULL',
                                     group=spec['group'], status='FAILED',
                                     error='%s: %s' % (type(exc).__name__, exc)))
            print('   !! %s' % exc)

    fin_r = [r for r in full_results if r.get('group') == 'finance']
    daily_r = [r for r in full_results if r.get('group') == 'daily']
    gen_r = [r for r in full_results if r.get('group') not in ('finance', 'daily', None)]

    null_day_loss = {}
    for r in daily_r:
        null_day_loss.update(r.get('null_day_loss', {}))

    totals = dict(
        d_only_rows=sum(max(r.get('d_only_rows', 0), 0) for r in full_results),
        gap_rows=sum(r.get('gap_rows', 0) for r in daily_r),
        cell_loss_total=sum(r.get('cell_loss_total', 0) for r in fin_r),
        cell_loss_cols=sum(r.get('cell_loss_cols', 0) for r in fin_r),
        e_dup_rows=sum(r.get('e_dup_rows', 0) for r in full_results),
        e_logic_dup_rows=sum(r.get('e_logic_dup_rows', 0) for r in fin_r),
        null_day_loss=null_day_loss,
    )

    need_bytes = 0
    for r in full_results:
        if r.get('status') != 'PLAN_OK':
            continue
        need_bytes += os.path.getsize(r['e_path']) + os.path.getsize(r['d_path'])
    for r in incr_results:
        if r.get('status') == 'PLAN_OK':
            need_bytes += r.get('bytes', 0)
    space = dict(free_gb=free_gb(D_BASE),
                 need_gb=need_bytes / (1024.0 ** 3),
                 note='staging(E 体积) + 备份(原 D 体积) 同卷并放')

    plan = dict(generated=datetime.now().isoformat(timespec='seconds'),
                run_tag=tag, mode='APPLY' if opts.apply else 'DRY-RUN',
                strict_deviations=strict_violations,
                selected_tables=selected,
                source_identity=source_identity(full_results + incr_results),
                totals=totals, space=space,
                full=full_results, incr=incr_results)
    fp, fp_payload = plan_fingerprint(plan)
    plan['plan_fingerprint'] = fp
    plan['fingerprint_payload'] = fp_payload

    hr('阶段三：不可逆后果汇总')
    print('  将被删除的 D 独有行合计   = %d' % totals['d_only_rows'])
    print('  其中日线五日缺口行       = %d' % totals['gap_rows'])
    print('  财务确定掉值单元格       = %d（涉及 %d 列，LOGIC KEY 任一版本口径）'
          % (totals['cell_loss_total'], totals['cell_loss_cols']))
    print('  E 自身 FULL KEY 重复行   = %d' % totals['e_dup_rows'])
    print('  E 自身 LOGIC KEY 多版本  = %d' % totals['e_logic_dup_rows'])
    print('  E 中 %s 整日缺失的日期   = %s'
          % ('/'.join(DAILY_NULL_COLS), sorted(null_day_loss.keys()) or '无'))
    print('  D 卷剩余空间             = %.1f GB / 本轮需 %.1f GB'
          % (space['free_gb'], space['need_gb']))
    print('  计划指纹 PLAN_FP         = %s' % fp)

    crit_hits = []
    for r in fin_r:
        for c, v in sorted(r.get('downstream_critical', {}).items()):
            if v['kind'] == 'LOST':
                crit_hits.append('%s/%s(-%d)' % (r['name'].split('/')[-1], c, v['cells']))
    if crit_hits:
        print('')
        print('  !! 命中下游因子真实消费字段（迁移后这些因子取值会变化）：')
        for x in crit_hits[:20]:
            print('       %s' % x)

    atomic_write_json(REPORT_JSON, plan)
    print('')
    print('  计划已落盘: %s' % REPORT_JSON)

    failed = [r for r in full_results + incr_results if r['status'] == 'FAILED']
    blockers = []
    if failed:
        blockers.append('存在预检失败表: %s' % [r['name'] for r in failed])
    if space['free_gb'] < space['need_gb'] * 1.1:
        blockers.append('磁盘空间不足: 剩余 %.1fGB < 需 %.1fGB'
                        % (space['free_gb'], space['need_gb']))
    if opts.accept_plan != fp:
        blockers.append('未承认本次计划指纹（当前 %s，需 --accept-plan %s）' % (fp, fp))
    if totals['gap_rows'] > 0 and not opts.backfill_gap_from_d:
        print('')
        print('  提醒：E 缺 %d 行历史交易日，严格替换会形成连续 5 日空洞；'
              '如需保留请加 --backfill-gap-from-d' % totals['gap_rows'])

    if blockers:
        hr('阻断：未获精确确认，拒绝写盘')
        for b in blockers:
            print('  !! %s' % b)
        print('')
        print('  D:\\astock 未被修改。')

    if not opts.apply:
        hr('DRY-RUN 结束（未写任何文件）')
        print('  认可上述后果后执行：')
        cmd = ['python migrate_astock_full_v2.py', '--apply',
               '--run-tag %s' % tag, '--accept-plan %s' % fp]
        if opts.tables:
            cmd.append("--tables '%s'" % opts.tables)
        cmd.extend(strict_violations)
        print('    %s' % ' '.join(cmd))
        return 0 if not blockers else 2

    if blockers:
        return 2

    hr('阶段四：执行（备份 SHA 校验 -> staging -> 原子替换 -> 写后复核）')
    txn = dict(run_tag=tag, started=datetime.now().isoformat(timespec='seconds'),
               plan_fingerprint=fp, applied=[], incr=[])
    save_txn(txn)
    applied = []
    try:
        for isp in incrs:
            os.makedirs(isp['target_dir'], exist_ok=True)
            sub = pd.read_parquet(isp['e_path'])
            dst = os.path.join(isp['target_dir'], 'stock_daily_incr_%s.parquet' % tag)
            if os.path.isfile(dst):
                raise RuntimeError('增量归档已存在: %s' % dst)
            tmp = dst + '.tmp'
            # 与 FULL 表同一纪律：**先登记事务再落盘**。若在 replace 之后、
            # save_txn 之前崩溃，磁盘上已有归档文件但日志无记录，
            # --rollback-only 将无从撤销，留下无人认领的文件。
            rec = dict(name=isp['name'], src=isp['e_path'], dst=dst,
                       phase='ARCHIVING')
            txn['incr'].append(rec)
            save_txn(txn)
            try:
                sub.to_parquet(tmp, engine='pyarrow')
                os.replace(tmp, dst)
            except Exception:
                # 写临时文件失败：清掉半成品，事务记录留给回滚路径处理
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                raise
            rec.update(phase='ARCHIVED', rows=int(len(sub)),
                       sha256=sha256_of(dst))
            save_txn(txn)
            print('  [INCR] 归档 %d 行 -> %s' % (len(sub), dst))

        for spec in specs:
            t0 = time.time()
            name = spec['name']
            print('')
            print('--- 写入 %s' % name)
            bak_path = spec['d_path'] + '.bak_%s' % tag
            entry = dict(name=name, d_path=spec['d_path'], bak_path=bak_path,
                         run_tag=tag, phase='PREPARED')

            # 先入事务再替换：保证「已替换未复核」也能回滚
            txn['applied'].append(entry)
            applied.append(entry)
            save_txn(txn)

            if spec['group'] == 'daily':
                rows, bak, extra = write_daily(spec, opts, tag, bak_path, entry, txn)
            elif spec['group'] == 'finance':
                rows, bak = write_fin(spec, opts, tag, bak_path, entry, txn)
            else:
                rows, bak = write_generic(spec, opts, tag, bak_path, entry, txn)

            entry['rows'] = int(rows)
            entry.update(bak)
            save_txn(txn)
            # 残留 staging 早已被 os.replace 消费；此处无需 sweep，
            # 仅在异常路径由 rollback 兜底清理同名临时文件。
            print('  OK %s  行数=%d  备份=%s  耗时=%.1fs'
                  % (name, rows, bak['bak'], time.time() - t0))
    except Exception as exc:
        print('')
        print('!! [ABORT] %s: %s' % (type(exc).__name__, exc))
        traceback.print_exc()
        _, failures = rollback(applied, txn['incr'])
        txn['aborted'] = '%s: %s' % (type(exc).__name__, exc)
        if not close_txn_if_clean(txn, failures, 'aborted_rollback_done'):
            print('')
            print('!! 回滚未完全成功，生产文件可能处于半迁移状态。')
            print('!! 事务记录保留在 %s，请人工核对后重跑 --rollback-only。' % TXN_LOG)
            return 3
        return 1

    txn['finished'] = datetime.now().isoformat(timespec='seconds')
    save_txn(txn)
    hr('全部完成')
    print('事务日志: %s（回滚用 --rollback-only）' % TXN_LOG)
    return 0


if __name__ == '__main__':
    sys.exit(main())
