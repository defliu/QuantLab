# coding=gbk
# =============================================================
# QMT built-in Python -- FULL capability probe  v4.6
# PROBE_TAG: 20261027-03
#
# !!! 2026-10-26 CRASH POST-MORTEM (READ THIS FIRST) !!!
#   v3.1 (20261024-02) was run on the d:/QMT...simulation client
#   (account 70180771) and
#   KILLED THE CLIENT PROCESS at 17:19:55 -- not a Python exception
#   (those are caught and printed), but an abrupt process death with
#   NO Windows WER entry, i.e. self-terminate / abort.
#   Last line written: "io chmod OK". Everything after was lost,
#   including the buffered FILE results.
#   => Therefore: SEGMENT switch below. Run one segment per paste,
#      starting with 'S'. Never run 'A' until F/P/N all pass alone.
#   => print() is buffered by the client's redirect: 17 result lines
#      arrived in ONE log entry. _p() now flushes every line.
#
# CHANNEL MATRIX COVERED
#   [HOOK]    is builtins.open / socket.socket replaced by broker?
#             (already answered on 10-26: NO, both are stock
#             io/socket, so Foribdden comes from the C layer)
#   [ENV]     interpreter, cwd, environ, injected globals
#   [KV]      save_py_key / get_py_key  (VENDOR KV -- CONFIRMED ALIVE
#             2026-10-26: save ret=(0,''), get ret=(value,0,''))
#   [FILE]    create/read/append/existing/listdir/stat/mkdir/rename/
#             remove/chmod + relative path + pathlib + sibling read
#   [FILE-2]  os.open O_WRONLY vs O_RDWR, tempfile, shutil, pickle,
#             mmap, sqlite  (native-crash suspect zone)
#   [MOD]     import your own module from the strategy dir
#   [PROC]    subprocess / os.system / os.popen
#   [NET]     loopback, 127.0.0.1:58600, external DNS, UDP
#   [HTTP]    urllib http:// + https://, raw-socket GET
#   [WIN32]   winreg, ctypes kernel32/user32
#   [LIBS]    third-party import availability
#   [QMT]     C API inventory + one live get_market_data_ex call
#
# EVIDENCE BASE
#   v1 2026-10-18: read BLOCKED, create OK, listdir/stat OK, net OK
#   v2 2026-10-20: read BLOCKED, create BLOCKED (flaky!)
#   v3 2026-10-26: read BLOCKED, create OK, append BLOCKED,
#                  m_bAllowOpenFile=1, white/black list empty,
#                  save_py_key/get_py_key WORK (2-arg, (val,rc,msg))
#   v4.5 2026-10-27: [MD] get_full_tick REAL DATA (11.49/9.75);
#                    get_market_data_ex empty dict BOTH with and
#                    without set_universe -> ROOT CAUSE FOUND: a
#                    positional call binds the stock list to fields
#                    (param 1) and leaves stock_code empty. Not a
#                    sandbox gap. v4.6 re-tests with stock_code= kw.
#
# SAFETY
#   No passorder, no cancel, no order query, no account access.
#   No file we did not create ourselves is ever written.
#   subprocess/os.system only "cmd /c echo|exit 0" no-ops.
#   All network timeouts <= 5s. Runs once, in init() only.
#
# HOW TO DEPLOY (manual paste -- required, see AGENTS.md)
#   1. QMT client -> strategy editor -> QMT_SANDBOX_PROBE
#   2. Paste this WHOLE file ONCE (SEGMENT='AUTO' is already set)
#   3. Run it in MODEL TRADING on the account you want to probe
#   4. Tell the assistant; it reads the log. DELETE the strategy when
#      done. If the client dies mid-run, the log already holds every
#      line up to the killer plus a ">> about to run:" marker naming
#      the exact next step -- no re-paste needed for the safe part.
# =============================================================

import os
import sys
import time
import socket
import threading

PROBE_TAG = '20261027-03'
POOL = 'D:/QMT_POOL'
RUN_ID = str(int(time.time()))
BASE = POOL + '/_probe4_' + RUN_ID

TOTAL = [0, 0]  # [ok, fail]

# ---- SEGMENT SWITCH -------------------------------------------------
# One paste is enough: SEGMENT='AUTO' runs every SAFE segment in
# sequence, then the risky ones LAST, so a native crash can only cost
# the remaining items (every line is flushed individually).
# 'S' = safe, 'F' = file, 'P' = proc, 'N' = net, 'X' = win32,
# 'L' = libs, 'Q' = qmt api, 'A' = everything, 'AUTO' = safe-first order.
# 2026-10-26: segment F killed the client right after "io chmod" on
# d:/QMT...simulation, suspected tempfile/os.open(O_RDWR). F is last.
SEGMENT = 'AUTO'
SEGMENTS = {
    'S': ('hook', 'env', 'kv'),
    'F': ('file',),
    'P': ('proc',),
    'N': ('net',),
    'X': ('win32',),
    'L': ('libs',),
    'Q': ('qmt',),
}
SEGMENTS['A'] = tuple(g for v in (
    SEGMENTS['S'], SEGMENTS['N'], SEGMENTS['L'], SEGMENTS['X'],
    SEGMENTS['Q'], SEGMENTS['P'], SEGMENTS['F']) for g in v)
