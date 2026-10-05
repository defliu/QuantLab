# -*- coding: utf-8 -*-
"""生产迁移状态核实：对照计划 JSON 的 source_identity，检查是否真的写入过。"""
import os
import sys
import json
import datetime

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'D:\astock'
PLAN = os.path.join(ROOT, '_migration_plan_v2.json')
TXN = os.path.join(ROOT, '_migration_txn_v2.json')
STAGING = os.path.join(ROOT, '_staging')
INCR = os.path.join(ROOT, '_incremental')

print('=' * 78)
print('1) 迁移痕迹检查')
print('=' * 78)
for p in (PLAN, TXN, STAGING, INCR):
    print('%-34s exists=%s' % (os.path.basename(p), os.path.exists(p)))

print()
print('=' * 78)
print('2) 全盘搜备份/事务/临时残留')
print('=' * 78)
pats = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    for fn in filenames:
        low = fn.lower()
        if ('.bak' in low or low.startswith('bak') or 'migration' in low
                or low.endswith('.tmp') or '.staging' in low):
            pats.append(os.path.join(dirpath, fn))
    for dn in list(dirnames):
        if dn in ('_staging', '_incremental') or dn.startswith('_'):
            pats.append(os.path.join(dirpath, dn) + os.sep)
print('命中 %d 项' % len(pats))
for p in sorted(pats)[:40]:
    print('   ', p)

print()
print('=' * 78)
print('3) 计划文件 mode / 指纹')
print('=' * 78)
if os.path.isfile(PLAN):
    plan = json.load(open(PLAN, 'r', encoding='utf-8'))
    print('mode          :', plan.get('mode'))
    print('generated     :', plan.get('generated') or plan.get('generated_at'))
    print('fingerprint   :', plan.get('plan_fingerprint'))
    print('run_tag       :', plan.get('run_tag'))
    si = plan.get('source_identity') or []
    print('source_identity 条数:', len(si))

    print()
    print('   逐一比对 size/mtime 是否与计划登记一致（不一致=已被改动）')
    changed = []
    missing = []
    for item in si:
        path, size, mtime = item[0], item[1], item[2]
        if not os.path.isfile(path):
            missing.append(path)
            continue
        cur_s = os.path.getsize(path)
        cur_m = int(os.path.getmtime(path))
        if cur_s != size or cur_m != mtime:
            changed.append((path, size, cur_s, mtime, cur_m))
    print('   缺失 :', len(missing))
    for p in missing:
        print('      -', p)
    print('   已变更:', len(changed))
    for p, s0, s1, m0, m1 in changed:
        print('      *', p)
        print('          size %d -> %d | mtime %s -> %s' % (
            s0, s1,
            datetime.datetime.fromtimestamp(m0).strftime('%Y-%m-%d %H:%M:%S'),
            datetime.datetime.fromtimestamp(m1).strftime('%Y-%m-%d %H:%M:%S')))
    if not changed and not missing:
        print('   => 全部一致，生产数据【未被修改】')

print()
print('=' * 78)
print('4) 14 个 D 目标文件当前实况')
print('=' * 78)
targets = [
    r'D:\astock\daily\stock_daily.parquet',
    r'D:\astock\board\ths_daily.parquet',
    r'D:\astock\lhb\top_list.parquet',
    r'D:\astock\research\report_rc_daily.parquet',
    r'D:\astock\board_fundflow\moneyflow_ind_ths.parquet',
    r'D:\astock\basic\stock_basic.parquet',
    r'D:\astock\finance\balancesheet.parquet',
    r'D:\astock\finance\cashflow.parquet',
    r'D:\astock\finance\cashflow.parquet',
    r'D:\astock\finance\dividend.parquet',
    r'D:\astock\finance\express.parquet',
    r'D:\astock\finance\fina_indicator.parquet',
    r'D:\astock\finance\forecast.parquet',
    r'D:\astock\finance\income.parquet',
    r'D:\astock\finance\share_change_event.parquet',
]
seen = set()
for p in targets:
    if p in seen:
        continue
    seen.add(p)
    if os.path.isfile(p):
        st = os.stat(p)
        print('OK   %-46s %12d bytes  %s' % (
            os.path.relpath(p, ROOT), st.st_size,
            datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')))
    else:
        print('MISS %-46s' % os.path.relpath(p, ROOT))
