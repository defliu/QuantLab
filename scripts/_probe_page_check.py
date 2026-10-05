import json
import os

BROWSE = r'D:\QuantLab\browse'


def rows_of(path):
    with open(path, 'rb') as fb:
        raw = fb.read()
    enc = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'
    if raw[:3] == b'\xef\xbb\xbf':
        raw = raw[3:]
        enc = 'utf-8'
    out = []
    for line in raw.decode(enc, errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


for name in ('TEST_size200_p1.jsonl', 'TEST_size200_p2.jsonl'):
    p = os.path.join(BROWSE, name)
    files, dirs, meta = [], [], None
    for r in rows_of(p):
        if r.get('type') != 'result':
            continue
        d = r.get('data') or {}
        if d.get('share_info'):
            meta = d['share_info']
        for f in (d.get('files') or []):
            if f.get('file_type') == '0':
                dirs.append(f)
            else:
                files.append(f)
    print(name)
    print('  ok      =', (rows_of(p)[-1].get('data') or {}).get('file_count'))
    print('  files   =', len(files))
    print('  dirs    =', len(dirs))
    print('  items   =', len(files) + len(dirs))
    if files:
        print('  first   =', files[0].get('filename'))
        print('  last    =', files[-1].get('filename'))
    print('  share   =', (meta or {}).get('share_id'))