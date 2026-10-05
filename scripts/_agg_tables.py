import json
import glob
import os
import re
import collections

_OUT = open(r'D:\QuantLab\browse\_agg_tables.txt', 'w', encoding='utf-8')


def szf(n):
    if n >= 1024.0 ** 3:
        return '%.2fGiB' % (n / 1024.0 ** 3)
    if n >= 1024.0 ** 2:
        return '%.1fMiB' % (n / 1024.0 ** 2)
    return '%.0fB' % n


_p = print


def print(*a, **k):
    k['file'] = _OUT
    _p(*a, **k)

C = r'D:\QuantLab\browse\crawl'
E = r'E:\temp\行情数据'

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
        files.append({
            'tag': k[0],
            'dir': dp,
            'fn': fn,
            'stem': stem,
            'size': int(f.get('size') or 0),
            'ext': os.path.splitext(fn)[1].lower(),
        })

print('=' * 100)
print('PART 1  AGGREGATE (non-per-security) FILES  -- candidate D-side tables')
print('=' * 100)
print('criterion: filename stem is NOT a bare 6-digit security code')
print('')

agg = collections.defaultdict(lambda: {
    'n': 0, 'b': 0, 'dirs': set(), 'tags': set(),
    'variants': collections.Counter(),
})

for x in files:
    if x['ext'] != '.parquet':
        continue
    base = strip_dup(x['stem'])
    if CODE.match(base):
        continue
    a = agg[base]
    a['n'] += 1
    a['b'] += x['size']
    a['dirs'].add(x['dir'])
    a['tags'].add(x['tag'])
    a['variants'][x['stem']] += 1

rows = sorted(agg.items(), key=lambda kv: -kv[1]['n'])

print('%-52s %7s %11s %s' % ('STEM', 'FILES', 'SIZE', 'DIRS'))
print('-' * 100)
for base, a in rows:
    sz = '%.1fMiB' % (a['b'] / 1024.0 ** 2) if a['b'] < 1024 ** 3 \
        else '%.2fGiB' % (a['b'] / 1024.0 ** 3)
    d = sorted(a['dirs'])
    ds = d[0][:44] if d else ''
    if len(d) > 1:
        ds += '  +%d' % (len(d) - 1)
    print('%-52s %7d %11s %s' % (base[:52], a['n'], sz, ds))

print('')
print('aggregate stems=%d  files=%d' % (len(agg), sum(a['n'] for a in agg.values())))

print('')
print('=' * 100)
print('PART 2  PER-SECURITY FILE GROUPS (by dataset dir)')
print('=' * 100)

per = collections.defaultdict(lambda: {'n': 0, 'b': 0, 'codes': set()})

for x in files:
    if x['ext'] != '.parquet':
        continue
    base = strip_dup(x['stem'])
    m = CODE.match(base)
    if not m:
        continue
    p = per[x['dir']]
    p['n'] += 1
    p['b'] += x['size']
    p['codes'].add(m.group(1))

print('%-58s %7s %11s %7s' % ('DIR', 'FILES', 'SIZE', 'CODES'))
print('-' * 100)
for d, p in sorted(per.items(), key=lambda kv: -kv[1]['b']):
    sz = '%.2fGiB' % (p['b'] / 1024.0 ** 3) if p['b'] >= 1024 ** 3 \
        else '%.1fMiB' % (p['b'] / 1024.0 ** 2)
    print('%-58s %7d %11s %7d' % (d[:58], p['n'], sz, len(p['codes'])))

print('')
print('per-security dirs=%d' % len(per))

print('')
print('=' * 100)
print('PART 3  NON-PARQUET FILES (archives / docs / dbs)')
print('=' * 100)

oth = collections.defaultdict(lambda: {'n': 0, 'b': 0, 'names': set()})

for x in files:
    if x['ext'] == '.parquet':
        continue
    o = oth[x['ext'] or '(none)']
    o['n'] += 1
    o['b'] += x['size']
    o['names'].add(x['fn'])

for e, o in sorted(oth.items(), key=lambda kv: -kv[1]['b']):
    sz = '%.2fGiB' % (o['b'] / 1024.0 ** 3) if o['b'] >= 1024 ** 3 \
        else '%.1fMiB' % (o['b'] / 1024.0 ** 2)
    print('')
    print('### %s  n=%d  %s' % (e, o['n'], sz))
    for nm in sorted(o['names'])[:40]:
        print('    %s' % nm[:90])
    if len(o['names']) > 40:
        print('    ... +%d more' % (len(o['names']) - 40))

print('')
print('=' * 100)
print('PART 4  E:\\temp\\行情数据  ALREADY-REVIEWED DELIVERY')
print('=' * 100)

if os.path.isdir(E):
    for root, dirs, fns in os.walk(E):
        for fn in fns:
            fp = os.path.join(root, fn)
            try:
                st = os.stat(fp)
            except Exception:
                continue
            rel = os.path.relpath(fp, E)
            print('%-70s %12.2f MiB' % (rel[:70], st.st_size / 1024.0 ** 2))
else:
    print('  [MISSING] %s' % E)