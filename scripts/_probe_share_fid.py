import json
import os

BROWSE = r'D:\QuantLab\browse'

TAGS = ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']

out = []
for tag in TAGS:
    p = os.path.join(BROWSE, 'share_%s_L1.jsonl' % tag)
    rows = []
    with open(p, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    files = []
    for r in rows:
        if r.get('type') == 'result':
            files = r['data'].get('files', [])
    print('# %s' % tag)
    for fl in files:
        if fl.get('file_type') == '0':
            print('%s\t%s' % (fl['fid'], fl['filename']))
            out.append((tag, fl['fid'], fl['filename']))
    print('')

import json as _j
with open(os.path.join(BROWSE, '_subdirs.json'), 'w', encoding='utf-8') as f:
    _j.dump(out, f, ensure_ascii=False, indent=1)
print('total subdirs = %d' % len(out))