import json
import os
import sys

BROWSE = r'D:\QuantLab\browse'

TARGETS = [
    ('c3a2365f2cac', '1.parquet格式数据'),
    ('f1732d2e8e2a', '1.2parquet格式股票数据'),
    ('ce4b5b27f15e', '每日A股日线行情数据parquet（日更）'),
]


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def human(n):
    n = float(n or 0)
    for u in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if n < 1024 or u == 'TiB':
            return '%.2f %s' % (n, u)
        n /= 1024.0


def dump(tag, title):
    p1 = load(os.path.join(BROWSE, 'share_%s_p1.jsonl' % tag))
    l1 = load(os.path.join(BROWSE, 'share_%s_L1.jsonl' % tag))

    print('=' * 72)
    print('链接 %s  %s' % (tag, title))

    for rows in (p1, l1):
        for r in rows:
            if r.get('type') != 'result':
                continue
            si = r['data'].get('share_info', {})
            print(
                '  share_info: file_num=%s all_file_num=%s file_only_num=%s size=%s'
                % (
                    si.get('file_num'),
                    si.get('all_file_num'),
                    si.get('file_only_num'),
                    human(si.get('size')),
                )
            )
            files = r['data'].get('files', [])
            print('  本页返回 %d 项' % len(files))
            for f in files:
                isdir = f.get('file_type') == '0'
                print(
                    '    [%s] %-58s %12s'
                    % (
                        'DIR ' if isdir else 'FILE',
                        (f.get('filename') or '')[:58],
                        '-' if isdir else human(f.get('size')),
                    )
                )
    print('')


for tag, title in TARGETS:
    dump(tag, title)