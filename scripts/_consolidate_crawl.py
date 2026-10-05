import collections
import glob
import json
import os
import re

CRAWL = r'D:\QuantLab\browse\crawl'
OUT = r'D:\QuantLab\browse'

TAGS = ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']

recs = []
for p in glob.glob(os.path.join(CRAWL, '*.json')):
    if p.endswith('.tmp'):
        continue
    try:
        with open(p, 'r', encoding='utf-8') as f:
            recs.append(json.load(f))
    except Exception:
        pass

print('crawl_records=%d' % len(recs))

by_tag = collections.Counter(r['tag'] for r in recs)
by_depth = collections.Counter(r['depth'] for r in recs)
print('by_tag=%s' % dict(by_tag))
print('by_depth=%s' % dict(sorted(by_depth.items())))

# Build parent->child path map
child_parent = {}
for r in recs:
    child_parent[(r['tag'], r['fid'])] = r['name']


# Reconstruct parent links: a dir D is a child of record R if D.fid in R.dirs
parent_of = {}
for r in recs:
    for d in r.get('dirs') or []:
        parent_of[(r['tag'], d['fid'])] = (r['tag'], r['fid'])


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


rows = []
for r in recs:
    base = path_of(r['tag'], r['fid'])
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        rows.append({
            'tag': r['tag'],
            'dir': base,
            'filename': fn,
            'ext': os.path.splitext(fn)[1].lower(),
            'size': int(f.get('size') or 0),
            'updated_at': f.get('updated_at'),
            'fid': f.get('fid'),
        })

print('total_file_rows=%d' % len(rows))

ext = collections.Counter(r['ext'] for r in rows)
print('\n=== EXT DISTRIBUTION ===')
for e, c in ext.most_common(30):
    tot = sum(r['size'] for r in rows if r['ext'] == e)
    print('  %-12s %8d  %14d bytes  (%.2f GiB)'
          % (e or '<none>', c, tot, tot / 1024.0 ** 3))

pq = [r for r in rows if r['ext'] == '.parquet']
print('\nparquet_files=%d  bytes=%d (%.2f GiB)'
      % (len(pq), sum(r['size'] for r in pq),
         sum(r['size'] for r in pq) / 1024.0 ** 3))

with open(os.path.join(OUT, '_all_files.jsonl'), 'w', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

print('wrote %s' % os.path.join(OUT, '_all_files.jsonl'))

# top-level business dirs
print('\n=== TOP LEVEL DIRS (depth1) ===')
d1 = collections.Counter()
for r in recs:
    if r['depth'] == 1:
        d1[(r['tag'], r['name'])] += r['n_files']
for (t, n), c in sorted(d1.items()):
    print('  %s  %-52s files>=%d' % (t, n[:52], c))