SEGMENTS['AUTO'] = SEGMENTS['A']


def _p(msg):
    try:
        print('[PROBE4] ' + str(msg).encode('ascii', 'replace').decode('ascii'))
        sys.stdout.flush()
    except BaseException:
        pass


def _run(label, fn):
    t0 = time.time()
    try:
        res = fn()
        TOTAL[0] += 1
        _p('%-40s OK   %6.3fs  %s' % (label, time.time() - t0, res))
        return True
    except BaseException as exc:
        TOTAL[1] += 1
        _p('%-40s FAIL %6.3fs  %s: %s'
           % (label, time.time() - t0, type(exc).__name__, exc))
        return False


# ---------------- 0. hook detection ----------------
# if the broker REPLACED built-in open/socket, later BLOCKs are
# strategy-layer hooks, not OS-level denial. check origin first.

def _hook_open_origin():
    m = getattr(open, '__module__', '?')
    n = getattr(open, '__name__', '?')
    fn = getattr(open, '__func__', None)
    fn_m = getattr(fn, '__module__', '?') if fn else '?'
    return 'builtins.open -> module=%s name=%s func_module=%s' % (m, n, fn_m)


def _hook_socket_origin():
    cls = socket.socket
    return 'socket.socket -> module=%s qualname=%s file=%s' % (
        getattr(cls, '__module__', '?'),
        getattr(cls, '__qualname__', getattr(cls, '__name__', '?')),
        getattr(getattr(cls, '__init__', None), '__code__', None) and
        getattr(cls.__init__.__code__, 'co_filename', '?'))


def _hook_sys_path():
    return 'n=%d :: %s' % (len(sys.path), ' | '.join(sys.path))


def _hook_strategy_dir():
    cwd = os.getcwd()
    try:
        marker = os.path.join(cwd, 'qmt_probe_helper.py')
        helper_here = os.path.exists(marker)
    except BaseException:
        helper_here = '?'
    return 'cwd=%s helper_here=%s' % (cwd, helper_here)


def _io_pathlib_roundtrip():
    import pathlib
    p = pathlib.Path(BASE + '_pl.txt')
    p.write_text('probe3-pathlib')
    back = p.read_text()
    p.unlink()
    return 'roundtrip=%r' % back


def _io_relative_write():
    p = 'qmt_probe_rel.txt'
    f = open(p, 'w')
    try:
        f.write('probe3-rel')
    finally:
        f.close()
    back = open(p, 'r').read()
    os.remove(p)
    return 'relative roundtrip=%r' % back


def _io_import_local_module():
    # requires qmt_probe_helper.py next to the strategy (see H4 line).
    # FAIL with ImportError -> broker blocks adding your own modules.
    # FAIL with IOError-file-not -> file simply not placed: WARN not BLOCK.
    import qmt_probe_helper as h
    add = getattr(h, 'add', None)
    return 'imported from %s, add(2,3)=%s' % (
        getattr(h, '__file__', '?'), add(2, 3) if add else 'n/a')


def _io_read_sibling_txt():
    f = open('probe_sample.txt', 'r')
    try:
        return 'read %d chars' % len(f.read())
    finally:
        f.close()


# ---------------- 1. environment ----------------

def _env_info():
    return 'py=%s exe=%s cwd=%s' % (sys.version.split()[0],
                                    sys.executable, os.getcwd())


def _env_environ():
    keys = sorted(os.environ.keys())
    interesting = [k for k in keys if k.upper() in (
        'PATH', 'PYTHONPATH', 'PYTHONHOME', 'TEMP', 'TMP', 'USERNAME',
        'USERDOMAIN', 'SESSIONNAME', 'CLIENTNAME', 'COMPUTERNAME')]
    vals = []
    for k in interesting:
        v = os.environ.get(k, '')
        vals.append('%s=%s' % (k, v[:120]))
    return 'n_env=%d :: %s' % (len(keys), ' | '.join(vals))


def _injected_globals():
    ours = set(_OUR_NAMES)
    names = sorted(n for n in globals().keys()
                   if n not in ours and not n.startswith('__'))
    return 'n=%d :: %s' % (len(names), ','.join(names))


def _injected_interesting():
    ours = set(_OUR_NAMES)
    keys = ('py_key', 'pykey', 'save', 'get_', 'store', 'cache', 'persist',
            'file', 'read', 'write', 'db', 'sql', 'redis', 'net', 'http',
            'order', 'trade', 'pass', 'download', 'upload', 'log', 'print',
            'send', 'msg', 'push', 'notice', 'alert')
    hits = []
    for n in sorted(globals().keys()):
        if n in ours or n.startswith('__'):
            continue
        low = n.lower()
        for k in keys:
            if k in low:
                hits.append(n)
                break
    return 'n=%d :: %s' % (len(hits), ','.join(hits))


