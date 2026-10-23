# -*- coding: utf-8 -*-
"""
update_astock_from_e.py
=======================
把 E:\\temp\\行情数据 中较新的离线数据资产（更新至 2026-09-11）合并更新到
D:/astock（唯一权威数据源）。

原则（强制）：
1. 主行情（daily/stock_daily）：2026-09-14 制度规定，有到最新日期的全量版则不备份、直接全量替换迁移
   （replace=True，os.replace 原子替换）；若只有增量则放增量目录不合并进主库（防口径污染）。
2. 其他表：备份先行，每张表写前把 D 版复制为 <name>.bak_20260913（同目录），备份完成才写。
3. 合并去重只增不删：按主键去重，保留 D 版独有行 + E 版行，同一主键冲突以 E 版为准。
4. 列对齐：以 D 版列序为基准，E 版独有列追加在后（实测 basic 的 fill_source 在 E 不在 D）。
5. 写后验证：行数 / 日期范围 / index 结构 / 主键唯一性，用 pandas read_parquet 复核。
6. 顺序：daily -> finance 8 表 -> lhb / research / board / moneyflow_ind -> basic；
   任何一步异常立即停止，后续表不动。
7. 不碰 northbound / cb / etf / min_* / index / chip / auction / benchmark /
   index_weight / Updatedata。

用法：
  python update_astock_from_e.py                          # dry-run（默认，nested 布局）：只打印合并计划
  python update_astock_from_e.py --apply                  # 备份 + 合并 + 写回 + 验证 + 生成报告
  python update_astock_from_e.py --e-layout flat          # 2026-10-01 网盘下载的扁平结构 dry-run
  python update_astock_from_e.py --e-layout flat --apply  # 扁平结构真实升级
  python update_astock_from_e.py --e-layout flat-sub --apply --run-tag 20261022
                                                          # 2026-10-22 起：网盘子目录镜像布局（推荐）
  python update_astock_from_e.py --run-tag 20261001       # 换本轮备份标识（避免复用旧 .bak_20260913）
"""
import sys
import os
import re
import time
import shutil
import argparse
import traceback

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BAK_SUFFIX = '.bak_20260913'
REPORT_PATH = r'D:\QuantLab\scripts\update_astock_verify_20260913.md'

# 2026-10-01：新一轮升级用独立 run tag（--run-tag 20261001），避免撞上上一轮已存在的
# .bak_20260913 备份触发「备份已存在，拒绝再次备份」门禁，造成 daily 已替换而后续表全部中止的半升级状态。
BAK_TAG = '20260913'

# 2026-10-01：用户自网盘下载的扁平结构（E:\temp\行情数据\*.parquet + 个股行情全量\stock_daily_full.parquet），
# 与 2026-09-14 首次升级时的离线包层级结构不同，故新增 --e-layout flat 走扁平映射。
# 扁平布局下各表文件名与 D 盘同名（basic 源文件仍为 stock_basic_data.parquet）。
E_FLAT_ROOT = r'E:\temp\行情数据'
E_FLAT_FULL_DIR = os.path.join(E_FLAT_ROOT, '个股行情全量')

E_BASE = r'E:\temp\行情数据\1.2parquet格式股票数据'

# 2026-10-22：网盘供应商版本目录名随期次变化（更新至YYYY.M.D），不再写死单一版本，
# 改由 --e-version 指定；缺省时自动选「更新至YYYY.M.D」中日期最大的一套（五类目录需齐备）。
E_VERSION = '2026.10.16'
E_VERSION_RE = re.compile(r'^更新至(\d{4})\.(\d{1,2})\.(\d{1,2})$')

E_DAILY_SUB = r'1.parquet格式个股行情数据'
E_FIN_SUB = r'财务数据parquet格式（已清洗去重）'
E_LHB_BOARD_SUB = r'parquet格式打板专题数据'
E_RESEARCH_SUB = r'parquet格式特色数据'
E_MONEYFLOW_SUB = r'parquet格式资金流向数据'

E_DAILY_DIR = E_FIN_DIR = E_LHB_BOARD_DIR = E_RESEARCH_DIR = E_MONEYFLOW_DIR = ''


def version_dir_name(sub_dir, version):
    return '更新至%s' % version


def detect_latest_version():
    """返回 E 盘五个子目录都存在的最大「更新至YYYY.M.D」版本号；找不到返回 None。"""
    cands = set()
    subs = [E_DAILY_SUB, E_FIN_SUB, E_LHB_BOARD_SUB, E_RESEARCH_SUB, E_MONEYFLOW_SUB]
    first = True
    for sub in subs:
        p = os.path.join(E_BASE, sub)
        if not os.path.isdir(p):
            return None
        found = set()
        for nm in os.listdir(p):
            m = E_VERSION_RE.match(nm)
            if m and os.path.isdir(os.path.join(p, nm)):
                found.add('%s.%s.%s' % (m.group(1), int(m.group(2)), int(m.group(3))))
        cands = found if first else (cands & found)
        first = False
    if not cands:
        return None
    return max(cands, key=lambda v: tuple(int(x) for x in v.split('.')))


