import json
import glob
import os

C = r'D:\QuantLab\browse\crawl'
TAG = 'ce4b5b27f15e'

recs = []
for p in glob.glob(os.path.join(C, '*.json')):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    if d.get('ok') and d.get('tag') == TAG:
        recs.append(d)

parent_of = {}
child_parent = {}
for r in recs:
    for d in r.get('dirs') or []:
        parent_of[(r['tag'], d['fid'])] = (r['tag'], r['fid'])
        child_parent[(r['tag'], d['fid'])] = d.get('filename') or ''


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


print('=== TAG %s 目录数=%d ===' % (TAG, len(recs)))
for r in recs:
    base = path_of(TAG, r['fid'])
    print('--- DIR depth=%d pages=%d files=%d dirs=%d GiB=%.3f | %s' % (
        r['depth'], r.get('pages', 0), r['n_files'], r['n_dirs'],
        r['bytes'] / 1024 ** 3, base or '<root>'))
    for f in r.get('files') or []:
        print('      %-58s %8.2f MiB  upd=%s' % (
            f.get('filename') or '',
            int(f.get('size') or 0) / 1024 ** 2,
            f.get('updated_at')))