# ---------------- 2. key/value API (vendor KV service) ----------------

def _kv_locate():
    g = globals()
    out = []
    for n in ('save_py_key', 'get_py_key'):
        obj = g.get(n)
        if obj is None:
            out.append('%s=MISSING' % n)
            continue
        out.append('%s=%s' % (n, type(obj).__name__))
    out.append('builtins_has=%s' % hasattr(
        globals().get('__builtins__'), 'save_py_key'))
    return ' '.join(out)


def _kv_save(key, value):
    g = globals()
    fn = g.get('save_py_key')
    if fn is None:
        raise RuntimeError('save_py_key missing')
    attempts = (
        ('2args', lambda: fn(key, value)),
        ('kwargs', lambda: fn(key=key, value=value)),
        ('rev', lambda: fn(value, key)),
        ('3args', lambda: fn(key, value, 0)),
    )
    errs = []
    for label, call in attempts:
        try:
            r = call()
            return ('arity=%s ret=%r' % (label, r))
        except TypeError as exc:
            errs.append('%s:TypeError' % label)
        except BaseException as exc:
            errs.append('%s:%s:%s' % (label, type(exc).__name__, exc))
    raise RuntimeError('all arities failed :: ' + ' | '.join(errs))


def _kv_get_value(key):
    g = globals()
    fn = g.get('get_py_key')
    if fn is None:
        raise RuntimeError('get_py_key missing')
    attempts = (
        ('1arg', lambda: fn(key)),
        ('kwargs', lambda: fn(key=key)),
    )
    errs = []
    for label, call in attempts:
        try:
            return call()
        except TypeError as exc:
            errs.append('%s:TypeError' % label)
        except BaseException as exc:
            errs.append('%s:%s:%s' % (label, type(exc).__name__, exc))
    raise RuntimeError('all arities failed :: ' + ' | '.join(errs))


# get_py_key returns a 3-tuple (value, rc, msg) -- confirmed 2026-10-26.
# A missing key yields ('', 0, '') so rc alone cannot tell hit from miss.
def _kv_unpack(raw):
    if isinstance(raw, tuple):
        val = raw[0] if len(raw) > 0 else None
        rc = raw[1] if len(raw) > 1 else None
        msg = raw[2] if len(raw) > 2 else None
        return val, rc, msg
    return raw, None, None


def _kv_get(key):
    raw = _kv_get_value(key)
    return 'ret=%r type=%s' % (raw, type(raw).__name__)


KV_KEY = 'qmt_probe_' + RUN_ID
KV_PAYLOAD = 'ping-' + PROBE_TAG


def _kv_roundtrip():
    save_res = _kv_save(KV_KEY, KV_PAYLOAD)
    raw = _kv_get_value(KV_KEY)
    val, rc, msg = _kv_unpack(raw)
    return 'save[%s] value_ok=%s len=%s rc=%s raw=%r' % (
        save_res, val == KV_PAYLOAD,
        len(val) if isinstance(val, str) else 'n/a', rc, raw)


def _kv_overwrite():
    _kv_save(KV_KEY, 'second-write')
    val, rc, msg = _kv_unpack(_kv_get_value(KV_KEY))
    return 'wrote second -> read back %r overwrite_ok=%s rc=%s' % (
        val, val == 'second-write', rc)


def _kv_missing():
    val, rc, msg = _kv_unpack(_kv_get_value('qmt_probe_absent_' + RUN_ID))
    return 'absent key -> val=%r rc=%s empty_means_miss=%s' % (
        val, rc, val == '')


def _kv_size_1mb():
    body = 'A' * (1024 * 1024)
    _kv_save(KV_KEY + '_1mb', body)
    val, rc, msg = _kv_unpack(_kv_get_value(KV_KEY + '_1mb'))
    n = len(val) if isinstance(val, str) else None
    return '1MB stored, readback len=%s match=%s rc=%s' % (
        n, val == body, rc)


def _kv_size_6mb():
    body = 'B' * (6 * 1024 * 1024)
    _kv_save(KV_KEY + '_6mb', body)
    val, rc, msg = _kv_unpack(_kv_get_value(KV_KEY + '_6mb'))
    n = len(val) if isinstance(val, str) else None
    return '6MB stored, readback len=%s match=%s rc=%s' % (
        n, val == body, rc)


def _kv_helpers():
    g = globals()
    hits = []
    for n in sorted(g.keys()):
        low = n.lower()
        if ('py_key' in low or 'pykey' in low) and n not in (
                'save_py_key', 'get_py_key'):
            hits.append(n)
    return 'extra=%s' % (hits if hits else 'none')


# ---------------- 3. file IO matrix ----------------

def _io_new_file():
    p = BASE + '.txt'
    f = open(p, 'w')
    try:
        f.write('probe3 %s\n' % PROBE_TAG)
    finally:
        f.close()
    return 'created %s' % p


