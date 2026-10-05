import json
import glob
import os
import re
import collections

C = r'D:\QuantLab\browse\crawl'
OUT = r'D:\QuantLab\browse\_all_files.jsonl'

TAGS = {
    'c3a2365f2cac': '1.parquet格式数据',
    'f1732d2e8e2a': '1.2parquet格式股票数据',
    'ce4b5b27f15e': '每日A股日线行情数据parquet（日更）',
}

recs = []
for p in glob.glob(os.path.join(C, '*.json')):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    if d.get('ok'):
        recs.append(d)

parent_of = {}
child_parent = {}
for r in recs:
    for d in r.get('dirs') or []:
        k = (r['tag'], d['fid'])
        parent_of[k] = (r['tag'], r['fid'])
        child_parent[k] = d.get('filename') or ''


def path_of(tag, fid):
    parts = []
    cur = (tag, fid)
    guard = 0
    while cur in child_parent and guard < 40:
        nm = child_parent[cur]
        if nm:
            parts.append(nm)
        nxt = parent_of.get(cur)
        if not nxt:
            break
        cur = nxt
        guard += 1
    return '/'.join(reversed(parts))


VER = re.compile(r'(20\d{2})[.\-/年]?(\d{1,2})?[.\-/月]?(\d{1,2})?')


def ver_key(path):
    m = None
    for m in VER.finditer(path or ''):
        pass
    if not m:
        return ''
    g = m.groups()
    y = g[0]
    mo = int(g[1]) if g[1] else 0
    dd = int(g[2]) if g[2] else 0
    return '%s%02d%02d' % (y, mo, dd)


def role_of(path):
    p = path or ''
    if '增量' in p or '增量数据' in p or '修补' in p or '补充包' in p or '缺失' in p:
        return 'INCR'
    if '修复版' in p:
        return 'FIX'
    if '_backup' in p or '备份' in p:
        return 'BACKUP'
    if re.search(r'更新至20\d{2}', p) or re.search(r'更新到20\d{2}', p):
        return 'FULL'
    return 'OTHER'


rows = []
for r in recs:
    base = path_of(r['tag'], r['fid'])
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        rows.append({
            'tag': r['tag'],
            'share': TAGS.get(r['tag'], r['tag']),
            'dir': base,
            'path': (base + '/' + fn) if base else fn,
            'filename': fn,
            'ext': os.path.splitext(fn)[1].lower(),
            'size': int(f.get('size') or 0),
            'updated_at': f.get('updated_at'),
            'fid': f.get('fid'),
            'role': role_of(base),
            'ver': ver_key(base),
        })

with open(OUT, 'w', encoding='utf-8') as fh:
    for x in rows:
        fh.write(json.dumps(x, ensure_ascii=False) + '\n')

pq = [x for x in rows if x['ext'] == '.parquet']
print('=== 抓取汇总 ===')
print('目录缓存=%d  文件记录=%d  parquet=%d  总GiB=%.2f' % (
    len(recs), len(rows), len(pq), sum(x['size'] for x in rows) / 1024 ** 3))

print('=== 按分享 ===')
for t, nm in TAGS.items():
    sub = [x for x in rows if x['tag'] == t]
    print('  %-14s files=%-7d parquet=%-7d GiB=%.2f' % (
        t, len(sub), len([x for x in sub if x['ext'] == '.parquet']),
        sum(x['size'] for x in sub) / 1024 ** 3))

print('=== 按扩展名 ===')
for e, n in collections.Counter(x['ext'] for x in rows).most_common(12):
    b = sum(x['size'] for x in rows if x['ext'] == e)
    print('  %-10s n=%-7d GiB=%.2f' % (e or '<none>', n, b / 1024 ** 3))

print('=== 按角色 ===')
for r_, n in collections.Counter(x['role'] for x in pq).most_common():
    b = sum(x['size'] for x in pq if x['role'] == r_)
    print('  %-8s n=%-7d GiB=%.2f' % (r_, n, b / 1024 ** 3))

print('=== 跨分享同名同体积重复交付（去重前后） ===')
sig = collections.defaultdict(list)
for x in pq:
    sig[(x['filename'], x['size'])].append(x)
dup = {k: v for k, v in sig.items() if len(set(y['tag'] for y in v)) > 1}
uniq_bytes = sum(x['size'] for x in {k: v[0] for k, v in sig.items()}.values())
print('  唯一(名称+体积)条目=%d  去重后GiB=%.2f' % (len(sig), uniq_bytes / 1024 ** 3))
print('  跨分享重复条目=%d' % len(dup))

print('=== 业务类目录（去掉版本层）===')
biz = collections.defaultdict(lambda: [0, 0, set()])
for x in pq:
    parts = (x['dir'] or '').split('/')
    if TAGS.get(x['tag']) and parts and parts[0] == TAGS[x['tag']]:
        parts = parts[1:]
    key = '/'.join(parts[:2]) if len(parts) >= 2 else '/'.join(parts)
    a = biz[key]
    a[0] += 1
    a[1] += x['size']
    a[2].add(x['role'])
for k in sorted(biz, key=lambda k: -biz[k][1]):
    n, b, rr = biz[k]
    print('  %-58s n=%-6d GiB=%7.2f roles=%s' % (
        k[:58], n, b / 1024 ** 3, ','.join(sorted(rr))))
print('WROTE %s' % OUT)