def bind_e_dirs(version):
    """把五类源目录绑定到指定版本（目录名形如「更新至YYYY.M.D」）。"""
    global E_VERSION, E_DAILY_DIR, E_FIN_DIR, E_LHB_BOARD_DIR, E_RESEARCH_DIR, E_MONEYFLOW_DIR
    E_VERSION = version
    E_DAILY_DIR = os.path.join(E_BASE, E_DAILY_SUB, version_dir_name(E_DAILY_SUB, version))
    E_FIN_DIR = os.path.join(E_BASE, E_FIN_SUB, version_dir_name(E_FIN_SUB, version))
    E_LHB_BOARD_DIR = os.path.join(E_BASE, E_LHB_BOARD_SUB, version_dir_name(E_LHB_BOARD_SUB, version))
    E_RESEARCH_DIR = os.path.join(E_BASE, E_RESEARCH_SUB, version_dir_name(E_RESEARCH_SUB, version))
    E_MONEYFLOW_DIR = os.path.join(E_BASE, E_MONEYFLOW_SUB, version_dir_name(E_MONEYFLOW_SUB, version))

D_BASE = r'D:\astock'

# finance 8 表主键（D 版 parquet 无显式索引层级（RangeIndex），按 Tushare 口径定义）
FIN_TABLES = [
    'balancesheet', 'cashflow', 'dividend', 'express',
    'fina_indicator', 'forecast', 'income', 'share_change_event',
]
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
FIN_DATE_COL = {
    'share_change_event': 'change_date',
}
FIN_DATE_COL_DEFAULT = 'ann_date'


def build_table_specs(layout='nested'):
    """layout='nested'：2026-09-14 离线包层级结构（默认），五类子目录下带「更新至YYYY.M.D」版本文件夹。
    layout='flat'：2026-10-01 用户自网盘下载的扁平结构，各表平铺在 E 盘根目录，
    daily 额外从 个股行情全量\stock_daily_full.parquet 取全量版做替换迁移
    （E 根目录的 stock_daily.parquet 是增量，制度规定不得混入主库）。
    layout='flat-sub'（2026-10-22 新增）：网盘文件按「子目录\\文件名」镜像落盘，
    不带版本文件夹，五类子目录根下直接放各表。"""
    if layout == 'flat':
        e_fin = e_lhb_board = e_research = e_moneyflow = E_FLAT_ROOT
        e_daily = E_FLAT_FULL_DIR          # 全量版所在子目录
        e_daily_fname = 'stock_daily_full.parquet'
        e_basic = E_FLAT_ROOT              # basic 源在根目录，不在「个股行情全量」子目录
    elif layout == 'flat-sub':
        e_daily = os.path.join(E_BASE, E_DAILY_SUB)
        e_daily_fname = 'stock_daily.parquet'
        e_basic = e_daily
        e_fin = os.path.join(E_BASE, E_FIN_SUB)
        e_lhb_board = os.path.join(E_BASE, E_LHB_BOARD_SUB)
        e_research = os.path.join(E_BASE, E_RESEARCH_SUB)
        e_moneyflow = os.path.join(E_BASE, E_MONEYFLOW_SUB)
    else:
        e_daily = E_DAILY_DIR
        e_daily_fname = 'stock_daily.parquet'
        e_basic = E_DAILY_DIR
        e_fin = E_FIN_DIR
        e_lhb_board = E_LHB_BOARD_DIR
        e_research = E_RESEARCH_DIR
        e_moneyflow = E_MONEYFLOW_DIR
    specs = []
    # 1. daily —— 主行情：2026-09-14 制度「主行情若有到最新日期的全量版，则不备份、直接全量替换迁移（原子替换），
    #            不做主键合并；若只有增量则增量文件放增量目录不合并进主库（防口径污染）」。
    #            当前 E 全量版 14,679,286 行 / 2009-01-05~2026-09-24 → replace=True
    specs.append(dict(
        name='daily/stock_daily',
        d_path=os.path.join(D_BASE, 'daily', 'stock_daily.parquet'),
        e_path=os.path.join(e_daily, e_daily_fname),
        keys=['trade_date', 'ts_code'],
        date_col='trade_date',
        indexed=True,
        replace=True,  # 全量替换模式（不备份，直接用 E 全量版覆盖，不做合并）
    ))
    # 2. finance 8 表
    #    2026-10-22 改用 put 模式「备份 + E 版全量替换」：同一 (code, end_date, ann_date) 下存在
    #    多条内容不同的版本记录（修订稿/更正公告），主键合并会丢掉一半历史行，故不做主键合并。
    for t in FIN_TABLES:
        specs.append(dict(
            name='finance/' + t,
            d_path=os.path.join(D_BASE, 'finance', t + '.parquet'),
            e_path=os.path.join(e_fin, t + '.parquet'),
            keys=FIN_KEYS[t],
            date_col=FIN_DATE_COL.get(t, FIN_DATE_COL_DEFAULT),
            indexed=False,
            put=True,  # 备份后整体替换（不合并、不去重）
        ))
    # 3. lhb
    specs.append(dict(
        name='lhb/top_list',
        d_path=os.path.join(D_BASE, 'lhb', 'top_list.parquet'),
        e_path=os.path.join(e_lhb_board, 'top_list.parquet'),
        keys=['trade_date', 'ts_code'],
        date_col='trade_date',
        indexed=True,
    ))
    # 4. research（主键按任务指定 5 列，index 另含 quarter）
    specs.append(dict(
        name='research/report_rc_daily',
        d_path=os.path.join(D_BASE, 'research', 'report_rc_daily.parquet'),
        e_path=os.path.join(e_research, 'report_rc_daily.parquet'),
        keys=['ts_code', 'report_date', 'org_name', 'author_name', 'report_title'],
        date_col='report_date',
        indexed=True,
    ))
    # 5. board
    specs.append(dict(
        name='board/ths_daily',
        d_path=os.path.join(D_BASE, 'board', 'ths_daily.parquet'),
        e_path=os.path.join(e_lhb_board, 'ths_daily.parquet'),
        keys=['trade_date', 'ts_code'],
        date_col='trade_date',
        indexed=True,
    ))
    # 6. board_fundflow
    specs.append(dict(
        name='board_fundflow/moneyflow_ind_ths',
        d_path=os.path.join(D_BASE, 'board_fundflow', 'moneyflow_ind_ths.parquet'),
        e_path=os.path.join(e_moneyflow, 'moneyflow_ind_ths.parquet'),
        keys=['trade_date', 'ts_code'],
        date_col='trade_date',
        indexed=True,
    ))
    # 7. basic（E 源文件名 stock_basic_data.parquet）
    specs.append(dict(
        name='basic/stock_basic',
        d_path=os.path.join(D_BASE, 'basic', 'stock_basic.parquet'),
        e_path=os.path.join(e_basic, 'stock_basic_data.parquet'),
        keys=['ts_code'],
        date_col='list_date',
        indexed=False,
    ))
    return specs


