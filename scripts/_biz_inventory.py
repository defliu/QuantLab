import json
import glob
import os
import re
import collections

C = r'D:\QuantLab\browse\crawl'
OUT = r'D:\QuantLab\browse\_biz_inventory.txt'

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
    for d in r.get('dirs') or []:
        k = (r['tag'], d['fid'])
        parent_of[k] = (r['tag'], r['fid'])
        child_parent[k] = d.get('filename') or ''


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
    y = g[0]
    mo = int(g[1]) if g[1] else 0
    dd = int(g[2]) if g[2] else 0
    return '%s%02d%02d' % (y, mo, dd)


rows = []
for r in recs:
    base = path_of(r['tag'], r['fid'])
    share = TAGS.get(r['tag'], r['tag'])
    for f in r.get('files') or []:
        fn = f.get('filename') or ''
        rows.append({
            'tag': r['tag'],
            'share': share,
            'dir': base,
            'filename': fn,
            'ext': os.path.splitext(fn)[1].lower(),
            'size': int(f.get('size') or 0),
        })

# dataset = 去掉版本层
def strip_ver(parts):
    out = []
    for p in parts:
        if re.search(r'更新[至到]\s*20\d{2}|20\d{2}[.\-/年]\d{1,2}[.\-/月]?\d{0,2}[.\-/月]?\d{0,2}日?$', p):
            continue
        out.append(p)
    return out


agg = collections.defaultdict(lambda: {
    'n': 0, 'b': 0, 'vers': collections.Counter(),
    'names': collections.Counter(), 'tags': set(),
})
for x in rows:
    parts = [p for p in (x['dir'] or '').split('/') if p]
    if parts and parts[0] == x['share']:
        parts = parts[1:]
    key = '/'.join(strip_ver(parts))
    a = agg[key]
    a['n'] += 1
    a['b'] += x['size']
    a['vers'][ver_of(x['dir'])] += 1
    a['tags'].add(x['tag'])
    if x['ext'] == '.parquet':
        stem = os.path.splitext(x['filename'])[0]
        stem = re.sub(r'\d{6}', '{code}', stem)
        stem = re.sub(r'\d{8}', '{date}', stem)
        stem = re.sub(r'\d{6,}', '{num}', stem)
        a['names'][stem] += 1

out = []
out.append('=== 数据集清单（按目录去版本层聚合） ===')
out.append('数据集数=%d' % len(agg))
for k in sorted(agg, key=lambda k: -agg[k]['b']):
    a = agg[k]
    vs = [v for v in a['vers'] if v]
    vs.sort()
    out.append('')
    out.append('目录: %s' % (k or '<root>'))
    out.append('  文件数=%d  体积=%.2f GiB  分享=%s' % (
        a['n'], a['b'] / 1024 ** 3, ','.join(sorted(a['tags']))))
    out.append('  版本层: %s' % (','.join(vs) if vs else '<无版本层>'))
    top = a['names'].most_common(4)
    out.append('  文件名模式: %s' % '; '.join('%s x%d' % (s, c) for s, c in top))

with open(OUT, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(out))

print('datasets=%d' % len(agg))
print('WROTE %s' % OUT)