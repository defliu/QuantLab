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

known = set((r['tag'], r['fid']) for r in recs)
pend = []
for r in recs:
    for x in r.get('dirs') or []:
        k = (r['tag'], x['fid'])
        if k not in known:
            pend.append((r['depth'] + 1, k[0], r['name'], x.get('filename')))

print('pending=%d' % len(pend))
for d, t, pn, cn in sorted(pend):
    print('  d%d %s | %-46s -> %s' % (d, t, pn[:46], cn))

done_big = sorted(
    [r for r in recs if r['n_files'] > 200],
    key=lambda r: -r['n_files'],
)[:12]
print('=== 已完成的大目录 top12 (pages/files) ===')
for r in done_big:
    print('  pages=%-4s files=%-6s %s' % (
        r.get('pages'), r['n_files'], r.get('name')))