import json
import glob
import os
import sys

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
child_parent = {}

for r in recs:
    for x in r.get('dirs') or []:
        k = (r['tag'], x['fid'])
        parent_of[k] = (r['tag'], r['fid'])
        child_parent[k] = x.get('filename') or ''


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


targets = sys.argv[1:]

if targets:
    for t in targets:
        tag = None
        fid = t
        if '__' in t:
            tag, fid = t.split('__', 1)
        hit = None
        for (tg, fd), nm in child_parent.items():
            if fd == fid and (tag is None or tg == tag):
                hit = (tg, fd)
                break
        if hit:
            print('%s -> %s' % (t, path_of(hit[0], hit[1])))
        else:
            print('%s -> NOT FOUND in cache' % t)

print('--- top pending by depth ---')
known = set((r['tag'], r['fid']) for r in recs)
pend = []

for r in recs:
    for x in r.get('dirs') or []:
        k = (r['tag'], x['fid'])
        if k not in known:
            pend.append((r['depth'] + 1, r['tag'], r['name'], x.get('filename')))

for d in sorted(pend):
    print('d%s %s | %s -> %s' % d)
print('pending=%d' % len(pend))
