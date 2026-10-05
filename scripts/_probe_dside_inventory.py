import os

D = r'D:\astock'
OUT = r'D:\QuantLab\scripts\_probe_dside_inventory.out.txt'

SKIP_DIRS = {'_incremental', 'Updatedata', '.git'}

_buf = []


def emit(s=''):
    _buf.append(s)


def human(n):
    n = float(n or 0)
    for u in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if n < 1024 or u == 'TiB':
            return '%.2f %s' % (n, u)
        n /= 1024.0


# 按一级子目录聚合
groups = {}
for dirpath, dirnames, filenames in os.walk(D):
    rel = os.path.relpath(dirpath, D)
    top = rel.split(os.sep)[0]
    if top in ('.', ''):
        top = '(root)'
    if top in SKIP_DIRS:
        continue
    for fn in filenames:
        if not fn.lower().endswith('.parquet'):
            continue
        full = os.path.join(dirpath, fn)
        try:
            sz = os.path.getsize(full)
        except OSError:
            continue
        g = groups.setdefault(top, {'n': 0, 'bytes': 0, 'files': []})
        g['n'] += 1
        g['bytes'] += sz
        if len(g['files']) < 6:
            g['files'].append(fn)

emit('=' * 96)
emit('D:\\astock 生产库一级目录清单（排除 _incremental / Updatedata）')
emit('=' * 96)
emit('')
emit('%-26s %8s %14s%s' % ('目录', '文件数', '体积', '  样例'))
emit('-' * 96)

tot_n = 0
tot_b = 0
for top in sorted(groups):
    g = groups[top]
    tot_n += g['n']
    tot_b += g['bytes']
    emit('%-26s %8d %14s  %s'
         % (top, g['n'], human(g['bytes']), ', '.join(g['files'][:4])))

emit('-' * 96)
emit('%-26s %8d %14s' % ('合计', tot_n, human(tot_b)))
emit('')

# 单独统计被排除的目录（迁移语义相关）
emit('=' * 96)
emit('增量/临时区（不参与生产口径，但迁移相关）')
emit('=' * 96)
for skip in ('_incremental', 'Updatedata'):
    p = os.path.join(D, skip)
    if not os.path.isdir(p):
        continue
    n = 0
    b = 0
    subdirs = []
    for dirpath, dirnames, filenames in os.walk(p):
        rel = os.path.relpath(dirpath, p)
        if rel != '.':
            top = rel.split(os.sep)[0]
            if top not in subdirs:
                subdirs.append(top)
        for fn in filenames:
            if fn.lower().endswith('.parquet'):
                n += 1
                try:
                    b += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    emit('  %-24s %6d 文件  %12s  子目录: %s'
         % (skip, n, human(b), ', '.join(subdirs[:8])))

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(_buf) + '\n')

print('groups=%d files=%d -> %s' % (len(groups), tot_n, OUT))