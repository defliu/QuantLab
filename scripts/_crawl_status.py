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
pend = collections.Counter()
pend_names = []
for r in recs:
    for x in r.get('dirs') or []:
        k = (r['tag'], x['fid'])
        if k not in known:
            pend[k[0]] += 1
            pend_names.append((r['depth'] + 1, k[0], r['name'], x.get('filename')))

by_tag = collections.Counter(r['tag'] for r in recs)
print('recs=%d files=%d GiB=%.2f pending=%d' % (
    len(recs),
    sum(r['n_files'] for r in recs),
    sum(r['bytes'] for r in recs) / 1024 ** 3,
    sum(pend.values()),
))
print('tag_dirs=%s' % dict(by_tag))
print('pending_by_tag=%s' % dict(pend))
for d, t, pn, cn in pend_names[:40]:
    print('  d%d %s | %s -> %s' % (d, t, pn[:34], cn[:56]))