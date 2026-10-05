import json
import os
import collections

ALL = r'D:\QuantLab\browse\_all_files.jsonl'
DS = r'D:\QuantLab\browse\_dside_files.json'

rows = []
with open(ALL, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

pq = [x for x in rows if x['ext'] == '.parquet']

ds = json.load(open(DS, encoding='utf-8'))
ds_by_top = collections.defaultdict(list)
ds_names = collections.defaultdict(set)
for x in ds:
    ds_by_top[x['top']].append(x)
    ds_names[x['top']].add(x['fn'])

print('=== D:/astock 一级目录（%d 个） ===' % len(ds_by_top))
for k in sorted(ds_by_top, key=lambda k: -len(ds_by_top[k])):
    b = sum(x['sz'] for x in ds_by_top[k])
    print('  %-16s n=%-6d GiB=%.2f' % (k, len(ds_by_top[k]), b / 1024 ** 3))

NUMERIC = 'numeric'


def is_num(fn):
    stem = os.path.splitext(fn)[0]
    return stem.isdigit()


print()
print('=== 供应商分证券（纯数字名）目录 -> D 侧覆盖 ===')
num_dirs = collections.defaultdict(lambda: [0, 0])
for x in pq:
    if not is_num(x['filename']):
        continue
    key = x['dir'].rstrip('/').split('/')[-1] if x['dir'] else '<root>'
    a = num_dirs[key]
    a[0] += 1
    a[1] += x['size']

covered = set()
for x in ds:
    covered.add(x['fn'])

for k in sorted(num_dirs, key=lambda k: -num_dirs[k][1]):
    n, b = num_dirs[k]
    tag = 'COVERED' if k in covered else 'D侧无同名目录'
    print('  %-34s n=%-6d GiB=%7.2f  %s' % (k[:34], n, b / 1024 ** 3, tag))

print()
print('=== 供应商具名表（非分证券）-> D 侧 basename 匹配 ===')
named = [x for x in pq if not is_num(x['filename'])]
gn = collections.defaultdict(lambda: [0, 0, set()])
for x in named:
    a = gn[x['filename']]
    a[0] += 1
    a[1] += x['size']
    a[2].add(x['dir'].split('/')[-1] if x['dir'] else '')

hit = [k for k in gn if k in covered]
miss = [k for k in gn if k not in covered]
print('  具名 parquet basename 数=%d  D侧同名=%d  D侧无同名=%d' % (
    len(gn), len(hit), len(miss)))
print('  --- D 侧已同名（供应商重复交付既有表）---')
for k in sorted(hit):
    a = gn[k]
    print('    %-44s n=%-4d GiB=%6.3f  ctx=%s' % (
        k[:44], a[0], a[1] / 1024 ** 3, list(a[2])[:2]))
print('  --- D 侧无同名（潜在新增能力）top 60 ---')
for k in sorted(miss, key=lambda k: -gn[k][1])[:60]:
    a = gn[k]
    print('    %-44s n=%-4d GiB=%6.3f  ctx=%s' % (
        k[:44], a[0], a[1] / 1024 ** 3, list(a[2])[:2]))