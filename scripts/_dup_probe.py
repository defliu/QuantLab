import json
import glob
import os
import re
import collections

_OUT = open(r'D:\QuantLab\browse\_dup_probe.txt', 'w', encoding='utf-8')
_p = print


def print(*a, **k):
    k['file'] = _OUT
    _p(*a, **k)


def szf(n):
    if n >= 1024.0 ** 3:
        return '%.2fGiB' % (n / 1024.0 ** 3)
    if n >= 1024.0 ** 2:
        return '%.1fMiB' % (n / 1024.0 ** 2)
    return '%dB' % n


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


CODE = re.compile(r'^(\d{6})(?:[._](SH|SZ|BJ))?$', re.I)
DUP = re.compile(r'\((\d+)\)$')


def strip_dup(stem):
    return DUP.sub('', stem).strip()


files = []

for k, r in recs.items():
    dp = path_of(k)
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        stem = os.path.splitext(fn)[0]
        ext = os.path.splitext(fn)[1].lower()
        m = CODE.match(strip_dup(stem))
        files.append({
            'tag': k[0],
            'dir': dp,
            'fn': fn,
            'stem': stem,
            'base': strip_dup(stem),
            'code': m.group(1) if m else None,
            'isdup': bool(DUP.search(stem)),
            'size': int(f.get('size') or 0),
            'ext': ext,
        })

print('=' * 104)
print('PART 1  (N)-SUFFIXED FILES  -- supplier duplicate delivery')
print('=' * 104)

dups = [x for x in files if x['isdup']]

print('total files=%d   (N)-suffixed=%d   bytes=%s'
      % (len(files), len(dups), szf(sum(x['size'] for x in dups))))

byext = collections.Counter(x['ext'] for x in dups)
print('by ext: %s' % dict(byext))

bydir = collections.defaultdict(lambda: {'n': 0, 'b': 0})
for x in dups:
    d = bydir[x['dir']]
    d['n'] += 1
    d['b'] += x['size']

print('')
print('%-62s %7s %11s' % ('DIR', 'DUPN', 'SIZE'))
print('-' * 104)
for d, v in sorted(bydir.items(), key=lambda kv: -kv[1]['n'])[:40]:
    print('%-62s %7d %11s' % (d[:62], v['n'], szf(v['b'])))

print('')
print('=' * 104)
print('PART 2  SAME (dir, code) APPEARING >1 TIME  -- real collision check')
print('=' * 104)

grp = collections.defaultdict(list)
for x in files:
    if x['code'] and x['ext'] == '.parquet':
        grp[(x['dir'], x['code'])].append(x)

multi = {k: v for k, v in grp.items() if len(v) > 1}

print('(dir,code) keys=%d   with>1 file=%d' % (len(grp), len(multi)))

byd = collections.defaultdict(lambda: {'n': 0, 'codes': 0, 'same': 0, 'diff': 0})
for (d, c), v in multi.items():
    e = byd[d]
    e['n'] += len(v)
    e['codes'] += 1
    if len(set(y['size'] for y in v)) == 1:
        e['same'] += 1
    else:
        e['diff'] += 1

print('')
print('%-58s %7s %7s %9s %7s' % ('DIR', 'CODES', 'FILES', 'SAME_SZ', 'DIFF_SZ'))
print('-' * 104)
for d, e in sorted(byd.items(), key=lambda kv: -kv[1]['codes'])[:40]:
    print('%-58s %7d %7d %9d %7d'
          % (d[:58], e['codes'], e['n'], e['same'], e['diff']))

print('')
print('--- samples: same (dir,code) ---')
for (d, c), v in sorted(multi.items(), key=lambda kv: (kv[0][0], kv[0][1]))[:25]:
    szs = set(y['size'] for y in v)
    tag = 'IDENTICAL_SIZE' if len(szs) == 1 else 'SIZE_DIFF'
    print('  %-44s %s  n=%d  %s  %s'
          % (d[-44:], c, len(v), tag,
             ', '.join('%s(%s)' % (y['fn'][:34], szf(y['size'])) for y in v)))

print('')
print('--- samples: SIZE_DIFF only (real content divergence) ---')
n = 0
for (d, c), v in sorted(multi.items(), key=lambda kv: (kv[0][0], kv[0][1])):
    if len(set(y['size'] for y in v)) == 1:
        continue
    n += 1
    if n > 30:
        break
    print('  %-40s %s' % (d[-40:], c))
    for y in sorted(v, key=lambda z: z['size']):
        print('        %-40s %12s  %s' % (y['fn'][:40], y['size'], szf(y['size'])))

print('')
print('=' * 104)
print('PART 3  PER-SECURITY DIR: files vs codes  (only dirs with files > codes)')
print('=' * 104)

per = collections.defaultdict(lambda: {'n': 0, 'b': 0, 'codes': set(), 'dupn': 0})
for x in files:
    if not x['code'] or x['ext'] != '.parquet':
        continue
    p = per[x['dir']]
    p['n'] += 1
    p['b'] += x['size']
    p['codes'].add(x['code'])
    if x['isdup']:
        p['dupn'] += 1

print('%-58s %7s %7s %7s %11s' % ('DIR', 'FILES', 'CODES', 'DUPN', 'SIZE'))
print('-' * 104)
for d, p in sorted(per.items(), key=lambda kv: -kv[1]['b']):
    if p['n'] <= len(p['codes']):
        continue
    print('%-58s %7d %7d %7d %11s'
          % (d[:58], p['n'], len(p['codes']), p['dupn'], szf(p['b'])))

print('')
print('=' * 104)
print('PART 4  NON-6DIGIT-CODE PER-SECURITY FILES  (intl index / future misclassified)')
print('=' * 104)

ncode = collections.Counter()

for x in files:
    if x['ext'] != '.parquet':
        continue
    if x['code']:
        continue
    b = x['base']
    if re.match(r'^[A-Za-z]{1,6}$', b) or re.match(r'^\d{3,5}$', b):
        ncode[(x['dir'], b)] += 1

agg = collections.defaultdict(lambda: {'n': 0, 'b': 0, 'dirs': set()})
for (d, b), n in ncode.items():
    a = agg[b]
    a['n'] += n
    a['dirs'].add(d)

print('distinct non-6digit stems=%d  total files=%d'
      % (len(agg), sum(a['n'] for a in agg.values())))
print('')
print('%-24s %7s  %s' % ('STEM', 'N', 'DIRS'))
print('-' * 104)
for b, a in sorted(agg.items(), key=lambda kv: -kv[1]['n'])[:50]:
    print('%-24s %7d  %s'
          % (b[:24], a['n'], sorted(a['dirs'])[0][-52:]))

_OUT.close()