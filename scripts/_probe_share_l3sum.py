import glob
import json
import os

BROWSE = r'D:\QuantLab\browse'
OUT = r'D:\QuantLab\scripts\_probe_share_l3sum.out.txt'
SIZE = 100

TAGS = ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']


def rows_of(path):
    with open(path, 'rb') as fb:
        raw = fb.read()
    enc = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'
    if raw[:3] == b'\xef\xbb\xbf':
        raw = raw[3:]
        enc = 'utf-8'
    for line in raw.decode(enc, errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            pass


def parse_dir_page(path):
    res = {'ok': False, 'code': None, 'msg': '', 'files': [], 'dirs': [],
           'share': None, 'count': None}
    for r in rows_of(path):
        t = r.get('type')
        if t == 'error':
            res['code'] = r.get('code')
            res['msg'] = str(r.get('message'))[:80]
            continue
        if t != 'result':
            continue
        d = r.get('data') or {}
        if d.get('share_info'):
            res['share'] = d['share_info']
        for f in (d.get('files') or []):
            if f.get('file_type') == '0':
                res['dirs'].append(f)
            else:
                res['files'].append(f)
        res['count'] = d.get('file_count')
        res['ok'] = True
    return res


def gib(b):
    return b / 1073741824.0


def main():
    lines = []
    W = lines.append

    # tag + L3 fid8 -> directory name, harvested from L2 listings
    name_by = {}
    for p in glob.glob(os.path.join(BROWSE, 'L2_*.jsonl')):
        stem = os.path.basename(p)[:-len('.jsonl')]
        tag = stem.split('_')[1]
        for r in rows_of(p):
            if r.get('type') != 'result':
                continue
            for f in ((r.get('data') or {}).get('files') or []):
                if f.get('file_type') == '0':
                    name_by[(tag, f['fid'][:8])] = f.get('filename') or ''

    per_tag = {}
    total_files = 0
    total_bytes = 0
    bad = []
    full_pages = []
    l4_targets = []

    for p in sorted(glob.glob(os.path.join(BROWSE, 'L3_*.jsonl'))):
        base = os.path.basename(p)[:-len('.jsonl')]
        parts = base.split('_')
        tag = parts[1]
        fid8 = parts[2]
        res = parse_dir_page(p)
        dname = name_by.get((tag, fid8), '?')
        nf = len(res['files'])
        nd = len(res['dirs'])
        nb = sum(int(f.get('size') or 0) for f in res['files'])
        total_files += nf
        total_bytes += nb
        t = per_tag.setdefault(tag, {'dirs': 0, 'files': 0, 'bytes': 0, 'sub': 0})
        t['dirs'] += 1
        t['files'] += nf
        t['bytes'] += nb
        t['sub'] += nd

        flag = 'OK'
        if not res['ok']:
            flag = 'ERRPARSE code=%s %s' % (res['code'], res['msg'])
            bad.append((base, res['code'], res['msg']))
        n_items = nf + nd
        # file_count is the PAGE item count (files + dirs), not the dir total.
        # A full page means there are more pages we have not fetched.
        if res['count'] is not None and res['count'] != n_items:
            flag += ' COUNT_MISMATCH(%s vs %d)' % (res['count'], n_items)
        if n_items >= SIZE:
            flag += ' FULLPAGE'
            full_pages.append((tag, fid8, dname, n_items))
        if nd:
            flag += ' SUB%d' % nd
            for f in res['dirs']:
                l4_targets.append((tag, f['fid'], f.get('filename') or '',
                                   fid8, dname))
        W('%-13s %-9s files=%-5d dirs=%-3d %9.2f MiB  %-22s %s' % (
            tag, fid8, nf, nd, nb / 1048576.0, flag, dname))

    W('')
    W('=== 汇总（第三层当前页可见，非完整递归） ===')
    npages = len(glob.glob(os.path.join(BROWSE, 'L3_*.jsonl')))
    for tag in TAGS:
        t = per_tag.get(tag)
        if not t:
            W('%s : 无 L3 页面' % tag)
            continue
        W('%s : 目录=%d 文件=%d 子目录=%d 体积=%.2f GiB' % (
            tag, t['dirs'], t['files'], t['sub'], gib(t['bytes'])))
    W('合计: 页面=%d 目录=%d 文件=%d 体积=%.2f GiB (%.2f MiB)' % (
        npages, npages, total_files, gib(total_bytes), total_bytes / 1048576.0))
    W('')
    W('=== 失败页面 (%d) ===' % len(bad))
    for b in bad:
        W('%s code=%s %s' % b)
    W('')
    W('=== 满页=可能被截断需翻页 (%d) ===' % len(full_pages))
    for tag, fid8, dname, n in full_pages:
        W('%s %s items=%d %s' % (tag, fid8, n, dname))
    W('')
    W('=== 待展开第四层子目录 (%d) ===' % len(l4_targets))
    for tag, fid, name, pfid8, pname in l4_targets:
        W('%s %s  parent=%s(%s)  name=%s' % (tag, fid, pfid8, pname, name))

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    with open(os.path.join(BROWSE, '_l4_targets.json'), 'w',
              encoding='utf-8') as f:
        json.dump([{'tag': t, 'fid': f, 'name': n, 'pfid8': pf,
                    'pname': pn} for t, f, n, pf, pn in l4_targets],
                  f, ensure_ascii=False, indent=1)

    print('lines=%d full_pages=%d l4_targets=%d -> %s'
          % (len(lines), len(full_pages), len(l4_targets), OUT))


if __name__ == '__main__':
    main()