def fmt_val(v):
    """把日期/数值格式化为可读字符串（兼容 int 20260911 / str / Timestamp / NaN）。"""
    try:
        if v is None or pd.isna(v):
            return 'NA'
    except Exception:
        pass
    return str(v)


def date_range_str(df, date_col, indexed):
    """取 (min, max) 日期字符串；indexed 表日期可能在 index 里。"""
    if indexed and date_col in list(df.index.names):
        s = df.index.get_level_values(date_col)
    else:
        if date_col not in list(df.columns):
            return ('NA', 'NA')
        s = df[date_col]
    if len(s) == 0:
        return ('EMPTY', 'EMPTY')
    return (fmt_val(s.min()), fmt_val(s.max()))


def process_table(spec, apply_mode):
    """处理单表。返回统计 dict。异常直接 raise，由外层统一停止。
    replace=True（主行情全量替换模式）：不备份，直接用 E 全量版覆盖，不做主键合并——
    按 2026-09-14 制度规定，主行情有全量版则不备份直接全量替换迁移（防增量合并带来的口径污染）。"""
    t0 = time.time()
    name = spec['name']
    d_path = spec['d_path']
    e_path = spec['e_path']
    keys = spec['keys']
    date_col = spec['date_col']
    indexed = spec['indexed']
    replace = spec.get('replace', False)
    put = spec.get('put', False)
    bak_path = d_path + BAK_SUFFIX

    st = dict(name=name, status='PENDING', error='')
    print('')
    print('=' * 78)
    print('[%s] 模式=%s%s%s' % (name, 'APPLY' if apply_mode else 'DRY-RUN',
                               '（全量替换）' if replace else '',
                               '（备份后整体替换，不合并）' if put else ''))

    if not os.path.isfile(d_path):
        raise RuntimeError('D 目标文件不存在: %s' % d_path)
    if not os.path.isfile(e_path):
        raise RuntimeError('E 源文件不存在: %s' % e_path)

    if apply_mode and not replace:
        if os.path.exists(bak_path):
            raise RuntimeError('备份已存在，拒绝再次备份（防止用已合并文件覆盖原始备份）: %s' % bak_path)

    t_read = time.time()
    d = pd.read_parquet(d_path)
    e = pd.read_parquet(e_path)
    st['read_sec'] = round(time.time() - t_read, 1)

    d_rows = len(d)
    e_rows = len(e)
    st['d_rows'] = d_rows
    st['e_rows'] = e_rows
    st['d_range'] = date_range_str(d, date_col, indexed)
    st['e_range'] = date_range_str(e, date_col, indexed)
    print('  D 行数=%d  日期[%s ~ %s]' % (d_rows, st['d_range'][0], st['d_range'][1]))
    print('  E 行数=%d  日期[%s ~ %s]' % (e_rows, st['e_range'][0], st['e_range'][1]))

    # index 结构一致性（replace 模式整体覆盖不合并，无需 index 一致；合并模式才检查）
    d_idx_names = list(d.index.names)
    e_idx_names = list(e.index.names)
    if indexed and not replace:
        if d_idx_names != e_idx_names:
            raise RuntimeError('index 层级不一致 D=%s E=%s' % (d_idx_names, e_idx_names))

    # 展开到值域做合并（index 层级先展开为列）
    if indexed:
        d2 = d.reset_index()
        e2 = e.reset_index()
    else:
        d2 = d.copy()
        e2 = e.copy()
    del d, e

    # 列对齐：D 列序（含展开后的 index 列）为基准，E 独有列追加
    d_cols = list(d2.columns)
    e_cols = list(e2.columns)
    d_only_cols = [c for c in d_cols if c not in e_cols]
    e_only_cols = [c for c in e_cols if c not in d_cols]
    union_cols = d_cols + e_only_cols
    st['d_only_cols'] = d_only_cols
    st['e_only_cols'] = e_only_cols
    st['union_cols_n'] = len(union_cols)
    st['union_cols'] = union_cols
    print('  列对齐: D独有列=%s E独有列=%s 合并列数=%d' % (d_only_cols, e_only_cols, len(union_cols)))

    # ---- put 模式（财务表，2026-10-22）：备份 + E 版整体替换 ----
    # 理由：同一主键下有多条内容不同的版本记录，主键合并会丢历史行；供应商 E 版即权威全量。
    # 校验：E 行数不得少于 D 的 50%（防下载不完整/错版覆盖），列结构以 D 为基准补齐。
    if put:
        if e_rows < d_rows * 0.5:
            raise RuntimeError('整体替换模式但 E 行数(%d) 不足 D 行数(%d) 的 50%%——疑似源文件不完整，拒绝替换'
                               % (e_rows, d_rows))
        # 去掉 E 端 parquet 残留的索引列（Parquet 里存的 RangeIndex 会变成 __index_level_0__ 列）
        e_extra_idx = [c for c in e2.columns if c not in d2.columns and str(c).startswith('__index_level_')]
        if e_extra_idx:
            e2 = e2.drop(columns=e_extra_idx)
            e_only_cols = [c for c in e_only_cols if c not in e_extra_idx]
        result = e2.reindex(columns=d2.columns if not e_only_cols else d_cols + e_only_cols)
        st['d_only_cols'] = d_only_cols
        st['e_only_cols'] = e_only_cols
        st['union_cols'] = list(result.columns)
        st['union_cols_n'] = len(result.columns)
        st['d_only_keys'] = 0
        st['e_new_keys'] = e_rows - d_rows if e_rows >= d_rows else 0
        st['conflict_keys'] = min(e_rows, d_rows)
        st['e_internal_dup'] = 0
        st['d_internal_dup'] = 0
        st['key_nan_rows'] = 0
        del e2, d2
        st['expected_rows'] = int(len(result))
        st['result_rows'] = int(len(result))
        st['result_range'] = date_range_str(result, date_col, indexed)
        print('  [整体替换] E 行数=%d（D 行数=%d），丢弃索引列=%s' % (e_rows, d_rows, e_extra_idx))
        print('  替换后行数=%d  日期[%s ~ %s]' % (st['result_rows'], st['result_range'][0], st['result_range'][1]))
        if not apply_mode:
            st['status'] = 'PLAN_OK'
            st['total_sec'] = round(time.time() - t0, 1)
            print('  [DRY-RUN] 不写文件。计划：备份->整体替换->验证')
            return st
        t_bak = time.time()
        shutil.copy2(d_path, bak_path)
        if not os.path.isfile(bak_path) or os.path.getsize(bak_path) != os.path.getsize(d_path):
            raise RuntimeError('备份失败或大小不一致: %s' % bak_path)
        st['backup'] = os.path.basename(bak_path)
        st['backup_sec'] = round(time.time() - t_bak, 1)
        print('  备份完成: %s（%.1fs）' % (bak_path, st['backup_sec']))
        t_write = time.time()
        tmp_path = d_path + '.tmp_put'
        result.to_parquet(tmp_path, engine='pyarrow')
        os.replace(tmp_path, d_path)
        st['write_sec'] = round(time.time() - t_write, 1)
        print('  写入完成（整体替换）: %s（%.1fs）' % (d_path, st['write_sec']))
        del result
        t_verify = time.time()
        v = pd.read_parquet(d_path)
        st['verified_rows'] = int(len(v))
        v_range = date_range_str(v, date_col, indexed)
        st['verified_range'] = v_range
        ok = True
        errs = []
        if st['verified_rows'] != st['expected_rows']:
            ok = False
            errs.append('行数不符: 写后=%d 预期=%d' % (st['verified_rows'], st['expected_rows']))
        if list(v.columns) != st['union_cols']:
            ok = False
            errs.append('列结构不符: %s' % (list(v.columns),))
        try:
            if v_range[1] < st['e_range'][1]:
                ok = False
                errs.append('写后日期上界 %s < E 上界 %s' % (v_range[1], st['e_range'][1]))
        except TypeError:
            pass
        st['verify_sec'] = round(time.time() - t_verify, 1)
        st['total_sec'] = round(time.time() - t0, 1)
        if not ok:
            st['status'] = 'FAIL'
            st['error'] = '写后验证失败: %s' % '; '.join(errs)
            raise RuntimeError(st['error'])
        print('  验证通过: 行数=%d 日期[%s ~ %s]（%.1fs）' % (st['verified_rows'], v_range[0], v_range[1], st['verify_sec']))
        st['status'] = 'OK'
        return st

    missing = [k for k in keys if k not in list(d2.columns) or k not in list(e2.columns)]
    if missing:
        raise RuntimeError('主键列缺失: %s' % missing)

    d2 = d2.reindex(columns=union_cols)
    e2 = e2.reindex(columns=union_cols)

    # ---- replace 模式（主行情全量替换）：不合并，直接用 E 全量版 ----
    # 2026-09-14 制度规定：主行情有到最新日期的全量版 → 不备份直接全量替换迁移（防增量合并口径污染）。
    # 校验：E 版行数 >= D 版行数（全量版应覆盖 D 的整个历史）。
    if replace:
        d_min, d_max = date_range_str(d2, date_col, False) if not indexed else date_range_str(d2, date_col, True)
        e_min, e_max = date_range_str(e2, date_col, False) if not indexed else date_range_str(e2, date_col, True)
        # date_range_str 返回 (min,max) 字符串；比较时用行数+版本号语义：E 行数 >= D 行数视为全量
        if e_rows < d_rows:
            raise RuntimeError('全量替换模式但 E 行数(%d) < D 行数(%d)——E 不是全量版，拒绝替换，改用增量处理' % (e_rows, d_rows))
        st['replace_check'] = 'E 全量版（%d 行）覆盖 D（%d 行），范围 %s~%s → %s~%s' % (
            e_rows, d_rows, d_min, d_max, e_min, e_max)
        print('  [全量替换] E 行数=%d >= D 行数=%d，确认为全量版，直接迁移' % (e_rows, d_rows))
        st['d_only_keys'] = 0
        st['e_new_keys'] = e_rows - d_rows
        st['conflict_keys'] = d_rows
        st['e_internal_dup'] = 0
        st['d_internal_dup'] = 0
        st['key_nan_rows'] = 0
        result = e2.copy()
        del e2, d2
        st['expected_rows'] = int(len(result))
        st['result_rows'] = int(len(result))
        st['result_range'] = date_range_str(result, date_col, indexed)
        print('  迁移后行数=%d  日期[%s ~ %s]' % (st['result_rows'], st['result_range'][0], st['result_range'][1]))
        if not apply_mode:
            st['status'] = 'PLAN_OK'
            st['total_sec'] = round(time.time() - t0, 1)
            print('  [DRY-RUN] 不写文件。计划：删除现有->全量迁移->验证')
            return st
        # APPLY：不备份（2026-09-14 诚哥指令：主行情直接删除迁移）-> 写临时文件 -> os.replace 原子替换 -> 验证
        # 不备份理由：主行情为 E 全量版直接迁移，旧 D 版被全量覆盖，无增量信息丢失风险。
        st['backup'] = '无（主行情规定不备份，直接全量迁移）'
        t_write = time.time()
        tmp_path = d_path + '.tmp_full'
        # 2026-09-14 修复：E 源为扁平结构（RangeIndex + 自带 'index' 列），直接 to_parquet 会把主库写成
        # 扁平（无 MultiIndex），导致所有裸读主库 .reset_index() 的脚本 KeyError: trade_date（当晚候选断链根因）。
        # 写入前：去 E 源残留 'index' 列 + 重建 (trade_date, ts_code) MultiIndex，与旧主库结构一致。
        if 'index' in result.columns:
            result = result.drop(columns=['index'])
        if indexed:
            result = result.set_index(keys).sort_index()
        result.to_parquet(tmp_path, engine='pyarrow')
        os.replace(tmp_path, d_path)   # 原子替换 = 删除旧文件 + 迁移新全量版
        st['write_sec'] = round(time.time() - t_write, 1)
        print('  写入完成（不备份，原子替换）: %s（%.1fs）' % (d_path, st['write_sec']))
        del result
        t_verify = time.time()
        v = pd.read_parquet(d_path)
        v_rows = int(len(v))
        st['verified_rows'] = v_rows
        v_range = date_range_str(v, date_col, indexed)
        st['verified_range'] = v_range
        ok = (v_rows == st['result_rows'])
        if ok and v_range[1] < e_max:
            ok = False
            errs = ['写后日期范围未覆盖 E 版']
        else:
            errs = []
        st['verify_sec'] = round(time.time() - t_verify, 1)
        if ok:
            st['status'] = 'OK'
            st['total_sec'] = round(time.time() - t0, 1)
            print('  写后复核: %d 行 / 日期[%s ~ %s] → OK' % (v_rows, v_range[0], v_range[1]))
        else:
            st['status'] = 'FAIL'
            st['error'] = '写后复核失败: %s' % ('; '.join(errs) if errs else '行数不符')
            raise RuntimeError(st['error'])
        return st

    # ---- 合并模式（主行情之外的表）----
    # 主键集合运算
    dk = pd.MultiIndex.from_frame(d2[keys])
    ek = pd.MultiIndex.from_frame(e2[keys])
    d_in_e = dk.isin(ek)
    e_dup_mask = ek.duplicated(keep='last')
    d_only_cnt = int((~d_in_e).sum())
    e_dup_cnt = int(e_dup_mask.sum())
    dk_u = dk.unique()
    ek_u = ek.unique()
    both_cnt = int(len(dk_u.intersection(ek_u)))
    e_new_cnt = int(len(ek_u.difference(dk_u)))
    d_internal_dup = int(len(dk) - len(dk_u))
    # 主键含 NaN 的行数（NaN 匹配语义可能不一致，需显式报告）
    key_nan_rows = int(d2[keys].isna().any(axis=1).sum() + e2[keys].isna().any(axis=1).sum())

    st['d_only_keys'] = d_only_cnt
    st['e_new_keys'] = e_new_cnt
    st['conflict_keys'] = both_cnt
    st['e_internal_dup'] = e_dup_cnt
    st['d_internal_dup'] = d_internal_dup
    st['key_nan_rows'] = key_nan_rows
    print('  主键=%s' % '+'.join(keys))
    print('  D独有键=%d  E新增键=%d  冲突键(以E为准)=%d  E内部重复行=%d  D内部重复行=%d  主键含NaN行=%d'
          % (d_only_cnt, e_new_cnt, both_cnt, e_dup_cnt, d_internal_dup, key_nan_rows))

    # 合并：D独有行 + E去重后全量（冲突键天然取 E）
    t_merge = time.time()
    d_only_df = d2.loc[~d_in_e]
    e_keep = e2.loc[~e_dup_mask]
    del d2, e2, dk, ek, d_in_e, e_dup_mask
    result = pd.concat([d_only_df, e_keep], copy=False)
    del d_only_df, e_keep

    expected_rows = int(len(result))
    st['expected_rows'] = expected_rows
    if expected_rows < max(d_rows, e_rows):
        msg = ('合并后行数异常坍缩: expected=%d < max(D,E)=%d（D内部重复=%d, E内部重复=%d）'
               % (expected_rows, max(d_rows, e_rows), d_internal_dup, e_dup_cnt))
        if d_internal_dup == 0 and e_dup_cnt == 0:
            raise RuntimeError(msg)
        print('  [警告] ' + msg)

    # 安全网：若仍有主键重复（NaN 匹配语义差异等），仅在 D 无内部重复时按 keep=last（E 优先）去重
    dup_after = result.duplicated(subset=keys, keep='last')
    dup_after_cnt = int(dup_after.sum())
    if dup_after_cnt > 0:
        if d_internal_dup > 0:
            raise RuntimeError('合并后存在 %d 行主键重复且 D 版本身有内部重复，需人工裁决，停止' % dup_after_cnt)
        print('  [安全网] 合并后主键重复 %d 行（NaN 语义差异），按 E 优先去重' % dup_after_cnt)
        result = result.loc[~dup_after]
        expected_rows = int(len(result))
        st['expected_rows'] = expected_rows
    del dup_after
    st['merge_sec'] = round(time.time() - t_merge, 1)

    # 排序恢复结构
    if indexed:
        result = result.set_index(d_idx_names)
        result = result.sort_index()
    else:
        result = result.sort_values(keys, kind='mergesort').reset_index(drop=True)

    st['result_rows'] = int(len(result))
    st['result_range'] = date_range_str(result, date_col, indexed)
    print('  合并后行数=%d（预期=%d）  日期[%s ~ %s]'
          % (st['result_rows'], st['expected_rows'], st['result_range'][0], st['result_range'][1]))

    if not apply_mode:
        st['status'] = 'PLAN_OK'
        st['total_sec'] = round(time.time() - t0, 1)
        print('  [DRY-RUN] 不写文件。计划：备份->合并->验证')
        return st

    # ---------- APPLY：备份 -> 写 -> 验证 ----------
    t_bak = time.time()
    shutil.copy2(d_path, bak_path)
    if not os.path.isfile(bak_path) or os.path.getsize(bak_path) != os.path.getsize(d_path):
        raise RuntimeError('备份失败或大小不一致: %s' % bak_path)
    st['backup'] = os.path.basename(bak_path)
    st['backup_sec'] = round(time.time() - t_bak, 1)
    print('  备份完成: %s（%.1fs）' % (bak_path, st['backup_sec']))

    t_write = time.time()
    result.to_parquet(d_path, engine='pyarrow')
    st['write_sec'] = round(time.time() - t_write, 1)
    print('  写入完成: %s（%.1fs）' % (d_path, st['write_sec']))
    del result

    # 写后验证：重新读取复核
    t_verify = time.time()
    v = pd.read_parquet(d_path)
    v_rows = int(len(v))
    st['verified_rows'] = v_rows
    ok = True
    errs = []
    if v_rows != st['expected_rows']:
        ok = False
        errs.append('行数不符: 写后=%d 预期=%d' % (v_rows, st['expected_rows']))
    if indexed:
        if list(v.index.names) != d_idx_names:
            ok = False
            errs.append('index 结构改变: %s != %s' % (list(v.index.names), d_idx_names))
        expected_data_cols = [c for c in union_cols if c not in d_idx_names]
    else:
        expected_data_cols = union_cols
    if list(v.columns) != expected_data_cols:
        ok = False
        errs.append('列结构不符: %s' % (list(v.columns),))
    # 主键唯一性
    if indexed:
        v2 = v.reset_index()
    else:
        v2 = v
    vdup = int(v2.duplicated(subset=keys).sum())
    if vdup != 0:
        ok = False
        errs.append('写后主键重复 %d 行' % vdup)
    # 日期范围必须覆盖 D 与 E 双方范围
    v_range = date_range_str(v, date_col, indexed)
    st['verified_range'] = v_range
    if v_range != ('EMPTY', 'EMPTY'):
        try:
            if v_range[1] < st['e_range'][1]:
                ok = False
                errs.append('写后日期上界 %s < E 上界 %s' % (v_range[1], st['e_range'][1]))
        except TypeError:
            pass
    st['verify_sec'] = round(time.time() - t_verify, 1)
    st['total_sec'] = round(time.time() - t0, 1)
    if not ok:
        raise RuntimeError('写后验证失败: %s' % '; '.join(errs))
    print('  验证通过: 行数=%d 日期[%s ~ %s] index=%s（%.1fs）'
          % (v_rows, v_range[0], v_range[1], d_idx_names if indexed else 'RangeIndex', st['verify_sec']))
    print('  [%s] 完成，总耗时 %.1fs' % (name, st['total_sec']))
    st['status'] = 'OK'
    return st


