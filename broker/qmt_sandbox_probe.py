# coding=gbk
# =============================================================
# QMT built-in Python -- FULL capability probe  v3
# PROBE_TAG: 20261024-01
#
# GOAL: policy keeps changing (server-side permission bits may flip
#       at any time). This probe enumerates EVERY plausible channel
#       a QMT built-in strategy could use to talk to the outside
#       world or persist state, so a single run tells us what is
#       open / closed RIGHT NOW.
#
# CHANNEL MATRIX COVERED
#   [KV]      save_py_key / get_py_key  (vendor backend KV service)
#             - locate, arity probe, roundtrip, overwrite, missing
#               key, 1MB / 6MB size, helper family
#   [FILE]    existing-file read (text + binary), new-file create,
#             read-back, append, overwrite, listdir, stat,
#             makedirs, rename, remove, chmod, tempfile, shutil
#   [SQLITE]  in-memory db, file-backed db in D:/QMT_POOL
#   [PICKLE]  pickle dump/load to a brand new file
#   [MMAP]    mmap of a newly created file
#   [PROC]    subprocess module call, os.system, os.popen
#   [TCP]     loopback, 127.0.0.1:58600 (FormulaServer),
#             external 114.114.114.114:53
#   [HTTP]    urllib GET http:// and https://, raw-socket HTTP GET,
#             urllib POST to a local nothing (connect-only)
#   [WIN32]   winreg read a known HKLM key, ctypes kernel32 call,
#             os.environ inventory (paths may reveal the sandbox)
#   [LIBS]    import probe: requests / numpy / pandas / scipy /
#             sklearn / pyarrow / yaml / json5 / sqlite3 / ctypes
#   [QMTAPI]  hasattr inventory on the C context object
#             (get_market_data_ex, get_full_tick, do_order, ...)
#   [NS]      full inventory of injected globals + keyword hits
#
# EVIDENCE BASE (v1 run 2026-10-18 + v2 10-20 recheck):
#   print() DOES reach userdata\log\XtClient_FormulaOutput_*.log
#   read ANY file -> PermissionError('Foribdden FileIO')
#   new-file create: OK on 10-18 AM, FAIL on 10-20  (flaky!)
#   listdir / stat / makedirs OK
#   loopback + 58600 + external DNS all OK (net bit NOT closed)
#   save_py_key / get_py_key exist as bare globals, never called
#
# SAFETY
#   No passorder, no cancel, no order query, no account access.
#   No file we did not create ourselves is ever written.
#   subprocess/os.system only run "cmd /c exit 0" style no-ops.
#   All network timeouts <= 5s. Everything runs once, in init().
#
# HOW TO DEPLOY (manual paste -- required, see AGENTS.md)
#   1. QMT client -> strategy editor -> new python model
#   2. Paste this WHOLE file
#   3. Run it in MODEL TRADING (simulation mode is fine; never trades)
#   4. Tell the assistant "done"; it reads the log. Then DELETE it.
# =============================================================

import os
import sys
import time
import socket
import threading

PROBE_TAG = '20261024-01'
POOL = 'D:/QMT_POOL'
RUN_ID = str(int(time.time()))
BASE = POOL + '/_probe3_' + RUN_ID

TOTAL = [0, 0]  # [ok, fail]


