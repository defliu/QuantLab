import json
import glob
import os
import collections

C = r'D:\QuantLab\browse\crawl'
recs = []
for p in glob.glob(os.path.join(C, '*.json')):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    if d.get('ok'):
        recs.append(d)

parent_of = {}
dir_name = {}
for r in recs:
    dir_name[(r['tag'], r['fid'])] = r.get('name') or ''
    for d in r.get('dirs') or []:
        parent_of[(r['tag'], d['fid'])] = (r['tag'], r['fid'])

child_parent = {}
for r in recs:
    for d in r.get('dirs') or []:
        nm = d.get('filename') or ''
        child_parent[(r['tag'], d['fid'])] = nm


def path_of(tag, fid):
    parts = []
    cur = (tag, fid)
    guard = 0
    while cur in child_parent and guard < 40:
        nm = child_parent[cur]
        if nm and nm != '<root>':
            parts.append(nm)
        nxt = parent_of.get(cur)
        if not nxt:
            break
        cur = nxt
        guard += 1
    return '/'.join(reversed(parts))


agg = collections.defaultdict(lambda: [0, 0])
for r in recs:
    base = path_of(r['tag'], r['fid'])
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        if not fn.lower().endswith('.parquet'):
            continue
        a = agg[base]
        a[0] += 1
        a[1] += int(f.get('size') or 0)

print('=== 供应商目录 x parquet 汇总 (共 %d 目录) ===' % len(agg))
for k in sorted(agg, key=lambda k: -agg[k][1]):
    n, b = agg[k]
    print('%-96s n=%-6d GiB=%8.2f' % (k[:96], n, b / 1024 ** 3))