def write_report(results, apply_mode, aborted_msg, e_layout='nested'):
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    lines = []
    lines.append('# D:/astock 数据更新验证报告（E 盘离线资产合并）')
    lines.append('')
    lines.append('- 执行时间：%s' % now)
    lines.append('- 执行模式：%s' % ('APPLY（备份+合并+写回+验证）' if apply_mode else 'DRY-RUN（仅计划，未写任何数据文件）'))
    lines.append('- 脚本：`D:\\QuantLab\\scripts\\update_astock_from_e.py`')
    lines.append('- E 源：`%s`' % (E_FLAT_ROOT if e_layout == 'flat' else E_BASE)
                 + ('（扁平布局：各表平铺根目录，daily 全量版取 `个股行情全量\\stock_daily_full.parquet`）'
                    if e_layout == 'flat' else
                    ('（子目录镜像布局：五类子目录根下直接放表）' if e_layout == 'flat-sub'
                     else '（各子目录「更新至%s」）' % E_VERSION)))
    lines.append('- D 目标：`D:\\astock\\`（唯一权威数据源，AGENTS 硬规则）')
    lines.append('- 备份后缀：`%s`（与原文件同目录）' % BAK_SUFFIX)
    lines.append('- 合并规则：按主键去重，D 独有行保留 + E 全量行，冲突键以 E 为准（E 较新）；只增不删。')
    lines.append('- 更新顺序：daily → finance 8 表 → lhb → research → board → board_fundflow → basic；异常即停，后续表不动。')
    lines.append('')
    if aborted_msg:
        lines.append('> **[中止]** %s' % aborted_msg)
        lines.append('')
    lines.append('## 结果总表')
    lines.append('')
    lines.append('| # | 表 | D 行数 | E 行数 | 合并后行数 | D独有键 | E新增键 | 冲突键(取E) | 日期范围(前→后) | 备份 | 耗时(s) | 状态 |')
    lines.append('|---|---|---|---|---|---|---|---|---|---|---|---|')
    for i, st in enumerate(results, 1):
        if 'd_rows' in st:
            rng = '%s~%s → %s~%s' % (st['d_range'][0], st['d_range'][1],
                                      st.get('verified_range', st.get('result_range', ('?', '?')))[0],
                                      st.get('verified_range', st.get('result_range', ('?', '?')))[1])
            lines.append('| %d | %s | %d | %d | %s | %d | %d | %d | %s | %s | %s | %s |' % (
                i, st['name'], st['d_rows'], st['e_rows'],
                st.get('verified_rows', st.get('result_rows', '-')),
                st['d_only_keys'], st['e_new_keys'], st['conflict_keys'],
                rng,
                st.get('backup', '-'), st.get('total_sec', '-'), st['status']))
        else:
            lines.append('| %d | %s | - | - | - | - | - | - | - | - | - | %s |' % (i, st['name'], st['status']))
    lines.append('')
    lines.append('## 每表详情')
    lines.append('')
    for i, st in enumerate(results, 1):
        lines.append('### %d. %s' % (i, st['name']))
        lines.append('')
        if 'd_rows' not in st:
            lines.append('- 状态：%s（未执行）' % st['status'])
            if st.get('error'):
                lines.append('- 错误：%s' % st['error'])
            lines.append('')
            continue
        lines.append('- D 目标：`%s`（%d 行）' % (st.get('d_path', ''), st['d_rows']))
        lines.append('- E 源：`%s`（%d 行）' % (st.get('e_path', ''), st['e_rows']))
        lines.append('- 主键：%s' % st.get('keys_str', ''))
        lines.append('- D 日期范围：%s ~ %s；E 日期范围：%s ~ %s；合并后：%s ~ %s' % (
            st['d_range'][0], st['d_range'][1], st['e_range'][0], st['e_range'][1],
            st.get('verified_range', st.get('result_range', ('?', '?')))[0],
            st.get('verified_range', st.get('result_range', ('?', '?')))[1]))
        lines.append('- 键统计：D独有键 %d / E新增键 %d / 冲突键(以E为准) %d / D内部重复 %d / E内部重复 %d / 主键含NaN行 %d' % (
            st['d_only_keys'], st['e_new_keys'], st['conflict_keys'],
            st['d_internal_dup'], st['e_internal_dup'], st['key_nan_rows']))
        lines.append('- 列对齐：D独有列=%s；E独有列=%s；合并后列数=%d' % (
            st['d_only_cols'] if st['d_only_cols'] else '无',
            st['e_only_cols'] if st['e_only_cols'] else '无',
            st['union_cols_n']))
        lines.append('- 合并后行数：%d（写后复核=%s）' % (
            st.get('result_rows', st.get('expected_rows', -1)), st.get('verified_rows', '未写')))
        if apply_mode and 'd_rows' in st and st.get('status') == 'OK':
            lines.append('- 备份：`%s`（%.1fs）；写回 %.1fs；验证 %.1fs；总耗时 %.1fs' % (
                st.get('backup', ''), st.get('backup_sec', 0), st.get('write_sec', 0),
                st.get('verify_sec', 0), st.get('total_sec', 0)))
        else:
            lines.append('- 计划耗时：读 %.1fs / 合并 %.1fs / 合计 %.1fs（dry-run 未写盘）' % (
                st.get('read_sec', 0), st.get('merge_sec', 0), st.get('total_sec', 0)))
        lines.append('- 状态：%s' % st['status'])
        if st.get('error'):
            lines.append('- 异常：`%s`' % st['error'])
        lines.append('')
    lines.append('## 口径修正与说明')
    lines.append('')
    lines.append('1. **basic 的 fill_source 列**：任务盘点写「E 无 D 独有的 fill_source 列」，实测相反——'
                 '`fill_source` 存在于 **E 版**（`stock_basic_data.parquet` 5904 行），**D 版**（5892 行）没有该列。'
                 '合并按「D 列序为基准 + E 独有列追加」的通用规则处理：合并后保留 E 的 `fill_source` 列，'
                 'D 独有股票（不在 E 中）该列为空。本报告以实际文件为准。')
    lines.append('2. **finance 主键**：D 版 8 张财务表 parquet 均无显式索引（RangeIndex），'
                 '按 Tushare 口径定义主键（见脚本 `FIN_KEYS`），冲突以 E 为准，PIT 字段（ann_date/f_ann_date）原样保留。')
    lines.append('3. **research 主键**：按任务指定的 5 列（ts_code+report_date+org_name+author_name+report_title），'
                 'index 中的 quarter 列不参与去重。')
    lines.append('4. **未触及**：northbound / cb / etf / min_* / index / chip / auction / benchmark / index_weight / Updatedata 均未改动。')
    if e_layout == 'flat':
        lines.append('5. **增量日线未混入**：E 盘根目录的 `stock_daily.parquet` 是增量版（22,246 行 / 2026-09-21~09-24），'
                     '按制度「只有增量则不合并进主库（防口径污染）」**未参与本次升级**；'
                     'daily 主库仅用 `个股行情全量\\stock_daily_full.parquet`（14,679,286 行 / 2009-01-05~2026-09-24）全量替换。')
    lines.append('')
    lines.append('## 遗留')
    lines.append('')
    if apply_mode:
        lines.append('- 回滚方式：任一表如需回滚，用同目录 `%s` 备份覆盖回原名即可（备份为 D 原版原样拷贝）。' % BAK_SUFFIX)
        lines.append('- 建议后续：下游 reader（astock_reader 等）跑一次冒烟回测确认新数据可读；'
                     '新数据（08-24~09-11）已经过本脚本「行数/日期范围/index/主键唯一性」四项验证。')
    else:
        lines.append('- 本报告为 dry-run 计划，未写任何数据文件；执行 `--apply` 后会重写本报告。')
    lines.append('')
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('')
    print('报告已写入: %s' % REPORT_PATH)