def _p(msg):
    try:
        print('[PROBE3] ' + str(msg).encode('ascii', 'replace').decode('ascii'))
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
    return 'wrote second -> read back %r overwrite_ok=%s' % (
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


def _io_tempfile():
    import tempfile
    tf = tempfile.NamedTemporaryFile(
        suffix='.probe3', delete=False, dir=POOL)
    try:
        tf.write(b'probe3-tempfile')
        tf.close()
        back = open(tf.name, 'rb').read()
        return 'tempfile=%s readback=%d bytes' % (tf.name, len(back))
    finally:
        try:
            os.remove(tf.name)
        except BaseException:
            pass


def _io_shutil_copy():
    import tempfile
    import shutil
    src = BASE + '_dir'
    dst = BASE + '_dir_copy'
    shutil.copytree(src, dst)
    n = len(os.listdir(dst))
    shutil.rmtree(dst)
    return 'copytree ok, %d entries copied, cleaned' % n


def _io_pickle():
    import pickle
    p = BASE + '.pkl'
    with open(p, 'wb') as f:
        pickle.dump({'tag': PROBE_TAG, 'n': 123}, f)
    with open(p, 'rb') as f:
        obj = pickle.load(f)
    os.remove(p)
    return 'roundtrip ok, obj=%r' % (obj,)


def _io_mmap():
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
    return 'file db row=%r (then removed)' % (row,)


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


# ---------------- main ----------------

def init(C):
    _p('===== QMT probe v3 %s begin (run_id=%s) =====' % (PROBE_TAG, RUN_ID))
    _p('context=%s' % type(C).__name__)

    _p('----- env -----')
    _run('runtime info', _env_info)
    _run('os.environ inventory', _env_environ)
    _run('injected globals inventory', _injected_globals)
    _run('injected keywords', _injected_interesting)

    _p('----- [KV] vendor py_key service (priority) -----')
    _run('locate save_py_key/get_py_key', _kv_locate)
    _run('kv roundtrip', _kv_roundtrip)
    _run('kv overwrite', _kv_overwrite)
    _run('kv missing key', _kv_missing)
    _run('kv 1MB value', _kv_size_1mb)
    _run('kv 6MB value', _kv_size_6mb)
    _run('kv helper family', _kv_helpers)

    _p('----- [FILE] io matrix -----')
    _run('io create new file', _io_new_file)
    _run('io read back new file', _io_read_back)
    _run('io append new file', _io_append)
    _run('io read existing file (text)', _io_read_existing_text)
    _run('io read existing file (binary)', _io_read_existing_bin)
    _run('io read system file C:/Windows/win.ini', _io_read_system_file)
    _run('io listdir pool', _io_listdir)
    _run('io stat pool', _io_stat)
    _run('io makedirs', _io_makedirs)
    _run('io rename', _io_rename)
    _run('io remove', _io_remove)
    _run('io chmod', _io_chmod)
    _run('io tempfile in pool', _io_tempfile)
    _run('io shutil copytree+rmtree', _io_shutil_copy)
    _run('io pickle roundtrip', _io_pickle)
    _run('io mmap write', _io_mmap)
    _run('sqlite :memory:', _io_sqlite_memory)
    _run('sqlite file db in pool', _io_sqlite_file)

    _p('----- [PROC] process execution -----')
    _run('subprocess.run cmd echo', _proc_subprocess)
    _run('os.system cmd exit 0', _proc_system)
    _run('os.popen cmd echo', _proc_popen)

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
    _run('http GET via raw socket :80', _http_get_raw_socket)
    _run('urllib.parse availability', _net_urlparse)

    _p('----- [WIN32] registry / ctypes -----')
    _run('winreg read HKLM ProductName', _win_registry_read)
    _run('ctypes kernel32 GetTickCount', _win_ctypes_kernel32)
    _run('ctypes user32 GetForegroundWindow', _win_ctypes_user32)

    _p('----- [LIBS] third-party availability -----')
    for lib in ('requests', 'numpy', 'pandas', 'scipy', 'sklearn',
                'pyarrow', 'yaml', 'sqlite3', 'ctypes', 'winreg'):
        _run('import %s' % lib, _lib_probe(lib))

    _p('----- [QMTAPI] context object inventory -----')
    _run('C hasattr api checklist', _ctx_inventory(C))
    _run('C dir() sample', _ctx_dir_sample(C))

    _p('===== SUMMARY: %d OK / %d FAIL =====' % (TOTAL[0], TOTAL[1]))
    _p('===== QMT probe v3 %s end =====' % PROBE_TAG)


def handlebar(C):
    # Required by QMT, deliberately empty: probe runs once in init only.
    pass


_OUR_NAMES = frozenset(list(globals().keys()))