def _io_read_back():
    f = open(BASE + '.txt', 'r')
    try:
        return 'read %d bytes' % len(f.read())
    finally:
        f.close()


def _io_append():
    f = open(BASE + '.txt', 'a')
    try:
        f.write('appended\n')
    finally:
        f.close()
    return 'append ok'


def _io_read_existing_text():
    f = open(POOL + '/v2_holdings_state.json', 'r')
    try:
        return 'read %d chars' % len(f.read())
    finally:
        f.close()


def _io_read_existing_bin():
    f = open(POOL + '/v2_holdings_state.json', 'rb')
    try:
        return 'read %d bytes' % len(f.read())
    finally:
        f.close()


def _io_read_system_file():
    f = open('C:/Windows/win.ini', 'r')
    try:
        return 'read %d chars' % len(f.read())
    finally:
        f.close()


def _io_listdir():
    return 'entries=%d' % len(os.listdir(POOL))


def _io_stat():
    st = os.stat(POOL)
    return 'mode=%o' % st.st_mode


def _io_makedirs():
    d = BASE + '_dir'
    os.makedirs(d)
    return 'made %s exists=%s' % (d, os.path.isdir(d))


def _io_rename():
    src = BASE + '.txt'
    dst = BASE + '_renamed.txt'
    os.rename(src, dst)
    return 'renamed, dst exists=%s src exists=%s' % (
        os.path.exists(dst), os.path.exists(src))


def _io_remove():
    os.remove(BASE + '_renamed.txt')
    return 'removed, exists=%s' % os.path.exists(BASE + '_renamed.txt')


def _io_chmod():
    d = BASE + '_dir'
    os.chmod(d, 0o755)
    return 'chmod ok'


def _io_os_open_wonly():
    # os.open bypasses builtins.open; O_WRONLY (no read flag) is the
    # safest create path. If THIS kills the client, os.open is blocked
    # at process level and no fd-based channel can be used here.
    p = BASE + '_osopen.txt'
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, b'probe-osopen')
    finally:
        os.close(fd)
    return 'os.open O_WRONLY ok, size=%s (readback skipped: read is blocked)' % (
        os.path.getsize(p))


def _io_os_open_rdwr():
    # O_RDWR requests READ access on a brand new file -- this is the
    # exact flag combination tempfile uses. Kept isolated so we know
    # whether O_RDWR alone (not tempfile) is the killer.
    p = BASE + '_osopen_rw.txt'
    fd = os.open(p, os.O_CREAT | os.O_RDWR | os.O_TRUNC)
    try:
        os.write(fd, b'probe-osopen-rw')
        n = os.fstat(fd).st_size
    finally:
        os.close(fd)
    return 'os.open O_RDWR on new file ok, size=%d' % n


def _tempfile_body():
    import tempfile
    tf = tempfile.NamedTemporaryFile(
        suffix='.probe3', delete=False, dir=POOL)
    name = tf.name
    try:
        tf.write(b'probe3-tempfile')
        tf.close()
    finally:
        try:
            os.remove(name)
        except BaseException:
            pass
    return 'tempfile created %s, write ok, removed' % name


def _isolate(fn, timeout=6.0, label='call'):
    # Runs fn on a daemon thread. Protects the rest of the probe from a
    # call that HANGS or silently aborts the interpreter's init frame
    # (2026-10-26: tempfile did exactly that -- process stayed alive,
    # init never continued, no traceback at all).
    box = {}

    def _worker():
        try:
            box['val'] = fn()
        except BaseException as exc:
            box['err'] = '%s: %s' % (type(exc).__name__, exc)

    t = threading.Thread(target=_worker)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise RuntimeError('%s HUNG >%.0fs on daemon thread -- unusable'
                           % (label, timeout))
    if box.get('err'):
        raise RuntimeError(box['err'])
    return box.get('val', 'no result')


def _io_tempfile():
    # 2026-10-26 18:16: tempfile SILENTLY ABORTED init -- the client
    # stayed alive but no line after "about to run: tempfile" ever
    # appeared. Isolated + kept last so it cannot swallow the run.
    return _isolate(_tempfile_body, 6.0, 'tempfile')


def _io_shutil_copy():
    return _isolate(_shutil_body, 6.0, 'shutil')


def _shutil_body():
    import tempfile
    import shutil
    src = BASE + '_dir'
    dst = BASE + '_dir_copy'
    shutil.copytree(src, dst)
    n = len(os.listdir(dst))
    shutil.rmtree(dst)
    return 'copytree ok, %d entries copied, cleaned' % n


def _io_pickle():
    return _isolate(_pickle_body, 6.0, 'pickle')


def _pickle_body():
    import pickle
    p = BASE + '.pkl'
    with open(p, 'wb') as f:
        pickle.dump({'tag': PROBE_TAG, 'n': 123}, f)
    with open(p, 'rb') as f:
        obj = pickle.load(f)
    os.remove(p)
    return 'roundtrip ok, obj=%r' % (obj,)


