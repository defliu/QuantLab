import concurrent.futures as cf
import json
import os
import subprocess
import time

SKILL = r'C:\Users\Administrator\.trae-cn\skills\quarkclouddrive'
CLI = os.path.join(SKILL, 'scripts', 'quark-drive.cjs')

SI = """（夸克网盘分享数量超量受限，分一下多个链接分享，所有链接加起来才是全部数据）
我用夸克网盘给你分享了「1.parquet格式数据」链接：https://pan.quark.cn/s/c3a2365f2cac
「1.2parquet格式股票数据」链接：https://pan.quark.cn/s/f1732d2e8e2a
「每日A股日线行情数据parquet（日更）」：https://pan.quark.cn/s/ce4b5b27f15e"

这是供应商的分享链接，你分析一下"""

# 8 distinct L4 fids from the target list
FIDS = [
    ('c3a2365f2cac', '2837aca194304c02b1a4bdc8b8c7c167'),
    ('c3a2365f2cac', '4fbdf571cf1d41bfb3fe25c4129ce011'),
    ('c3a2365f2cac', '7882cdc1a10243fb930ed3e61030015e'),
    ('c3a2365f2cac', 'e06a188370734c26ba0be88d58f4c38f'),
    ('c3a2365f2cac', '2ccc3dce4df340a7a63bf80a2bd6c94f'),
    ('c3a2365f2cac', '011dde8c35a145fc888783cae2a1de6d'),
    ('f1732d2e8e2a', '92339b95d7694409b6d6f5b8b8f0bb0d'),
    ('f1732d2e8e2a', 'fb92d54687194cf1a13c3df20429ccfa'),
]


def run(job):
    tag, fid = job
    t0 = time.time()
    p = subprocess.run(
        ['node', 'scripts/quark-drive.cjs', 'share-detail',
         '--url', 'https://pan.quark.cn/s/' + tag,
         '--pdir-fid', fid, '--size', '100', '--page', '1',
         '--session-input', SI, '--session-id', '1791073000-q7a2x9'],
        cwd=SKILL, capture_output=True, timeout=180,
    )
    raw = p.stdout
    ok = 0
    for line in raw.decode('utf-8', 'replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('type') == 'result':
            ok = len((r.get('data') or {}).get('files') or [])
    return (tag, fid[:8], round(time.time() - t0, 1), ok, p.returncode)


for workers in (4, 8):
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(run, FIDS[:workers]))
    wall = time.time() - t0
    print('workers=%d wall=%.1fs per_req_eff=%.2fs' % (workers, wall, wall / workers))
    for r in res:
        print('   ', r)