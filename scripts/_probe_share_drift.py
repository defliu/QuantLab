import json
import os

BROWSE = r'D:\QuantLab\browse'
OUT = r'D:\QuantLab\scripts\_probe_share_drift.out.txt'

TAGS = ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']

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


def snap(path):
    """返回 (all_file_num, file_only_num, size, updated_at, 第一层项数)"""
    for r in rows_of(path):
        if r.get('type') != 'result':
            continue
        d = r.get('data') or {}
        si = d.get('share_info') or {}
        if si:
            return {
                'all': si.get('all_file_num'),
                'files': si.get('file_only_num'),
                'size': si.get('size'),
                'updated_at': si.get('updated_at'),
                'n1': len(d.get('files') or []),
            }
    return None


import datetime


def ts(ms):
    if not ms:
        return '-'
    return datetime.datetime.fromtimestamp(
        int(ms) / 1000.0).strftime('%Y-%m-%d %H:%M:%S')


emit('=' * 96)
emit('三分享分享级元数据漂移核对')
emit('=' * 96)
emit('')
emit('%-18s %-26s %14s %14s %12s' % ('分享', '快照文件', 'all_file_num', 'file_only_num', 'size'))
emit('-' * 96)

for tag in TAGS:
    for label, fn in [
        ('P1 首次', 'share_%s_p1.jsonl' % tag),
        ('L1 第二次', 'share_%s_L1.jsonl' % tag),
        ('RECHECK 复核', 'RECHECK_%s.jsonl' % tag),
    ]:
        p = os.path.join(BROWSE, fn)
        if not os.path.exists(p):
            emit('%-18s %-26s %14s' % (tag, label, '(缺)'))
            continue
        s = snap(p)
        if not s:
            emit('%-18s %-26s %14s' % (tag, label, '(无 share_info)'))
            continue
        emit('%-18s %-26s %14s %14s %12s'
             % (tag, label, s['all'], s['files'], human(s['size'])))

emit('')
emit('=' * 96)
emit('第三分享第一层文件明细（RECHECK）')
emit('=' * 96)

p = os.path.join(BROWSE, 'RECHECK_ce4b5b27f15e.jsonl')
for r in rows_of(p):
    if r.get('type') != 'result':
        continue
    d = r.get('data') or {}
    fs = d.get('files') or []
    emit('本页返回 %d 项' % len(fs))
    for f in fs:
        emit('  [%s] %-46s %12s'
             % ('DIR ' if f.get('file_type') == '0' else 'FILE',
                (f.get('filename') or '')[:46],
                '-' if f.get('file_type') == '0' else human(f.get('size'))))

emit('')
emit('=' * 96)
emit('第一分享第一层（RECHECK）')
emit('=' * 96)

p = os.path.join(BROWSE, 'RECHECK_c3a2365f2cac.jsonl')
for r in rows_of(p):
    if r.get('type') != 'result':
        continue
    d = r.get('data') or {}
    fs = d.get('files') or []
    emit('本页返回 %d 项' % len(fs))
    for f in fs:
        emit('  [%s] %-56s %12s'
             % ('DIR ' if f.get('file_type') == '0' else 'FILE',
                (f.get('filename') or '')[:56],
                '-' if f.get('file_type') == '0' else human(f.get('size'))))

emit('')
emit('=' * 96)
emit('第二分享第一层（RECHECK）')
emit('=' * 96)

p = os.path.join(BROWSE, 'RECHECK_f1732d2e8e2a.jsonl')
for r in rows_of(p):
    if r.get('type') != 'result':
        continue
    d = r.get('data') or {}
    fs = d.get('files') or []
    emit('本页返回 %d 项' % len(fs))
    for f in fs:
        emit('  [%s] %-56s %12s'
             % ('DIR ' if f.get('file_type') == '0' else 'FILE',
                (f.get('filename') or '')[:56],
                '-' if f.get('file_type') == '0' else human(f.get('size'))))

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(_buf))
    f.write('\n')

print('written %d lines -> %s' % (len(_buf), OUT))