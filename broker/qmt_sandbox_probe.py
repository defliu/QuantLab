# coding=gbk
# =============================================================
# QMT built-in Python -- capability probe  v2
# PROBE_TAG: 20261018-03
#
# EVIDENCE BASE (v1 run, 2026-10-18 23:28 + 23:32,
#               client = 2nd QMT install on D: (NOT the one ending in
#               "moni"), account 70180771, py3.6.8):
#   OK   print() DOES reach
#        userdata\log\XtClient_FormulaOutput_<date>.log   <- proven channel
#   FAIL open() on an EXISTING file -> PermissionError('Foribdden FileIO')
#        (read / append / overwrite all refused)
#   OK   creating a BRAND NEW file (first run only; the 2nd run failed
#        because the same path already existed)
#   OK   listdir / stat
#   OK   loopback TCP (bind+listen+connect+roundtrip, 2ms)
#   OK   127.0.0.1:58600 (FormulaServer)
#   OK   external 114.114.114.114:53 (13ms)   <- network bit NOT closed
#   ??   save_py_key / get_py_key exist as BARE GLOBALS
#        (found by name search only -- never actually called)
#
# WHAT v2 ADDS
#   1. print-based reporting (proven channel), no reliance on files
#   2. ONE report file with a UNIQUE name -> always a brand new file
#   3. ACTUALLY CALLS save_py_key / get_py_key: signature probing,
#      roundtrip equality, overwrite, missing key, size limit, delete/list
#   4. inventories ALL injected globals (what QMT hands the strategy ns)
#   5. re-checks the file / TCP matrix so this run carries its own evidence
#
# SAFETY
#   No passorder, no cancel, no order query, no account access.
#   Only os / open / listdir / stat / socket / importlib / save_py_key.
#   All socket timeouts <= 3s. Runs once, in init().
#
# HOW TO DEPLOY (manual paste -- required, see AGENTS.md)
#   1. QMT client -> strategy editor -> new python model
#   2. Paste this WHOLE file
#   3. Run it in MODEL TRADING (simulation mode is fine; never trades)
#   4. Tell me "done"; I read the log. Then DELETE the model.
# =============================================================

import os
import sys
import time
import socket
import threading

PROBE_TAG = '20261018-03'
POOL = 'D:/QMT_POOL'
RUN_ID = str(int(time.time()))


def _p(msg):
    try:
        print('[PROBE2] ' + str(msg).encode('ascii', 'replace').decode('ascii'))
    except BaseException:
        pass


def _run(label, fn):
    t0 = time.time()
    try:
        res = fn()
        _p('%-36s OK   %7.3fs  %s' % (label, time.time() - t0, res))
        return True
    except BaseException as exc:
        _p('%-36s FAIL %7.3fs  %s: %s'
           % (label, time.time() - t0, type(exc).__name__, exc))
        return False


# ---------------- 1. environment ----------------

def _env_info():
    return 'py=%s exe=%s cwd=%s' % (sys.version.split()[0],
                                    sys.executable, os.getcwd())


def _injected_globals():
    """Names QMT injects into the strategy namespace (everything not ours)."""
    ours = set(_OUR_NAMES)
    names = sorted(n for n in globals().keys()
                   if n not in ours and not n.startswith('__'))
    return 'n=%d :: %s' % (len(names), ','.join(names))


def _injected_interesting():
    ours = set(_OUR_NAMES)
    keys = ('py_key', 'pykey', 'save', 'get_', 'store', 'cache', 'persist',
            'file', 'read', 'write', 'db', 'sql', 'redis', 'net', 'http')
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


# ---------------- 2. key/value API (the priority) ----------------

def _kv_locate():
    g = globals()
    out = []
    for n in ('save_py_key', 'get_py_key'):
        obj = g.get(n)
        if obj is None:
            out.append('%s=MISSING' % n)
            continue
        out.append('%s=%s' % (n, type(obj).__name__))
    out.append('builtins_has=%s' % hasattr(globals().get('__builtins__'), 'save_py_key'))
    return ' '.join(out)


def _kv_save(key, value):
    """Call save_py_key with the first arity that works."""
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
            return 'arity=%s ret=%r' % (label, r)
        except TypeError as exc:
            errs.append('%s:TypeError' % label)
        except BaseException as exc:
            errs.append('%s:%s:%s' % (label, type(exc).__name__, exc))
    raise RuntimeError('all arities failed :: ' + ' | '.join(errs))


