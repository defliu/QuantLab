import glob
import json
import os

BROWSE = r'D:\QuantLab\browse'
OUT = r'D:\QuantLab\scripts\_probe_share_l2sum.out.txt'

_buf = []


def emit(s=''):
    _buf.append(s)


def human(n):
    n = float(n or 0)
    for u in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if n < 1024 or u == 'TiB':
            return '%.2f %s' % (n, u)
        n /= 1024.0


def read_text(path):
    raw = open(path, 'rb').read()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    return raw.decode('utf-8', errors='replace')


def rows_of(path):
    out = []
    if not os.path.exists(path):
        return out
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.loads(read_text(path))
    except Exception:
        return default


sub = read_json(os.path.join(BROWSE, '_subdirs.json'), [])
name_by_fid = dict((fid, name) for (_t, fid, name) in sub)

paths = sorted(glob.glob(os.path.join(BROWSE, 'L2_*.jsonl')))
emit('L2 文件数 = %d' % len(paths))

total_files = 0
total_dirs = 0
total_bytes = 0.0
pending = []

for p in paths:
    base = os.path.basename(p)
    stem = base[:-len('.jsonl')]
    # L2_<tag>_<fid8>
    fid8 = stem.split('_')[-1]
    files = []
    share_total = None
    for r in rows_of(p):
        if r.get('type') != 'result':
            continue
        d = r.get('data') or {}
        si = d.get('share_info') or {}
        if isinstance(si, dict) and si.get('all_file_num'):
            share_total = (si.get('all_file_num'), si.get('size'))
        got = d.get('files')
        if got:
            files = got
    if not files and share_total is None:
        emit('[%s] 无有效返回' % fid8)
        continue

    ndir = sum(1 for f in files if f.get('file_type') == '0')
    nfil = len(files) - ndir
    bsum = sum(f.get('size', 0) for f in files if f.get('file_type') != '0')
    total_files += nfil
    total_dirs += ndir
    total_bytes += bsum

    emit('=' * 78)
    emit('[%s] %s' % (fid8, name_by_fid.get(fid8, '(名称未知)')))
    emit('  本页: %d 文件 / %d 子目录   本页合计 %s' % (nfil, ndir, human(bsum)))
    if share_total:
        emit('  分享总量: all_file_num=%s  size=%s'
             % (share_total[0], human(share_total[1])))
    for f in files:
        isdir = f.get('file_type') == '0'
        emit('    [%s] %-56s %12s'
             % ('DIR ' if isdir else 'FILE',
                (f.get('filename') or '')[:56],
                '-' if isdir else human(f.get('size'))))
    if ndir:
        pending.append((fid8, name_by_fid.get(fid8, '')))
    emit('')

emit('#' * 78)
emit('本页可见汇总: 文件 %d / 子目录 %d / 体积 %s'
     % (total_files, total_dirs, human(total_bytes)))
emit('')
emit('仍含子目录需继续展开: %d' % len(pending))
for fid8, nm in pending:
    emit('  %s  %s' % (fid8, nm))

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(_buf))
    f.write('\n')

print('written %d lines -> %s' % (len(_buf), OUT))