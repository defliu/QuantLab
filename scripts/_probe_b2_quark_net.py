# coding=utf-8
"""网盘源头清单 vs E 盘实物 逐项字节级比对（只读）。

用最新 browse 产物作为「网盘侧权威清单」，回答用户的问题：
  Q1 网盘上「行情数据」到底交付了几项？有没有子目录里的额外文件？
  Q2 这 15 项 E 盘是不是都下载全了（字节数逐一相等）？
  Q3 有没有「网盘有但 E 盘完全没有」的项？
  Q4 有没有可疑的交付特征（同批时间戳 / 尺寸异常 / 疑似增量冒充全量）？
"""
import json
import os
import sys

import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BROWSE = r'D:\QuantLab\browse\browse-20261004-083033-040428.jsonl'
BROWSE_SUB = r'D:\QuantLab\browse\browse-20261004-083002-44af14.jsonl'
E_ROOT = r'E:\temp\行情数据'


def ts(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000.0).strftime('%Y-%m-%d %H:%M:%S')


def load(path):
    out = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


root = load(BROWSE)
sub = load(BROWSE_SUB)

print('=' * 96)
print('Q1) 网盘「行情数据」源头交付清单')
print('=' * 96)
print('根目录直接子项 %d 个：' % len(root))
dirs = [r for r in root if r.get('category') == 0]
files = [r for r in root if r.get('category') != 0]
print('  其中 目录 %d 个 / 文件 %d 个' % (len(dirs), len(files)))
print()
print('%-6s %-34s %16s  %s' % ('类型', '网盘文件名', '网盘字节数', '网盘创建/更新时刻'))
print('-' * 96)
for r in sorted(files, key=lambda x: x['filename']):
    print('%-6s %-34s %16d  %s'
          % ('文件', r['filename'], r['size'], ts(r['created_at'])))
for r in dirs:
    print('%-6s %-34s %16s  %s'
          % ('目录', r['filename'], '-', ts(r['created_at'])))
print()
print('子目录内容（%s）：' % (dirs[0]['filename'] if dirs else '无'))
for r in sub:
    print('  %-34s %16d  %s' % (r['filename'], r['size'], ts(r['created_at'])))

total = sum(r['size'] for r in files) + sum(r['size'] for r in sub)
print()
print('网盘交付合计 %d 项 / %.2f MiB' % (len(files) + len(sub), total / 1024.0 / 1024.0))

print()
print('=' * 96)
print('Q2/Q3) 逐项比对 E 盘实物（递归找文件，含子目录）')
print('=' * 96)


def e_index():
    idx = {}
    for dirpath, _dirnames, filenames in os.walk(E_ROOT):
        for fn in filenames:
            if fn.lower().endswith('.parquet'):
                full = os.path.join(dirpath, fn)
                idx.setdefault(fn, []).append((full, os.path.getsize(full)))
    return idx


EIDX = e_index()
print('E 盘递归扫到 parquet 文件 %d 个' % sum(len(v) for v in EIDX.values()))

rows = []
for r in files + sub:
    fn = r['filename']
    qsz = r['size']
    hits = EIDX.get(fn, [])
    if not hits:
        rows.append((fn, qsz, None, None, 'MISSING'))
        continue
    for path, esz in hits:
        if esz == qsz:
            tag = 'EXACT'
        elif esz < qsz:
            tag = 'SHORT'
        else:
            tag = 'OVER'
        rows.append((fn, qsz, path, esz, tag))

print()
print('%-34s %16s %16s  %s' % ('文件名', '网盘字节', 'E盘字节', '判定'))
print('-' * 96)
n_exact = n_short = n_over = n_missing = 0
for fn, qsz, path, esz, tag in sorted(rows, key=lambda x: (x[4], x[0])):
    if tag == 'EXACT':
        n_exact += 1
    elif tag == 'SHORT':
        n_short += 1
    elif tag == 'OVER':
        n_over += 1
    else:
        n_missing += 1
    shown = esz if esz is not None else '—'
    rel = os.path.relpath(path, E_ROOT) if path else '—'
    print('%-34s %16d %16s  %-7s %s' % (fn, qsz, shown, tag, rel))
print()
print('字节完全一致 %d / 偏小 %d / 偏大 %d / 缺失 %d' % (n_exact, n_short, n_over, n_missing))

print()
print('=' * 96)
print('Q4) 交付特征诊断')
print('=' * 96)
batches = {}
for r in files + sub:
    batches.setdefault(r['created_at'], []).append(r['filename'])
print('按网盘创建时刻分组（看供应商是分几批交付的）：')
for t, names in sorted(batches.items()):
    print('  %s  %2d 项  %s' % (ts(t), len(names), ', '.join(sorted(names))))
print()
print('=> 单批同刻交付说明这批是一次性快照，不是持续增量推送。')

print()
print('=' * 96)
print('Q5) E 盘 parquet 结构抽检（行数/列数/日期范围）')
print('=' * 96)
print('%-34s %10s %5s %-12s %-12s %10s'
      % ('文件', '行数', '列数', '起始', '结束', 'MiB'))
print('-' * 96)
for fn, qsz, path, esz, tag in sorted(rows, key=lambda x: x[0]):
    if not path:
        continue
    try:
        f = pq.ParquetFile(path)
        md = f.metadata
        names = list(f.schema_arrow.names)
        dcol = 'trade_date' if 'trade_date' in names else (
            'end_date' if 'end_date' in names else None)
        lo = hi = ''
        if dcol:
            import pandas as pd
            t = f.read(columns=[dcol]).to_pandas()
            if isinstance(t.index, pd.MultiIndex):
                t = t.reset_index()
            td = pd.to_datetime(t[dcol], format='mixed', errors='coerce')
            if td.notna().any():
                lo = str(td.min().date())
                hi = str(td.max().date())
        print('%-34s %10d %5d %-12s %-12s %10.2f'
              % (fn, md.num_rows, md.num_columns, lo, hi, esz / 1024.0 / 1024.0))
    except Exception as exc:
        print('%-34s  读取失败: %s' % (fn, exc))