def _io_mmap():
    return _isolate(_mmap_body, 6.0, 'mmap')


def _mmap_body():
    import mmap
    p = BASE + '.mm'
    with open(p, 'wb') as f:
        f.write(b'0' * 4096)
    f = open(p, 'r+b')
    try:
        m = mmap.mmap(f.fileno(), 4096)
        try:
            m[0:5] = b'HELLO'
            return 'mmap write ok, head=%r' % m[0:8]
        finally:
            m.close()
    finally:
        f.close()
        try:
            os.remove(p)
        except BaseException:
            pass


def _io_sqlite_memory():
    import sqlite3
    con = sqlite3.connect(':memory:')
    try:
        con.execute('CREATE TABLE t (k TEXT, v TEXT)')
        con.execute('INSERT INTO t VALUES (?,?)', ('a', 'b'))
        row = con.execute('SELECT v FROM t WHERE k=?', ('a',)).fetchone()
        return 'row=%r' % (row,)
    finally:
        con.close()


def _io_sqlite_file():
    return _isolate(_sqlite_file_body, 6.0, 'sqlite-file')


def _sqlite_file_body():
    import sqlite3
    p = BASE + '.db'
    con = sqlite3.connect(p)
    try:
        con.execute('CREATE TABLE IF NOT EXISTS t (k TEXT, v TEXT)')
        con.execute('INSERT INTO t VALUES (?,?)', ('tag', PROBE_TAG))
        con.commit()
        row = con.execute('SELECT v FROM t').fetchone()
    finally:
        con.close()
    try:
        os.remove(p)
    except BaseException:
        pass
    # 2026-10-27 audit: the old text "(then removed)" was a lie -- os.remove
    # is blocked on client B, so the file survives. Report the real state.
    return 'file db row=%r exists_after_remove=%s' % (
        (row,), os.path.exists(p))


# ---- cross-restart persistence (the only test that needs 2 runs) ----
# Fixed path (NOT time-stamped) and never deleted: run the probe a
# SECOND time days later and this must list every previous PROBE_TAG.
PERSIST_DB = POOL + '/_probe_persist.db'


def _persist_write():
    return _isolate(_persist_write_body, 6.0, 'persist-write')


def _persist_write_body():
    import sqlite3
    con = sqlite3.connect(PERSIST_DB)
    try:
        con.execute(
            'CREATE TABLE IF NOT EXISTS runs ('
            'tag TEXT, run_id TEXT, ts TEXT)')
        con.execute('INSERT INTO runs VALUES (?,?,?)',
                    (PROBE_TAG, RUN_ID,
                     time.strftime('%Y-%m-%d %H:%M:%S')))
        con.commit()
        n = con.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
    finally:
        con.close()
    return 'appended this run, total rows now=%s at %s' % (n, PERSIST_DB)


def _persist_read_back():
    return _isolate(_persist_read_back_body, 6.0, 'persist-read')


def _persist_read_back_body():
    # fresh connection, re-opened from disk -- this is the real test
    import sqlite3
    con = sqlite3.connect(PERSIST_DB)
    try:
        rows = con.execute(
            'SELECT tag, run_id, ts FROM runs ORDER BY ts').fetchall()
    finally:
        con.close()
    tags = [r[0] for r in rows]
    uniq = []
    for t in tags:
        if t not in uniq:
            uniq.append(t)
    return 'rows=%d DISTINCT_RUNS=%d :: %s' % (
        len(rows), len(uniq), ' | '.join(
            '%s@%s' % (r[0], r[2]) for r in rows))


# ---------------- 4. process execution ----------------

def _proc_subprocess():
    import subprocess
    r = subprocess.run(
        ['cmd', '/c', 'echo', 'probe3-ok'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    return 'rc=%s out=%r' % (
        r.returncode, r.stdout[:40] if r.stdout else b'')


def _proc_system():
    rc = os.system('cmd /c exit 0')
    return 'rc=%s' % rc


def _proc_popen():
    f = os.popen('cmd /c echo probe3-popen')
    try:
        out = f.read()
    finally:
        f.close()
    return 'out=%r' % out[:40]


# ---------------- 5. network ----------------

def _connect(host, port, timeout):
    def _fn():
        s = socket.create_connection((host, port), timeout=timeout)
        try:
            return 'peer=%s' % (s.getpeername(),)
        finally:
            s.close()
    return _fn


def _loopback():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder = {}
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _worker():
            try:
                srv.settimeout(3.0)
                conn, _ = srv.accept()
                conn.settimeout(3.0)
                data = conn.recv(64)
                conn.sendall(b'PONG:' + data)
                conn.close()
            except BaseException as exc:
                holder['err'] = '%s: %s' % (type(exc).__name__, exc)

        t = threading.Thread(target=_worker)
        t.daemon = True
        t.start()
        cli = socket.create_connection(('127.0.0.1', port), timeout=3.0)
        cli.settimeout(3.0)
        cli.sendall(b'PING')
        back = cli.recv(64)
        cli.close()
        t.join(2.0)
        if holder.get('err'):
            raise RuntimeError('server side: ' + holder['err'])
        return 'port=%d reply=%s' % (port, back)
    finally:
        try:
            srv.close()
        except BaseException:
            pass


def _udp_external():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(3.0)
        s.sendto(b'probe3', ('114.114.114.114', 53))
        return 'udp sent ok (fire-and-forget)'
    finally:
        s.close()


def _dns_resolve():
    return 'baidu.com -> %s' % (socket.gethostbyname('www.baidu.com'),)


def _http_get_urllib(url):
    def _fn():
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'probe3'})
        resp = urllib.request.urlopen(req, timeout=5)
        try:
            body = resp.read(200)
            return 'status=%s body[:40]=%r' % (resp.status if hasattr(
                resp, 'status') else resp.getcode(), body[:40])
        finally:
            resp.close()
    return _fn


