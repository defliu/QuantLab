"""Dump the supplier share catalogue: category tree + real table filenames.

share_info.all_file_num at the share root is the authoritative recursive file
count, so full recursion to file level is not required to size the delivery.
What we DO need is the table-level mapping: which parquet tables the supplier
actually ships, so we can diff them against the D:/astock production tables.
"""
import glob
import json
import os
import re
from collections import defaultdict

BROWSE = r'D:\QuantLab\browse'
OUT = r'D:\QuantLab\scripts\_probe_share_files.out.txt'


def rows_of(path):
    with open(path, 'rb') as fb:
        raw = fb.read()
    enc = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xff\xfe') else 'utf-8'
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        enc = 'utf-16'
    elif raw[:3] == b'\xef\xbb\xbf':
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


def entries(path):
    """Return (files, dirs, share_info) for one saved page."""
    files, dirs, si = [], [], None
    for r in rows_of(path):
        if r.get('type') != 'result':
            continue
        d = r.get('data') or {}
        if d.get('share_info'):
            si = d['share_info']
        for f in (d.get('files') or []):
            (dirs if f.get('file_type') == '0' else files).append(f)
    return files, dirs, si


def tag_of(path):
    return os.path.basename(path)[:-len('.jsonl')].split('_')[1]


def main():
    lines = []
    W = lines.append

    # ---- root snapshots: authoritative recursive totals -------------------
    W('=== 分享根级权威总量 (share_info) ===')
    roots = {}
    for p in sorted(glob.glob(os.path.join(BROWSE, 'share_*_p1.jsonl'))
                    + glob.glob(os.path.join(BROWSE, 'RECHECK_*.jsonl'))):
        base = os.path.basename(p)[:-len('.jsonl')]
        _, si = entries(p)[1], entries(p)[2]
        if not si:
            continue
        roots.setdefault(base.split('_')[-1], []).append(
            (base, si.get('all_file_num'), si.get('file_only_num'),
             si.get('size'), si.get('updated_at')))
    for k, v in roots.items():
        for base, alln, fonly, size, upd in v:
            W('%-34s all=%-9s files=%-9s size=%-16s upd=%s' % (
                base, alln, fonly, size, upd))
    W('')

    # ---- L1 tree ---------------------------------------------------------
    name_by = {}
    W('=== 第一层目录 ===')
    for p in sorted(glob.glob(os.path.join(BROWSE, 'share_*_L1.jsonl'))):
        tag = tag_of(p)
        files, dirs, _ = entries(p)
        W('--- %s (%d 项) ---' % (tag, len(files) + len(dirs)))
        for f in sorted(dirs, key=lambda x: x.get('filename') or ''):
            name_by[(tag, f['fid'][:8])] = f.get('filename') or ''
            W('  [D] %s' % f.get('filename'))
        for f in sorted(files, key=lambda x: x.get('filename') or ''):
            W('  [F] %-52s %10.2f MiB' % (
                f.get('filename'), (f.get('size') or 0) / 1048576.0))
    W('')

    # ---- L2 tree, with L3 dir names -------------------------------------
    W('=== 第二层目录（含其下第三层目录名） ===')
    for p in sorted(glob.glob(os.path.join(BROWSE, 'L2_*.jsonl'))):
        stem = os.path.basename(p)[:-len('.jsonl')]
        tag, pfid8 = stem.split('_')[1], stem.split('_')[-1]
        files, dirs, _ = entries(p)
        pname = name_by.get((tag, pfid8), '?')
        W('--- %s / %s  (文件=%d 目录=%d) ---' % (tag, pname, len(files),
                                                 len(dirs)))
        for f in sorted(files, key=lambda x: x.get('filename') or ''):
            nm = f.get('filename') or ''
            W('  [F] %-52s %10.2f MiB' % (nm,
                                            (f.get('size') or 0) / 1048576.0))
        for f in sorted(dirs, key=lambda x: x.get('filename') or ''):
            nm = f.get('filename') or ''
            name_by[(tag, f['fid'][:8])] = nm
            W('  [D] %s' % nm)
    W('')

    # ---- L3 real filenames: this is the table-level evidence ------------
    W('=== 第三层真实文件名（表级映射证据） ===')
    table_hits = defaultdict(lambda: {'n': 0, 'bytes': 0, 'where': set()})
    for p in sorted(glob.glob(os.path.join(BROWSE, 'L3_*.jsonl'))):
        stem = os.path.basename(p)[:-len('.jsonl')]
        tag, fid8 = stem.split('_')[1], stem.split('_')[2]
        dname = name_by.get((tag, fid8), '?')
        files, dirs, _ = entries(p)
        named = [f for f in files if not re.fullmatch(r'\d{6}', os.path.splitext(
            f.get('filename') or '')[0])]
        if not named:
            continue
        W('--- %s / %s  (文件=%d 其中具名=%d) ---' % (tag, dname, len(files),
                                                     len(named)))
        for f in sorted(named, key=lambda x: x.get('filename') or ''):
            nm = f.get('filename') or ''
            sz = f.get('size') or 0
            W('  %-56s %10.2f MiB' % (nm, sz / 1048576.0))
            key = os.path.splitext(nm)[0].lower()
            h = table_hits[key]
            h['n'] += 1
            h['bytes'] += sz
            h['where'].add('%s/%s' % (tag, dname))
    W('')
    W('=== 去重后的具名表/文件（按出现次数降序） ===')
    for key, h in sorted(table_hits.items(),
                         key=lambda kv: -kv[1]['n'])[:80]:
        W('%-46s n=%-4d %9.2f MiB  <- %s' % (
            key, h['n'], h['bytes'] / 1048576.0,
            '; '.join(sorted(h['where'])[:3])))

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('lines=%d tables=%d -> %s' % (len(lines), len(table_hits), OUT))


if __name__ == '__main__':
    main()