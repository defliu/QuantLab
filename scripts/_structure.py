import json
import glob
import os

C = r'D:\QuantLab\browse\crawl'
NAMES = {
    'c3a2365f2cac': 'S1 1.parquet格式数据',
    'f1732d2e8e2a': 'S2 1.2parquet格式股票数据',
    'ce4b5b27f15e': 'S3 每日A股日线行情(日更)',
}

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

kids = {}

for k in list(parent_of) + [('c3a2365f2cac', '0'),
                            ('f1732d2e8e2a', '0'),
                            ('ce4b5b27f15e', '0')]:
    kids.setdefault(k, [])
for k, p in parent_of.items():
    kids.setdefault(p, []).append(k)

for p in kids:
    kids[p] = sorted(set(kids[p]), key=lambda z: child_name.get(z, ''))


def agg(k):
    """Aggregate subtree totals. Returns (files, bytes, pending_dirs)."""
    stack = [(k, False)]
    seen = set()
    n = b = u = 0
    while stack:
        cur, done = stack.pop()
        if done:
            n += CN[cur]
            b += CB[cur]
            u += CU[cur]
            continue
        if cur in seen:
            continue
        seen.add(cur)
        stack.append((cur, True))
        for c in kids.get(cur, []):
            if c not in seen:
                stack.append((c, False))
    return n, b, u


CN = {}
CB = {}
CU = {}

_pending_roots = set()


def fill(k):
    st = [(k, False)]
    seen = set()
    order = []

    while st:
        cur, done = st.pop()
        if done:
            order.append(cur)
            continue
        if cur in seen:
            continue
        seen.add(cur)
        st.append((cur, True))
        for c in kids.get(cur, []):
            if c not in seen:
                st.append((c, False))

    for cur in order:
        r = recs.get(cur)
        n = 0
        b = 0
        u = 0
        if r is None:
            u = 1
            _pending_roots.add(cur)
        else:
            n += r['n_files']
            b += r['bytes']
            for c in kids.get(cur, []):
                n += CN.get(c, 0)
                b += CB.get(c, 0)
                u += CU.get(c, 0)
        CN[cur] = n
        CB[cur] = b
        CU[cur] = u


def sz(n):
    if n >= 1024.0 ** 3:
        return '%.2fGiB' % (n / 1024.0 ** 3)
    if n >= 1024.0 ** 2:
        return '%.1fMiB' % (n / 1024.0 ** 2)
    if n >= 1024.0:
        return '%.1fKiB' % (n / 1024.0)
    return '%dB' % n


for t in ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']:
    fill((t, '0'))


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


out = []

out.append('=' * 96)
out.append('SUPPLIER SHARE STRUCTURE  (recursive, live crawl cache)')
out.append('=' * 96)

for t in ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']:
    root = (t, '0')
    if root not in recs:
        continue
    out.append('')
    out.append('### %s   tag=%s' % (NAMES[t], t))
    out.append('  TOTAL files=%-8d %-10s unfinished_dirs=%d'
               % (CN[root], sz(CB[root]), CU[root]))
    out.append('')

    def rec(k, prefix, depth, maxdepth):
        if depth > maxdepth:
            return
        for c in kids.get(k, []):
            nm = child_name.get(c, '?')
            n, b, u = CN.get(c, 0), CB.get(c, 0), CU.get(c, 0)
            if c not in recs:
                out.append('%s[?] %-44s  PENDING' % (prefix, nm[:44]))
                continue
            mark = '  *%d' % u if u else ''
            out.append('%s%-44s n=%-7d %9s%s'
                       % (prefix, nm[:44], n, sz(b), mark))
            if depth < maxdepth:
                rec(c, prefix + '  | ', depth + 1, maxdepth)

    rec(root, '  ', 1, 2)

out.append('')
out.append('=' * 96)
out.append('PENDING DIRECTORIES (not yet crawled)')
out.append('=' * 96)
for k in sorted(_pending_roots, key=lambda z: (z[0], path_of(z))):
    out.append('  %s  %s' % (k[0][:8], path_of(k)))

txt = '\n'.join(out)
open(r'D:\QuantLab\browse\_structure.txt', 'w', encoding='utf-8').write(txt)
print(txt)