def _http_get_raw_socket():
    def _fn():
        s = socket.create_connection(('www.baidu.com', 80), timeout=5)
        try:
            s.settimeout(5.0)
            s.sendall(b'GET / HTTP/1.0\r\nHost: www.baidu.com\r\n\r\n')
            data = s.recv(200)
            return 'reply_head=%r' % data[:60]
        finally:
            s.close()
    return _fn


def _net_urlparse():
    from urllib.parse import urlparse
    u = urlparse('https://example.com:443/p?q=1')
    return 'parsed host=%s port=%s' % (u.hostname, u.port)


# ---------------- 6. win32 level ----------------

def _win_registry_read():
    import winreg
    k = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r'SOFTWARE\Microsoft\Windows NT\CurrentVersion')
    try:
        v, _ = winreg.QueryValueEx(k, 'ProductName')
        return 'ProductName=%r' % v
    finally:
        k.Close()


def _win_ctypes_kernel32():
    import ctypes
    tick = ctypes.windll.kernel32.GetTickCount()
    return 'GetTickCount=%s' % tick


def _win_ctypes_user32():
    import ctypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return 'GetForegroundWindow=%s' % hwnd


def _win_named_pipe_name():
    # only check the name space exists; no server is created
    return 'pipe namespace \\\\.\\pipe\\ accessible via CreateFile (not tested: no peer)'


# ---------------- 7. library availability ----------------

def _lib_probe(name):
    def _fn():
        __import__(name)
        m = sys.modules[name]
        return 'imported, version=%s' % getattr(m, '__version__', '?')
    return _fn


# ---------------- 8. QMT context API inventory ----------------

QMT_C_API = (
    'get_market_data_ex', 'get_full_tick', 'get_market_data',
    'get_stock_list_in_sector', 'get_sector_list', 'get_instrument_detail',
    'get_trading_dates', 'get_all_trading_dates', 'get_current_time',
    'get_bar_timetag', 'renderbar', 'paint', 'do_order', 'do_cancel',
    'do_backtest', 'order_target_volume', 'passorder', 'cancel',
    'get_trade_detail_data', 'get_account_info', 'get_capital',
    'get_position', 'get_positions', 'subscribe_quote', 'unsubscribe_quote',
    'subscribe_whole_quote', 'run_in_thread', 'stop_run_in_thread',
    'register_notice', 'send_email', 'send_sms', 'download_sector_data',
    'save_py_key', 'get_py_key', 'writeFile', 'readFile', 'appendFile',
)


def _ctx_inventory(C):
    def _fn():
        have = []
        miss = []
        for n in QMT_C_API:
            if hasattr(C, n):
                have.append(n)
            else:
                miss.append(n)
        return 'have(%d)=%s || missing(%d)=%s' % (
            len(have), ','.join(have), len(miss), ','.join(miss))
    return _fn


def _ctx_dir_sample(C):
    def _fn():
        names = [n for n in dir(C) if not n.startswith('_')]
        return 'n=%d :: %s' % (len(names), ','.join(sorted(names)))
    return _fn


# ---- live market data (2026-10-27: the two APIs that decide whether an
# ---- in-QMT strategy can compute its own signal) -----------------------
#
# 2026-10-27 SIGNATURE TRAP -- root cause of BOTH the 10-26 KeyError
# ('000001.SZ', old probe test) and the v4.5 empty dicts. Real
# C-context signature, read from the client's own
# python/_PyContextInfo.py:181:
#   get_market_data_ex(self, fields=[], stock_code=[],
#                      period='follow', start_time='', end_time='',
#                      count=-1, dividend_type='follow',
#                      fill_data=True, subscribe=True)
# The FIRST positional arg is FIELDS, not the stock list. A positional
# call C.get_market_data_ex(['000001.SZ'], ...) binds the stock list to
# fields, leaves stock_code empty, and returns {} with NO exception.
# Production G2 (strategy_p16_g2_selfcontained, BUILD_TAG 20261023-105529)
# calls with the stock_code= keyword; its docstring records a 2026-10-17
# alignment check (60d, max rel err 3.6e-16). G2 never calls set_universe.