def _kv_get_value(key):
    """Read a key with arity probing; returns the raw value."""
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


def _kv_get(key):
    val = _kv_get_value(key)
    return 'ret=%r type=%s' % (val, type(val).__name__)


KV_KEY = 'qmt_probe_' + RUN_ID


def _kv_roundtrip():
    save_res = _kv_save(KV_KEY, 'ping-' + PROBE_TAG)
    get_res = _kv_get(KV_KEY)
    val = _kv_get_value(KV_KEY)
    ok = (val == 'ping-' + PROBE_TAG)
    return 'save[%s] get[%s] equal=%s' % (save_res, get_res, ok)


def _kv_overwrite():
    _kv_save(KV_KEY, 'second-write')
    val = _kv_get_value(KV_KEY)
    return 'wrote second value -> read back %r overwrite_ok=%s' % (
        val, val == 'second-write')


def _kv_missing():
    val = _kv_get_value('qmt_probe_absent_' + RUN_ID)
    return 'absent key -> %r type=%s' % (val, type(val).__name__)


def _kv_size_1mb():
    body = 'A' * (1024 * 1024)
    _kv_save(KV_KEY + '_1mb', body)
    back = _kv_get_value(KV_KEY + '_1mb')
    return '1MB stored, readback len=%s match=%s' % (
        len(back) if back is not None else None, back == body)


def _kv_size_6mb():
    body = 'B' * (6 * 1024 * 1024)
    _kv_save(KV_KEY + '_6mb', body)
    back = _kv_get_value(KV_KEY + '_6mb')
    return '6MB stored, readback len=%s' % (
        len(back) if back is not None else None)


def _kv_helpers():
    """Look for delete/list helpers of the same family."""
    g = globals()
    hits = []
    for n in sorted(g.keys()):
        low = n.lower()
        if ('py_key' in low or 'pykey' in low) and n not in (
                'save_py_key', 'get_py_key'):
            hits.append(n)
    return 'extra=%s' % (hits if hits else 'none')


# ---------------- 3. file + tcp sanity ----------------

def _io_new_file():
    p = POOL + '/_probe2_' + RUN_ID + '.txt'
    f = open(p, 'w')
    try:
        f.write('probe2 %s\n' % PROBE_TAG)
    finally:
        f.close()
    return 'created %s' % p


def _io_read_back():
    p = POOL + '/_probe2_' + RUN_ID + '.txt'
    f = open(p, 'r')
    try:
        return 'read %d bytes' % len(f.read())
    finally:
        f.close()


def _io_read_existing():
    f = open(POOL + '/v2_holdings_state.json', 'rb')
    try:
        return 'read %d bytes' % len(f.read())
    finally:
        f.close()


def _io_listdir():
    return 'entries=%d' % len(os.listdir(POOL))


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


def init(C):
    _p('===== QMT probe v2 %s begin (run_id=%s) =====' % (PROBE_TAG, RUN_ID))
    _p('context=%s' % type(C).__name__)

    _p('----- env -----')
    _run('runtime info', _env_info)
    _run('injected globals inventory', _injected_globals)
    _run('injected keywords', _injected_interesting)

    _p('----- key/value persistence API (priority) -----')
    _run('locate save_py_key/get_py_key', _kv_locate)
    _run('kv roundtrip', _kv_roundtrip)
    _run('kv overwrite', _kv_overwrite)
    _run('kv missing key', _kv_missing)
    _run('kv 1MB value', _kv_size_1mb)
    _run('kv 6MB value', _kv_size_6mb)
    _run('kv helper family', _kv_helpers)

    _p('----- file IO sanity -----')
    _run('io create new file', _io_new_file)
    _run('io read back new file', _io_read_back)
    _run('io read existing file', _io_read_existing)
    _run('io listdir pool', _io_listdir)

    _p('----- tcp sanity -----')
    _run('tcp loopback', _loopback)
    _run('tcp 127.0.0.1:58600', _connect('127.0.0.1', 58600, 3.0))
    _run('tcp external dns 53', _connect('114.114.114.114', 53, 3.0))

    _p('===== QMT probe v2 %s end =====' % PROBE_TAG)


def handlebar(C):
    # Required by QMT, deliberately empty: probe runs once in init only.
    pass


_OUR_NAMES = frozenset(list(globals().keys()))
