import collections
import glob
import json
import os
import re

CRAWL = r'D:\QuantLab\browse\crawl'
DROOT = r'D:\astock'

SKIP_DIR_PARTS = ('_incremental', 'Updatedata', '.git', 'crawl')


def scan_cached():
    recs = {}
    for p in glob.glob(os.path.join(CRAWL, '*.json')):
        if p.endswith('.tmp'):
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get('ok'):
            recs[(d['tag'], d['fid'])] = d
    return recs


recs = scan_cached()
print('crawl_dirs=%d' % len(recs))

parent_of = {}
for (t, fid), r in recs.items():
    for d in r.get('dirs') or []:
        if d.get('fid'):
            parent_of[(t, d['fid'])] = (t, fid)

name_of = {k: v['name'] for k, v in recs.items()}


def path_of(tag, fid):
    parts = []
    cur = (tag, fid)
    guard = 0
    while cur in name_of and guard < 50:
        nm = name_of[cur]
        if nm and nm != '<root>':
            parts.append(nm)
        nxt = parent_of.get(cur)
        if not nxt:
            break
        cur = nxt
        guard += 1
    return '/'.join(reversed(parts))


rows = []
for (t, fid), r in recs.items():
    base = path_of(t, fid)
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        rows.append({
            'tag': t, 'dir': base, 'filename': fn,
            'ext': os.path.splitext(fn)[1].lower(),
            'size': int(f.get('size') or 0),
            'updated_at': f.get('updated_at'),
        })

print('file_rows=%d' % len(rows))

# ---------- D side inventory ----------
dside = collections.defaultdict(lambda: {'n': 0, 'bytes': 0, 'names': set()})
for dirpath, dirnames, filenames in os.walk(DROOT):
    rel = os.path.relpath(dirpath, DROOT)
    top = rel.split(os.sep)[0]
    if top.upper() in ('_INCREMENTAL', 'UPDATEDATA', '.GIT'):
        continue
    for fn in filenames:
        if fn.lower().endswith('.parquet'):
            s = dside[top]
            s['n'] += 1
            s['bytes'] += os.path.getsize(os.path.join(dirpath, fn))
            s['names'].add(os.path.splitext(fn)[0].lower())

print('\n=== D SIDE PRODUCTION (top dirs) ===')
tn = tb = 0
for k in sorted(dside):
    v = dside[k]
    tn += v['n']
    tb += v['bytes']
    print('  %-16s %6d  %8.2f MiB' % (k, v['n'], v['bytes'] / 1024.0 ** 2))
print('  %-16s %6d  %8.2f GiB' % ('TOTAL', tn, tb / 1024.0 ** 3))

# ---------- supplier logical tables (parquet basenames) ----------
pq = [r for r in rows if r['ext'] == '.parquet']
print('\nsupplier_parquet_files=%d  (%.2f GiB)'
      % (len(pq), sum(r['size'] for r in pq) / 1024.0 ** 3))

sup_tables = collections.defaultdict(lambda: {'n': 0, 'bytes': 0, 'dirs': set()})
for r in pq:
    stem = os.path.splitext(r['filename'])[0].lower()
    e = sup_tables[stem]
    e['n'] += 1
    e['bytes'] += r['size']
    e['dirs'].add(r['dir'])

print('supplier_distinct_basenames=%d' % len(sup_tables))

# non-numeric basenames = real logical tables; numeric = per-stock/per-code files
logical = {k: v for k, v in sup_tables.items() if not re.fullmatch(r'\d{6}', k)}
numeric = {k: v for k, v in sup_tables.items() if re.fullmatch(r'\d{6}', k)}
print('  logical_tables=%d' % len(logical))
print('  per_code_files=%d  (dirs=%d)'
      % (sum(v['n'] for v in numeric.values()), len(numeric)))

# ---------- coverage of D side tables ----------
d_tables = set()
for k, v in dside.items():
    d_tables |= v['names']

print('\n=== COVERAGE: D-side table -> supplier ===')
covered, missing = [], []
for t in sorted(d_tables):
    if t in sup_tables:
        covered.append(t)
    else:
        missing.append(t)

print('D-side logical tables=%d' % len(d_tables))
print('  found in supplier   =%d' % len(covered))
print('  NOT found in supplier=%d' % len(missing))
print('\n-- D side tables with NO supplier match (first 80) --')
for m in missing[:80]:
    print('   ', m)

print('\n-- supplier logical tables NOT present on D side (first 120) --')
extra = [k for k in sorted(logical) if k not in d_tables]
print('   count=%d' % len(extra))
for k in extra[:120]:
    e = logical[k]
    print('    %-34s n=%-5d %8.1f MiB  dirs=%d'
          % (k, e['n'], e['bytes'] / 1024.0 ** 2, len(e['dirs'])))

with open(r'D:\QuantLab\browse\_all_files.jsonl', 'w', encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print('\nwrote _all_files.jsonl rows=%d' % len(rows))