MD_CODES = ['000001.SZ', '600000.SH']


def _mdx_report(data):
    if not isinstance(data, dict):
        return 'ret type=%s val=%r' % (type(data).__name__, data)
    parts = []
    for code in MD_CODES:
        df = data.get(code)
        if df is None:
            parts.append('%s=NONE' % code)
            continue
        if hasattr(df, 'empty') and df.empty:
            parts.append('%s=EMPTY' % code)
            continue
        cols = [str(c) for c in list(df.columns)[:8]]
        last = 'n/a'
        try:
            last = df['close'].iloc[-1]
        except BaseException as exc:
            last = 'ERR:%s' % type(exc).__name__
        parts.append('%s rows=%d last_close=%s cols=%s' % (
            code, len(df), last, ','.join(cols)))
    return 'dict_n=%d :: %s' % (len(data), ' | '.join(parts))


def _ctx_mdx_positional_ctrl(C):
    # control: the WRONG (positional) call that returned empty dicts on
    # 10-26 (KeyError '000001.SZ') and in v4.5. Keeps the trap on record.
    def _fn():
        data = C.get_market_data_ex(list(MD_CODES), period='1d', count=5)
        return _mdx_report(data)
    return _fn


def _ctx_mdx_stock_code_kw(C):
    # decisive test: stock_code= keyword, NO set_universe (G2 pattern).
    def _fn():
        data = C.get_market_data_ex(
            stock_code=list(MD_CODES), period='1d', count=5)
        return _mdx_report(data)
    return _fn


def _ctx_mdx_after_universe(C):
    # G2 production pattern: set_universe + stock_code= + front_ratio.
    def _fn():
        r = C.set_universe(list(MD_CODES))
        data = C.get_market_data_ex(
            stock_code=list(MD_CODES), period='1d', count=5,
            dividend_type='front_ratio')
        return 'set_universe ret=%r :: %s' % (r, _mdx_report(data))
    return _fn


def _ctx_full_tick(C):
    def _fn():
        d = C.get_full_tick(list(MD_CODES))
        if not isinstance(d, dict):
            return 'ret type=%s val=%r' % (type(d).__name__, d)
        parts = []
        for code in MD_CODES:
            t = d.get(code)
            if not isinstance(t, dict):
                parts.append('%s=%r' % (code, t))
                continue
            parts.append('%s lastPrice=%s volume=%s open=%s' % (
                code, t.get('lastPrice'), t.get('volume'), t.get('open')))
        return 'dict_n=%d :: %s' % (len(d), ' | '.join(parts))
    return _fn


# ---------------- main ----------------

def _done(name):
    # milestone marker: if the client dies later, the log still shows
    # every segment that finished before the killer.
    _p('  == segment [%s] finished ==' % name)


