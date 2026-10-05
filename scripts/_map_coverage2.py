import json
import os
import re
import collections

ALL = r'D:\QuantLab\browse\_all_files.jsonl'
DS = r'D:\QuantLab\browse\_dside_files.json'

CODE = re.compile(r'^(\d{6})(?:[._](SH|SZ|BJ))?$', re.I)


def code_of(fn):
    stem = os.path.splitext(fn)[0]
    m = CODE.match(stem)
    return m.group(1) if m else None


def board_of(fn):
    stem = os.path.splitext(fn)[0]
    m = CODE.match(stem)
    if not m:
        return None
    suf = (m.group(2) or '').upper()
    if suf in ('SH', 'SZ', 'BJ'):
        return suf
    return None


rows = []
with open(ALL, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
pq = [x for x in rows if x['ext'] == '.parquet']

ds = json.load(open(DS, encoding='utf-8'))
ds_names = collections.defaultdict(set)
ds_codes = collections.defaultdict(set)
for x in ds:
    ds_names[x['top']].add(x['fn'])
    c = code_of(x['fn'])
    if c:
        ds_codes[x['top']].add(c)

# 供应商数据集 -> D 侧对应生产目录
MAP = {
    'stock_1min': 'min_1min',
    'stock_5min': 'min_5min',
    'stock_15min': 'min_15min',
    'stock_30min': 'min_30min',
    'stock_60min': 'min_60min',
    'index_1min': 'index',
    'index_5min': 'index',
    'index_15min': 'index',
    'index_30min': 'index',
    'index_60min': 'index',
    'etf_1min': 'etf',
    'etf_5min': 'etf',
    'etf_15min': 'etf',
    'etf_30min': 'etf',
    'etf_60min': 'etf',
}

print('=== 供应商数据集 -> D 侧证券代码覆盖 ===')
agg = collections.defaultdict(lambda: {
    'n': 0, 'b': 0, 'codes': set(), 'vers': set(), 'dirs': set(),
})
for x in pq:
    leaf = x['dir'].rstrip('/').split('/')[-1] if x['dir'] else ''
    c = code_of(x['filename'])
    a = agg[leaf]
    a['n'] += 1
    a['b'] += x['size']
    if c:
        a['codes'].add(c)
    if x['ver']:
        a['vers'].add(x['ver'])
    a['dirs'].add(x['dir'])

rowsout = []
for leaf in sorted(agg, key=lambda k: -agg[k]['b']):
    a = agg[leaf]
    tgt = MAP.get(leaf, '')
    if tgt and a['codes']:
        have = len(a['codes'] & ds_codes[tgt])
        miss = len(a['codes'] - ds_codes[tgt])
        rate = have / max(1, len(a['codes']))
        status = 'OK %.1f%%' % (rate * 100)
        if rate < 0.999:
            status += ' (缺%d)' % miss
        rowsout.append((leaf, tgt, len(a['codes']), have, miss, status))
    else:
        status = '非分证券/无D侧映射'

for leaf, tgt, nc, have, miss, status in rowsout:
    print('  %-14s -> %-12s 证券=%-6d 命中=%-6d %s' % (
        leaf, tgt, nc, have, status))

print()
print('=== 供应商非分证券数据集（按目录叶子聚合 top 40）===')
nonsec = []
for leaf, a in agg.items():
    if not a['codes']:
        nonsec.append((leaf, a))
for leaf, a in sorted(nonsec, key=lambda t: -t[1]['b'])[:40]:
    print('  %-30s n=%-6d GiB=%7.3f vers=%s' % (
        leaf[:30], a['n'], a['b'] / 1024 ** 3, sorted(a['vers'])[-3:]))
    for d in sorted(a['dirs'])[:3]:
        print('        <- %s' % d[:110])