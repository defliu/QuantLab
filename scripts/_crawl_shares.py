import concurrent.futures as cf
import glob
import json
import os
import subprocess
import threading
import time

SKILL = r'C:\Users\Administrator\.trae-cn\skills\quarkclouddrive'
CRAWL = r'D:\QuantLab\browse\crawl'
PAGE = 100
WORKERS = 10
RETRIES = 3

SI = """（夸克网盘分享数量超量受限，分一下多个链接分享，所有链接加起来才是全部数据）
我用夸克网盘给你分享了「1.parquet格式数据」链接：https://pan.quark.cn/s/c3a2365f2cac
「1.2parquet格式股票数据」链接：https://pan.quark.cn/s/f1732d2e8e2a
「每日A股日线行情数据parquet（日更）」：https://pan.quark.cn/s/ce4b5b27f15e"

这是供应商的分享链接，你分析一下"""

TAGS = ['c3a2365f2cac', 'f1732d2e8e2a', 'ce4b5b27f15e']

os.makedirs(CRAWL, exist_ok=True)

_lock = threading.Lock()
_state = {'req': 0, 'dirs': 0, 'files': 0, 'bytes': 0, 'err': 0, 'skip': 0}
_t0 = time.time()


def out_path(tag, fid):
    return os.path.join(CRAWL, '%s__%s.json' % (tag, fid))


def already(tag, fid):
    p = out_path(tag, fid)
    if not os.path.exists(p) or os.path.getsize(p) < 10:
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if d.get('ok'):
            return d
    except Exception:
        pass
    return None


def parse_pages(raw):
    files, dirs, share, ok = [], [], None, False
    for line in raw.decode('utf-8', 'replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('type') != 'result':
            continue
        d = r.get('data') or {}
        ok = True
        if d.get('share_info'):
            share = d['share_info']
        for f in (d.get('files') or []):
            if f.get('file_type') == '0':
                dirs.append(f)
            else:
                files.append(f)
    return files, dirs, share, ok


def fetch_dir(job):
    tag, fid, name, depth = job

    cached = already(tag, fid)
    if cached is not None:
        with _lock:
            _state['skip'] += 1
        return (job, cached, None, True)

    files, dirs = [], []
    page = 1
    while True:
        got = False
        for attempt in range(RETRIES):
            try:
                p = subprocess.run(
                    ['node', 'scripts/quark-drive.cjs', 'share-detail',
                     '--url', 'https://pan.quark.cn/s/' + tag,
                     '--pdir-fid', fid, '--size', str(PAGE),
                     '--page', str(page),
                     '--session-input', SI,
                     '--session-id', '1791073000-q7a2x9'],
                    cwd=SKILL, capture_output=True, timeout=240,
                )
            except Exception:
                time.sleep(3 + attempt * 3)
                continue

            with _lock:
                _state['req'] += 1

            f1, d1, _sh, ok1 = parse_pages(p.stdout)
            if ok1:
                files.extend(f1)
                dirs.extend(d1)
                got = True
                break
            time.sleep(3 + attempt * 3)

        if not got:
            with _lock:
                _state['err'] += 1
            if page == 1:
                return (job, None, 'PAGE_FAIL p1', False)
            print('  WARN partial %s %s stopped@page%d' % (tag, fid[:8], page),
                  flush=True)
            break

        if len(f1) + len(d1) < PAGE:
            break
        page += 1
        if page > 4000:
            break

    rec = {
        'ok': True, 'tag': tag, 'fid': fid, 'name': name,
        'depth': depth, 'pages': page,
        'n_files': len(files), 'n_dirs': len(dirs),
        'bytes': sum(int(f.get('size') or 0) for f in files),
        'files': files, 'dirs': dirs,
    }
    tmp = out_path(tag, fid) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, out_path(tag, fid))

    with _lock:
        _state['dirs'] += 1
        _state['files'] += len(files)
        _state['bytes'] += rec['bytes']

    return (job, rec, None, False)


def log():
    with _lock:
        s = dict(_state)
    el = time.time() - _t0
    print('[%6.0fs] req=%-6d dirs=%-5d files=%-7d bytes=%-14d err=%-3d skip=%-5d'
          % (el, s['req'], s['dirs'], s['files'], s['bytes'], s['err'], s['skip']),
          flush=True)


def scan_cached():
    known = {}
    for p in glob.glob(os.path.join(CRAWL, '*.json')):
        if p.endswith('.tmp'):
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get('ok'):
            known[(d['tag'], d['fid'])] = d
    return known


def main():
    known = scan_cached()
    print('resumed_cached_records=%d' % len(known), flush=True)

    # Frontier = every child dir referenced by ANY cached record that we have
    # not yet crawled. Roots are seeded explicitly.
    pending = []
    for (t, f0) in [(t, '0') for t in TAGS]:
        if (t, f0) not in known:
            pending.append((t, f0, '<root>', 0))

    for (t, fid), r in known.items():
        for d in r.get('dirs') or []:
            dfid = d.get('fid')
            if dfid and (t, dfid) not in known:
                pending.append((t, dfid, d.get('filename') or '',
                                r.get('depth', 0) + 1))

    wave = 0
    while pending:
        wave += 1
        print('=== WAVE %d  pending=%d ===' % (wave, len(pending)), flush=True)
        nxt = []
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for job, rec, err, cached in ex.map(fetch_dir, pending):
                if err:
                    print('  ERR %s %s %s' % (job[0], job[1][:8], err), flush=True)
                    continue
                log()
                if cached:
                    continue
                for d in rec['dirs']:
                    dfid = d.get('fid')
                    if not dfid:
                        continue
                    if (job[0], dfid) in known:
                        continue
                    nxt.append((job[0], dfid, d.get('filename') or '', job[3] + 1))
        known = scan_cached()
        pending = nxt

    print('=== CRAWL DONE ===', flush=True)
    log()


if __name__ == '__main__':
    main()