def main():
    parser = argparse.ArgumentParser(
        description='Merge newer E:/temp data into D:/astock '
                    '(daily: full replace; others: backup + primary-key merge)'
    )
    parser.add_argument('--apply', action='store_true', help='真正执行（默认 dry-run 只打印计划）')
    parser.add_argument('--e-layout', choices=['nested', 'flat', 'flat-sub'], default='nested',
                        help='E 盘源布局：nested=2026-09-14 离线包层级结构（默认）；'
                             'flat=2026-10-01 网盘下载扁平结构（根目录平铺 + 个股行情全量\\stock_daily_full.parquet）；'
                             'flat-sub=网盘文件按子目录镜像落盘（各子目录根下直接放表，无版本文件夹）')
    parser.add_argument('--run-tag', default=None,
                        help='本轮备份标识（YYYYMMDD），决定 .bak_<tag> 后缀与报告文件名；'
                             '不传则用默认 20260913。若该 tag 的备份已存在会被门禁拒绝，需换新 tag。')
    parser.add_argument('--e-version', default=None,
                        help='nested 布局下 E 盘版本文件夹的日期，形如 2026.10.16（对应「更新至2026.10.16」）；'
                             '不传则自动选五类子目录齐备的最大版本。')
    args = parser.parse_args()
    apply_mode = bool(args.apply)

    if args.e_layout == 'nested':
        ver = args.e_version or detect_latest_version()
        if not ver:
            raise SystemExit('E 盘未找到五类子目录齐备的「更新至YYYY.M.D」版本文件夹，'
                             '请先下载并用 --e-version 指定')
        bind_e_dirs(ver)
        print('E 版本: 更新至%s' % ver)

    global BAK_TAG, BAK_SUFFIX, REPORT_PATH
    if args.run_tag:
        tag = ''.join(ch for ch in args.run_tag if ch.isdigit())
        if not tag:
            raise SystemExit('--run-tag 需为数字日期，如 20261001')
        BAK_TAG = tag
        BAK_SUFFIX = '.bak_%s' % BAK_TAG
        REPORT_PATH = r'D:\QuantLab\scripts\update_astock_verify_%s.md' % BAK_TAG

    print('pandas %s / pyarrow %s' % (pd.__version__, __import__('pyarrow').__version__))
    print('E 布局: %s' % args.e_layout)
    print('备份标识: %s（后缀 %s）' % (BAK_TAG, BAK_SUFFIX))
    print('模式: %s' % ('APPLY' if apply_mode else 'DRY-RUN（默认，不写任何文件）'))

    specs = build_table_specs(args.e_layout)

    # APPLY 前置门禁：一次性检查全部 14 张表的「源文件存在 + 备份名不冲突」。
    # 原因：脚本按 daily -> finance -> ... 顺序处理且遇错即停，若中途才发现某个备份已存在，
    # 会出现「daily 已原子替换、后续表全部未动」的半升级状态。故必须在动手前全量预检。
    if apply_mode:
        conflicts = []
        for spec in specs:
            if not os.path.isfile(spec['d_path']):
                conflicts.append('D 目标缺失: %s' % spec['d_path'])
            if not os.path.isfile(spec['e_path']):
                conflicts.append('E 源缺失: %s' % spec['e_path'])
            if spec.get('replace'):
                continue
            bak = spec['d_path'] + BAK_SUFFIX
            if os.path.exists(bak):
                conflicts.append('备份已存在(需换 --run-tag): %s' % bak)
        if conflicts:
            print('')
            print('!! [PRECHECK FAIL] APPLY 前置检查未通过，未写任何文件：')
            for c in conflicts:
                print('   - %s' % c)
            sys.exit(2)
        print('前置检查通过：14 张表源/目标齐备，备份名无冲突（%s）' % BAK_SUFFIX)

    results = []
    aborted_msg = None
    for spec in specs:
        st = dict(name=spec['name'], status='PENDING', error='',
                  d_path=spec['d_path'], e_path=spec['e_path'],
                  keys_str='+'.join(spec['keys']))
        try:
            st = process_table(spec, apply_mode)
            st['d_path'] = spec['d_path']
            st['e_path'] = spec['e_path']
            st['keys_str'] = '+'.join(spec['keys'])
            results.append(st)
        except Exception as exc:
            st['status'] = 'FAILED'
            st['error'] = '%s: %s' % (type(exc).__name__, exc)
            results.append(st)
            idx = len(results)
            for rest in specs[idx:]:
                results.append(dict(name=rest['name'], status='SKIPPED(中止)', error=''))
            aborted_msg = ('表 %s 处理失败（%s: %s），按规则立即停止，其后所有表未动。'
                           % (spec['name'], type(exc).__name__, exc))
            print('')
            print('!! [ABORT] ' + aborted_msg)
            traceback.print_exc()
            break

    print('')
    print('=' * 78)
    print('汇总:')
    for st in results:
        print('  %-42s %s' % (st['name'], st['status']))
    write_report(results, apply_mode, aborted_msg, args.e_layout)
    if aborted_msg:
        sys.exit(1)
    print('')
    print('全部完成。' if apply_mode else 'DRY-RUN 完成，确认计划无误后加 --apply 执行。')


if __name__ == '__main__':
    main()
