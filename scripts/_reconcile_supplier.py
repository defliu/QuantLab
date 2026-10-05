import json
import glob
import os
import re
import collections

C = r'D:\QuantLab\browse\crawl'
OUT = r'D:\QuantLab\browse\_reconcile.txt'

TAGS = {
    'c3a2365f2cac': '1.parquet格式数据',
    'f1732d2e8e2a': '1.2parquet格式股票数据',
    'ce4b5b27f15e': '每日A股日线行情数据parquet（日更）',
}

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


VER = re.compile(r'(20\d{2})[.\-/年]?(\d{1,2})?[.\-/月]?(\d{1,2})?')


def ver_of(path):
    m = None
    for m in VER.finditer(path or ''):
        pass
    if not m:
        return ''
    g = m.groups()
    return '%s%02d%02d' % (
        g[0], int(g[1]) if g[1] else 0, int(g[2]) if g[2] else 0)


VER_DIR = re.compile(
    r'更新[至到]\s*20\d{2}|'
    r'20\d{2}[.\-/年]\d{1,2}[.\-/月]?\d{0,2}[.\-/月]?\d{0,2}日?$|'
    r'^20\d{2}$|'
    r'parquet格式20\d{6}'
)


def strip_ver(parts):
    return [p for p in parts if not VER_DIR.search(p)]


def code_of(fn):
    stem = os.path.splitext(fn)[0]
    m = re.match(r'^(\d{6})(?:[._](SH|SZ|BJ))?$', stem, re.I)
    return m.group(1) if m else None


# ---------- D 侧 ----------
ds = json.load(open(
    r'D:\QuantLab\browse\_dside_files.json', encoding='utf-8'))

ds_top = collections.defaultdict(list)
ds_codes = collections.defaultdict(set)
ds_stems = collections.defaultdict(set)
for x in ds:
    ds_top[x['top']].append(x)
    stem = os.path.splitext(x['fn'])[0].lower()
    ds_stems[x['top']].add(stem)
    c = code_of(x['fn'])
    if c:
        ds_codes[x['top']].add(c)

# ---------- 供应商聚合 ----------
sup = collections.defaultdict(lambda: {
    'vers': collections.defaultdict(lambda: {'n': 0, 'b': 0, 'files': []}),
})

for r in recs:
    d = path_of(r['tag'], r['fid'])
    share = TAGS.get(r['tag'], r['tag'])
    parts = [p for p in d.split('/') if p]
    if parts and parts[0] == share:
        parts = parts[1:]
    key = '/'.join(strip_ver(parts))
    v = ver_of(d)
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        sz = int(f.get('size') or 0)
        b = sup[key]['vers'][v]
        b['n'] += 1
        b['b'] += sz
        b['files'].append((fn, sz))

PER_SEC = re.compile(r'^\d{6}[._](SH|SZ|BJ)', re.I)

MAP = {
    '1.parquet格式个股行情数据/stock_1min': 'min_1min',
    '1.parquet格式个股行情数据/stock_5min': 'min_5min',
    '1.parquet格式个股行情数据/stock_15min': 'min_15min',
    '1.parquet格式个股行情数据/stock_30min': 'min_30min',
    '1.parquet格式个股行情数据/stock_60min': 'min_60min',
    'parquet格式指数数据/index_1min': 'index',
    'parquet格式指数数据/index_5min': 'index',
    'parquet格式指数数据/index_15min': 'index',
    'parquet格式指数数据/index_30min': 'index',
    'parquet格式指数数据/index_60min': 'index',
    'parquet格式ETF数据/etf_1min': 'etf',
    'parquet格式ETF数据/etf_5min': 'etf',
    'parquet格式ETF数据/etf_15min': 'etf',
    'parquet格式ETF数据/etf_30min': 'etf',
    'parquet格式ETF数据/etf_60min': 'etf',
    '筹码分布数据': 'chip',
}

L = []
L.append('=== 供应商 -> D:/astock 生产对账（快照） ===')
L.append('供应商去版本层数据集=%d   D侧一级目录=%d' % (len(sup), len(ds_top)))
L.append('')

L.append('--- A. 分证券数据集：证券代码级覆盖 ---')
L.append('%-46s %-10s %7s %7s %7s %7s  %s' % (
    '供应商数据集', 'D侧目录', '供应码', '最新码', '命中', '缺失', '最新版本/覆盖率'))
a_rows = []
for k in sorted(MAP):
    tgt = MAP[k]
    rec = sup.get(k)
    if not rec:
        a_rows.append((k, tgt, 0, 0, 0, 0, '<未抓到>'))
        continue
    vs = [v for v in rec['vers'] if v]
    latest = max(vs) if vs else ''
    allc, latc = set(), set()
    for v, b in rec['vers'].items():
        for fn, _ in b['files']:
            c = code_of(fn)
            if c:
                allc.add(c)
                if v == latest:
                    latc.add(c)
    have = len(latc & ds_codes[tgt])
    a_rows.append((k, tgt, len(allc), len(latc), have,
                   len(latc) - have, latest))
for k, tgt, na, nl, hv, ms, lt in sorted(a_rows, key=lambda r: -r[3]):
    L.append('%-46s %-10s %7d %7d %7d %7d  %s / %.1f%%' % (
        k[:46], tgt, na, nl, hv, ms, lt or '-',
        100.0 * hv / max(1, nl)))

L.append('')
L.append('--- B. 聚合表数据集：表名 -> D侧归属 ---')
L.append('%-50s %-14s %6s %8s  %s' % (
    '供应商数据集', '最新版本', '文件', 'GiB', 'D侧匹配'))
b_rows = []
for k, rec in sup.items():
    if k in MAP:
        continue
    vs = [v for v in rec['vers'] if v]
    latest = max(vs) if vs else ''
    b = rec['vers'].get(latest) or {'n': 0, 'b': 0, 'files': []}
    pq = [f for f in b['files'] if f[0].lower().endswith('.parquet')]
    persec = sum(1 for f in pq if PER_SEC.match(f[0]))
    hit = []
    for fn, _ in pq:
        if PER_SEC.match(fn):
            continue
        stem = os.path.splitext(fn)[0].lower()
        cands = [t for t in ds_stems if t == stem]
        hit.append('%s->%s' % (stem, ','.join(sorted(cands)) or 'MISS'))
    hit = sorted(set(hit))
    shown = '; '.join(hit[:6]) if hit else '<全为分证券文件或无 parquet>'
    if len(hit) > 6:
        shown += ' ...(+%d)' % (len(hit) - 6)
    b_rows.append((k, latest or '-', b['n'], b['b'], persec, shown))

for k, lt, n, b, ps, shown in sorted(b_rows, key=lambda r: -r[3]):
    L.append('%-50s %-14s %6d %8.2f  %s' % (
        k[:50] or '<root>', lt, n, b / 1024 ** 3, shown[:130]))

L.append('')
L.append('--- C. D侧生产目录 vs 供应商供给 ---')
L.append('%-16s %6s %8s  %-5s %s' % ('D侧目录', '文件', 'GiB', '映射', '供应商相关数据集'))
matched_targets = set(MAP.values())
for t in sorted(ds_top):
    n = len(ds_top[t])
    b = sum(x['sz'] for x in ds_top[t]) / 1024 ** 3
    keyhit = sorted(k for k in sup if t in k.split('/') or k.endswith(t))
    L.append('%-16s %6d %8.2f  %-5s %s' % (
        t, n, b, 'Y' if t in matched_targets else '-',
        '; '.join(keyhit[:3]) if keyhit else '<无>'))

with open(OUT, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(L))

print('\n'.join(L))
print('WROTE %s' % OUT)