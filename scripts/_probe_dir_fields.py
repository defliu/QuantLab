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


print('=== DIR ENTRY FIELD DUMP (from L1 listing) ===')
p = os.path.join(BROWSE, 'share_c3a2365f2cac_L1.jsonl')
for r in rows_of(p):
    if r.get('type') != 'result':
        continue
    d = r.get('data') or {}
    fs = d.get('files') or []
    for f in fs:
        if f.get('file_type') == '0':
            print(json.dumps(f, ensure_ascii=False, indent=1))
            raise SystemExit(0)

print('=== FILE ENTRY FIELD DUMP ===')
for r in rows_of(p):
    if r.get('type') != 'result':
        continue
    d = r.get('data') or {}
    for f in (d.get('files') or []):
        if f.get('file_type') != '0':
            print(json.dumps(f, ensure_ascii=False, indent=1))
            raise SystemExit(0)