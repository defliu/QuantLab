import json
import glob
import os
import re
import sys

C = r'D:\QuantLab\browse\crawl'

recs = {}

for p in glob.glob(os.path.join(C, '*.json')):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    if d.get('ok'):
        recs[(d['tag'], d['fid'])] = d

parent_of = {}
child_name = {}

for k, r in recs.items():
    for x in r.get('dirs') or []:
        ck = (r['tag'], x['fid'])
        parent_of[ck] = k
        child_name[ck] = x.get('filename') or ''


def path_of(k):
    parts = []
    cur = k
    g = 0
    while cur in parent_of and g < 60:
        nm = child_name.get(cur)
        if nm:
            parts.append(nm)
        cur = parent_of[cur]
        g += 1
    return '/'.join(reversed(parts))


targets = sys.argv[1:]

for k in sorted(recs.keys(), key=lambda z: (path_of(z), z[1])):
    dp = path_of(k)
    if not any(t in dp for t in targets):
        continue
    r = recs[k]
    print('')
    print('#' * 100)
    print('DIR  %s' % dp)
    print('     tag=%s pages=%s n_files=%s n_dirs=%s bytes=%.2f MiB'
          % (k[0], r.get('pages'), r.get('n_files'), r.get('n_dirs'),
             r.get('bytes', 0) / 1024.0 ** 2))
    print('#' * 100)
    fs = sorted(r.get('files') or [],
                key=lambda x: x.get('filename') or '')
    for f in fs[:80]:
        print('   %-58s %14d' % ((f.get('filename') or '')[:58],
                                  int(f.get('size') or 0)))
    if len(fs) > 80:
        print('   ... +%d more' % (len(fs) - 80))
    for d in sorted(r.get('dirs') or [],
                    key=lambda x: x.get('filename') or ''):
        print('   [DIR] %s' % (d.get('filename') or ''))