def init(C):
    active = SEGMENTS.get(SEGMENT, SEGMENTS['A'])
    _p('===== QMT probe %s begin (run_id=%s segment=%s) ====='
       % (PROBE_TAG, RUN_ID, SEGMENT))
    _p('context=%s active_segments=%s' % (type(C).__name__, ','.join(active)))

    if 'hook' in active:
        _p('----- [HOOK] hook detection (origin of open/socket) -----')
        _run('open origin', _hook_open_origin)
        _run('socket.socket origin', _hook_socket_origin)
        _run('sys.path inventory', _hook_sys_path)
        _run('strategy cwd + helper file', _hook_strategy_dir)
        _done('hook')

    if 'env' in active:
        _p('----- env -----')
        _run('runtime info', _env_info)
        _run('os.environ inventory', _env_environ)
        _run('injected globals inventory', _injected_globals)
        _run('injected keywords', _injected_interesting)
        _done('env')

    if 'kv' in active:
        _p('----- [KV] vendor py_key service (priority) -----')
        _run('locate save_py_key/get_py_key', _kv_locate)
        _run('kv roundtrip', _kv_roundtrip)
        _run('kv overwrite', _kv_overwrite)
        _run('kv missing key', _kv_missing)
        _run('kv 1MB value', _kv_size_1mb)
        _run('kv 6MB value', _kv_size_6mb)
        _run('kv helper family', _kv_helpers)
        _done('kv')

    if 'file' in active:
        # RISK: 2026-10-26 this segment killed the client right after
        # 'io chmod'. Each step is flushed so the next run shows the
        # exact culprit. Tempfile/mmap/sqlite are last on purpose.
        _p('----- [FILE] io matrix -----')
        _run('io create new file', _io_new_file)
        _run('io read back new file', _io_read_back)
        _run('io append new file', _io_append)
        _run('io read existing file (text)', _io_read_existing_text)
        _run('io read existing file (binary)', _io_read_existing_bin)
        _run('io read system file C:/Windows/win.ini', _io_read_system_file)
        _run('io relative-path write/read', _io_relative_write)
        _run('io pathlib roundtrip', _io_pathlib_roundtrip)
        _run('io read sibling probe_sample.txt', _io_read_sibling_txt)
        _run('io listdir pool', _io_listdir)
        _run('io stat pool', _io_stat)
        _run('io makedirs', _io_makedirs)
        _run('io rename', _io_rename)
        _run('io remove', _io_remove)
        _run('io chmod', _io_chmod)
        _p('----- [FILE-2] risky io, each step announced first -----')
        # If the client dies here, the LAST "about to run" line names
        # the killer exactly -- no guessing, no lost buffer.
        for _label, _fn in (
            ('os.open O_CREAT|O_WRONLY (no O_RDWR)', _io_os_open_wonly),
            ('os.open O_RDWR on new file', _io_os_open_rdwr),
            ('io shutil copytree+rmtree', _io_shutil_copy),
            ('io pickle roundtrip', _io_pickle),
            ('io mmap write', _io_mmap),
            ('sqlite :memory:', _io_sqlite_memory),
            ('sqlite file db in pool', _io_sqlite_file),
            ('sqlite PERSIST write (fixed path)', _persist_write),
            ('sqlite PERSIST read back', _persist_read_back),
            ('io tempfile in pool (KNOWN TO ABORT init)', _io_tempfile),
        ):
            _p('  >> about to run: ' + _label)
            _run(_label, _fn)
        _done('file')

    if 'proc' in active:
        _p('----- [MOD] local module import (needs helper placed) -----')
        # FAIL(ImportError, "No module named...") with file present -> BLOCK;
        # FAIL(IOError file not found) -> helper not placed: WARN not BLOCK.
        _run('import qmt_probe_helper', _io_import_local_module)

        _p('----- [PROC] process execution -----')
        for _label, _fn in (
            ('subprocess.run cmd echo', _proc_subprocess),
            ('os.system cmd exit 0', _proc_system),
            ('os.popen cmd echo', _proc_popen),
        ):
            _p('  >> about to run: ' + _label)
            _run(_label, _fn)
        _done('proc')

    if 'net' in active:
        _p('----- [TCP] socket layer -----')
        _run('tcp loopback', _loopback)
        _run('tcp 127.0.0.1:58600 (FormulaServer)', _connect(
            '127.0.0.1', 58600, 3.0))
        _run('tcp external dns 114.114.114.114:53', _connect(
            '114.114.114.114', 53, 3.0))
        _run('udp external dns fire-and-forget', _udp_external)
        _run('dns resolve www.baidu.com', _dns_resolve)

        _p('----- [HTTP] urllib / raw socket -----')
        _run('http GET http://www.baidu.com', _http_get_urllib(
            'http://www.baidu.com'))
        _run('http GET https://www.baidu.com', _http_get_urllib(
            'https://www.baidu.com'))
        _run('http GET via raw socket :80', _http_get_raw_socket())
        _run('urllib.parse availability', _net_urlparse)
        _done('net')

    if 'win32' in active:
        _p('----- [WIN32] registry / ctypes -----')
        _run('winreg read HKLM ProductName', _win_registry_read)
        _run('ctypes kernel32 GetTickCount', _win_ctypes_kernel32)
        _run('ctypes user32 GetForegroundWindow', _win_ctypes_user32)
        _done('win32')

    if 'libs' in active:
        _p('----- [LIBS] third-party availability -----')
        for lib in ('requests', 'numpy', 'pandas', 'scipy', 'sklearn',
                    'pyarrow', 'yaml', 'sqlite3', 'ctypes', 'winreg',
                    'websocket', 'zmq', 'ssl', 'pymysql', 'MySQLdb',
                    'redis', 'talib', 'h5py'):
            _run('import %s' % lib, _lib_probe(lib))
        _done('libs')

    if 'qmt' in active:
        _p('----- [QMTAPI] context object inventory -----')
        _run('C hasattr api checklist', _ctx_inventory(C))
        _run('C dir() sample', _ctx_dir_sample(C))

        _p('----- [MD] LIVE MARKET DATA (decides in-QMT self-signal) -----')
        # order matters: positional-trap control first (documents the
        # fields-vs-stock_code signature trap), then the correct
        # stock_code= keyword call, then the G2 production pattern,
        # then full_tick.
        _run('MD mdx positional (fields-trap ctrl)',
             _ctx_mdx_positional_ctrl(C))
        _run('MD mdx stock_code= kw (5d)',
             _ctx_mdx_stock_code_kw(C))
        _run('MD set_universe + mdx kw (5d front_ratio)',
             _ctx_mdx_after_universe(C))
        _run('MD get_full_tick', _ctx_full_tick(C))
        _done('qmt')

    _p('===== SUMMARY seg=%s: %d OK / %d FAIL ====='
       % (SEGMENT, TOTAL[0], TOTAL[1]))
    _p('===== QMT probe %s end =====' % PROBE_TAG)


def handlebar(C):
    # Required by QMT, deliberately empty: probe runs once in init only.
    pass


_OUR_NAMES = frozenset(list(globals().keys()))
