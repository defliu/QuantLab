import glob
import json
import os

BROWSE = r'D:\QuantLab\browse'
OUT = r'D:\QuantLab\scripts\_probe_share_l3_targets.out.txt'


def read_text(path):
    raw = open(path, 'rb').read()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    return raw.decode('utf-8', errors='replace')


def rows_of(path):
    out = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# fid -> (tag, name, parent_chain)
nodes = {}

for p in sorted(glob.glob(os.path.join(BROWSE, 'L2_*.jsonl'))):
    base = os.path.basename(p)
    stem = base[:-len('.jsonl')]
    tag = stem.split('_')[1]
    pfid8 = stem.split('_')[-1]
    for r in rows_of(p):
        if r.get('type') != 'result':
            continue
        d = r.get('data') or {}
        for f in (d.get('files') or []):
            if f.get('file_type') != '0':
                continue
            fid = f['fid']
            nodes[fid] = {
                'tag': tag,
                'fid': fid,
                'name': f.get('filename') or '',
                'parent': pfid8,
            }

_buf = []
_buf.append('第三层待展开子目录总数 = %d' % len(nodes))
_buf.append('')

groups = {}
for fid, n in nodes.items():
    groups.setdefault(n['tag'], []).append(n)

for tag in sorted(groups):
    _buf.append('=' * 96)
    _buf.append('分享 %s  待展开 %d 个' % (tag, len(groups[tag])))
    _buf.append('=' * 96)
    for n in sorted(groups[tag], key=lambda x: x['name']):
        _buf.append('  %s  %s  (父 %s)'
                    % (n['fid'][:8], n['name'][:70], n['parent']))
    _buf.append('')

# 生成 PowerShell 映射
_ps = ['$si = @\'',
       '（夸克网盘分享数量超量受限，分一下多个链接分享，所有链接加起来才是全部数据）',
       '我用夸克网盘给你分享了「1.parquet格式数据」链接：https://pan.quark.cn/s/c3a2365f2cac',
       '「1.2parquet格式股票数据」链接：https://pan.quark.cn/s/f1732d2e8e2a',
       '「每日A股日线行情数据parquet（日更）」：https://pan.quark.cn/s/ce4b5b27f15e"',
       '',
       '这是供应商的分享链接，你分析一下',
       '\'@',
       '',
       '$map = @(']
for fid, n in sorted(nodes.items(), key=lambda kv: (kv[1]['tag'], kv[1]['name'])):
    _ps.append("  @{tag='%s'; fid='%s'}" % (n['tag'], fid))
_ps.append(')')
_ps.append('')
_ps.append('foreach ($m in $map) {')
_ps.append('  $url = \'https://pan.quark.cn/s/\' + $m.tag')
_ps.append('  $out = \'D:\\QuantLab\\browse\\L3_\' + $m.tag + \'_\' + $m.fid.Substring(0,8) + \'.jsonl\'')
_ps.append('  node scripts/quark-drive.cjs share-detail --url $url --pdir-fid $m.fid --size 100 --session-input $si --session-id "1791073000-q7a2x9" > $out 2>&1')
_ps.append('  Write-Output ("DONE " + $m.tag + " " + $m.fid.Substring(0,8))')
_ps.append('}')

ps_path = r'D:\QuantLab\scripts\_probe_share_l3.ps1'
with open(ps_path, 'w', encoding='utf-8-sig') as f:
    f.write('\n'.join(_ps) + '\n')

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(_buf) + '\n')

print('targets=%d' % len(nodes))
print('ps -> %s' % ps_path)