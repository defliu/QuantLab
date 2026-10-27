# coding=gbk
# =============================================================
# QMT built-in Python -- DATA-SIDE probe  v4.7
# PROBE_TAG: 20261027-04
#
# WHY THIS FILE EXISTS (work order T1, P0)
#   v4.6 proved OHLCV is reachable (get_market_data_ex with
#   stock_code= keyword). It did NOT call the fundamental APIs that
#   sit in C.dir() but were never invoked. This probe calls them and
#   prints the RAW return so an out-of-sandbox script can diff it
#   against D:/astock.
#
# SEGMENTS
#   [API]   hasattr inventory for the 10 target APIs
#   [SEC]   get_stock_list_in_sector   x 8 sector names
#   [INST]  get_instrument_detail      x 20 (FloatVolume/TotalVolume)
#   [CAP]   get_float_caps             x 20 (实测=流通股本(股)，与 FloatVolume 同值，不是市值)
#   [TSHR]  get_total_share            x 20
#   [TURN]  get_turnover_rate (batch) + get_turn_over_rate (x3)
#   [FIN]   get_finance                x 20 (per-stock finance dict)
#   [FDATA] get_financial_data         candidate field names
#   [ADJ]   get_market_data_ex x 20, dividend_type none vs front_ratio
#
# OUTPUT CONTRACT (machine parsed outside the client)
#   one record per line:   [P47] {json}
#   json = ensure_ascii=True, sort_keys=True -> pure ASCII.
#   kinds: meta | seg | call
#   call.ok=True  -> call.v holds the raw (deep-converted) return
#   call.ok=False -> call.err holds "Type: message" or "HUNG >Ns"
#   Parser: broker/qmt_probe_v47_align.py
#
# RISK MODEL
#   read-only market/finance APIs only. No passorder, no cancel, no
#   account query, no file IO, no network, no subprocess.
#   Every call runs on a daemon thread with a hard timeout, so one
#   hanging API cannot swallow the rest of the run (v4.2 lesson).
#
# PYTHON 3.6.8 (client embedded) -- no f-string, no walrus, no
#   match, no PEP-585 generics. File is pure ASCII on purpose: it
#   survives GBK/UTF-8 pasting unchanged, and the only non-ASCII
#   payloads (sector names) are written as \u escapes.
#
# DEPLOY (manual paste, AGENTS.md rule)
#   1) in the client UI delete the stale QMT_SANDBOX_PROBE strategy
#      first (it re-runs on every bar and floods FormulaOutput)
#   2) new strategy -> paste this WHOLE file once (SEGMENT='AUTO')
#   3) run in MODEL TRADING on account 67014907
#   4) log: <client>/userdata/log/XtClient_FormulaOutput_YYYYMMDD.log
#   5) delete the strategy once the log is collected
# =============================================================

import json
import sys
import threading
import time

PROBE_TAG = '20261027-04'

# 'AUTO' runs every segment in this order. Set to one of
# 'API','SEC','INST','CAP','TSHR','TURN','FIN','FDATA','ADJ' to re-run
# a single segment without redoing the whole probe.
SEGMENT = 'AUTO'

# 20 codes: today's 4 G2 candidates + 16 liquid names (SH/SZ/ChiNext/STAR)
SAMPLE = [
    '603986.SH', '603529.SH', '300223.SZ', '002738.SZ',
    '000001.SZ', '000333.SZ', '000651.SZ', '000858.SZ',
    '002415.SZ', '002594.SZ', '300059.SZ', '300750.SZ',
    '600000.SH', '600036.SH', '600519.SH', '600900.SH',
    '601012.SH', '601318.SH', '601899.SH', '688981.SH',
]

# (ascii alias, QMT sector name as unicode escapes)
SECTORS = [
    ('hs_a', u'\u6caa\u6df1A\u80a1'),        # Shanghai+Shenzhen A
    ('sh_a', u'\u4e0a\u8bc1A\u80a1'),        # Shanghai A
    ('sz_a', u'\u6df1\u8bc1A\u80a1'),        # Shenzhen A
    ('hs300', u'\u6caa\u6df1300'),           # CSI 300
    ('sz50', u'\u4e0a\u8bc150'),             # SSE 50
    ('chinext', u'\u521b\u4e1a\u677f'),      # ChiNext
    ('star', u'\u79d1\u521b\u677f'),         # STAR market
    ('zz500', u'\u4e2d\u8bc1500'),           # CSI 500
]

# Candidate statement field names. QMT field naming is table.column;
# a name that does not resolve tells us the schema, a name that
# resolves but returns empty tells us the data package is missing.
FIN_FIELDS = [
    'ASHAREINCOME.net_profit_incl_min_int_inc',
    'ASHAREINCOME.total_operating_revenue',
    'ASHAREBALANCESHEET.tot_assets',
    'ASHAREBALANCESHEET.total_equity',
    'ASHARECASHFLOW.net_cash_flows_oper_act',
    'ASHAREFINANCIALINDICATOR.roe',
    'PERSHAREINDEX.roe',
]

API_TARGETS = [
    'get_finance', 'get_financial_data', 'get_raw_financial_data',
    'get_turnover_rate', 'get_turn_over_rate', 'get_float_caps',
    'get_total_share', 'get_stock_list_in_sector',
    'get_instrument_detail', 'get_market_data_ex',
]


def _emit(rec):
    try:
        line = json.dumps(rec, ensure_ascii=True, sort_keys=True)
    except BaseException:
        line = '{"k": "err", "err": "json-dump-failed"}'
    try:
        print('[P47] ' + line)
        sys.stdout.flush()
    except BaseException:
        pass


def _jsafe(o, depth=0):
    # Convert arbitrary API returns (dict/list/DataFrame/numpy) into
    # something json.dumps accepts, with size caps so one huge dict
    # cannot blow up the log line.
    if depth > 5:
        return '<deep>'
    if o is None or isinstance(o, (bool, int, float, str)):
        if isinstance(o, float):
            if o != o or o in (float('inf'), float('-inf')):
                return repr(o)
        if isinstance(o, str) and len(o) > 300:
            return o[:300] + '..'
        return o
    if isinstance(o, dict):
        out = {}
        for i, k in enumerate(list(o.keys())):
            if i >= 80:
                break
            out[str(k)] = _jsafe(o[k], depth + 1)
        return out
    if isinstance(o, (list, tuple, set)):
        seq = list(o)
        return [_jsafe(v, depth + 1) for v in seq[:60]]
    try:
        import pandas as _pd
        if isinstance(o, _pd.DataFrame):
            cols = [str(c) for c in list(o.columns)]
            nrow = int(len(o))
            rows = []
            for i in range(min(nrow, 12)):
                try:
                    rows.append([_jsafe(x, depth + 1) for x in list(o.iloc[i])])
                except BaseException as exc:
                    rows.append(['<row-err:%s>' % type(exc).__name__])
            return {
                '__df__': True,
                'shape': [nrow, len(cols)],
                'columns': cols,
                'index': [str(x) for x in list(o.index)[:12]],
                'rows': rows,
            }
        if isinstance(o, _pd.Series):
            return {
                '__series__': True,
                'name': str(o.name),
                'index': [str(x) for x in list(o.index)[:12]],
                'values': [_jsafe(x, depth + 1) for x in list(o.values)[:12]],
            }
    except BaseException:
        pass
    if hasattr(o, 'item'):
        try:
            return _jsafe(o.item(), depth + 1)
        except BaseException:
            pass
    try:
        return repr(o)[:300]
    except BaseException:
        return '<unrepr>'


def _call(api, fn, extra=None, timeout=8.0):
    box = {}

    def _worker():
        try:
            box['v'] = fn()
        except BaseException as exc:
            box['e'] = '%s: %s' % (type(exc).__name__, exc)

    t0 = time.time()
    th = threading.Thread(target=_worker)
    th.daemon = True
    th.start()
    th.join(timeout)
    ms = round((time.time() - t0) * 1000.0, 1)
    rec = {'k': 'call', 'api': api, 'ms': ms}
    if extra:
        for k in list(extra.keys()):
            rec[k] = extra[k]
    if th.is_alive():
        rec['ok'] = False
        rec['err'] = 'HUNG >%.0fs' % timeout
    elif 'e' in box:
        rec['ok'] = False
        rec['err'] = box['e']
    else:
        rec['ok'] = True
        rec['v'] = _jsafe(box.get('v'))
    _emit(rec)
    return rec


# ---------------- segments ----------------

def _seg_api(C):
    have = []
    miss = []
    for name in API_TARGETS:
        try:
            ok = hasattr(C, name)
        except BaseException:
            ok = False
        if ok:
            have.append(name)
        else:
            miss.append(name)
    _emit({'k': 'call', 'api': 'hasattr_inventory', 'ok': True,
           'have': have, 'miss': miss})


def _seg_sector(C):
    sample = set(SAMPLE)
    for alias, name in SECTORS:
        def _fn(nm=name):
            lst = C.get_stock_list_in_sector(nm)
            if not isinstance(lst, (list, tuple)):
                return {'ret_type': type(lst).__name__, 'ret': _jsafe(lst)}
            codes = [str(x) for x in lst]
            cs = set(codes)
            return {
                'n': len(codes),
                'head': codes[:5],
                'tail': codes[-5:],
                'sample_hit': len([c for c in SAMPLE if c in cs]),
                'sample_miss': [c for c in SAMPLE if c not in cs],
            }
        _call('get_stock_list_in_sector', _fn, {'sector': alias}, 12.0)


def _seg_inst(C):
    for code in SAMPLE:
        _call('get_instrument_detail',
              lambda c=code: C.get_instrument_detail(c), {'code': code}, 6.0)


def _seg_cap(C):
    for code in SAMPLE:
        _call('get_float_caps',
              lambda c=code: C.get_float_caps(c), {'code': code}, 6.0)


def _seg_tshare(C):
    for code in SAMPLE:
        _call('get_total_share',
              lambda c=code: C.get_total_share(c), {'code': code}, 6.0)


def _seg_turn(C):
    end = time.strftime('%Y%m%d')
    start = time.strftime('%Y%m%d', time.localtime(time.time() - 12 * 86400))
    _call('get_turnover_rate',
          lambda: C.get_turnover_rate(stock_code=list(SAMPLE),
                                      start_time=start, end_time=end),
          {'mode': 'batch20', 'start': start, 'end': end}, 15.0)
    for code in SAMPLE[:3]:
        _call('get_turn_over_rate',
              lambda c=code: C.get_turn_over_rate(c),
              {'code': code, 'mode': 'single'}, 6.0)


def _seg_fin(C):
    for code in SAMPLE:
        _call('get_finance',
              lambda c=code: C.get_finance(c), {'code': code}, 6.0)


def _seg_fdata(C):
    start = '20250101'
    end = time.strftime('%Y%m%d')
    for fld in FIN_FIELDS:
        _call('get_financial_data',
              lambda f=fld: C.get_financial_data(
                  fieldList=[f], stockList=['000001.SZ'],
                  startDate=start, endDate=end,
                  report_type='announce_time'),
              {'field': fld, 'code': '000001.SZ',
               'report_type': 'announce_time', 'start': start, 'end': end},
              10.0)
    _call('get_financial_data',
          lambda: C.get_financial_data(
              fieldList=FIN_FIELDS[:2], stockList=SAMPLE[:2],
              startDate=start, endDate=end, report_type='report_time'),
          {'mode': 'batch2', 'report_type': 'report_time',
           'start': start, 'end': end}, 12.0)


def _seg_adj(C):
    for dtype in ('none', 'front_ratio'):
        for code in SAMPLE:
            def _fn(c=code, dt=dtype):
                d = C.get_market_data_ex(stock_code=[c], period='1d',
                                         count=3, dividend_type=dt)
                if not isinstance(d, dict):
                    return {'ret_type': type(d).__name__, 'ret': _jsafe(d)}
                df = d.get(c)
                if df is None:
                    return 'NONE'
                try:
                    if len(df) == 0:
                        return 'EMPTY'
                except BaseException:
                    pass
                out = {'rows': int(len(df)),
                       'cols': [str(x) for x in list(df.columns)]}
                for col in ('open', 'high', 'low', 'close', 'volume',
                            'amount', 'preClose'):
                    if col in df.columns:
                        vals = []
                        for x in list(df[col])[-3:]:
                            try:
                                vals.append(float(x))
                            except BaseException:
                                vals.append(str(x))
                        out[col] = vals
                out['dates'] = [str(x) for x in list(df.index)[-3:]]
                return out
            _call('mdx_' + dtype, _fn,
                  {'code': code, 'dividend_type': dtype, 'count': 3}, 15.0)


SEGMENTS = [
    ('API', _seg_api),
    ('SEC', _seg_sector),
    ('INST', _seg_inst),
    ('CAP', _seg_cap),
    ('TSHR', _seg_tshare),
    ('TURN', _seg_turn),
    ('FIN', _seg_fin),
    ('FDATA', _seg_fdata),
    ('ADJ', _seg_adj),
]


def init(C):
    _emit({'k': 'meta', 'phase': 'begin', 'tag': PROBE_TAG,
           'seg': SEGMENT, 'py': sys.version.split()[0],
           'ctx': type(C).__name__,
           'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'sample_n': len(SAMPLE), 'sample': SAMPLE})
    if SEGMENT == 'AUTO':
        active = SEGMENTS
    else:
        active = [(n, f) for (n, f) in SEGMENTS if n == SEGMENT]
        if not active:
            _emit({'k': 'meta', 'phase': 'bad-segment', 'seg': SEGMENT})
    for name, fn in active:
        _emit({'k': 'seg', 'seg': name, 'phase': 'begin',
               't': time.strftime('%H:%M:%S')})
        try:
            fn(C)
        except BaseException as exc:
            _emit({'k': 'seg', 'seg': name, 'phase': 'abort',
                   'err': '%s: %s' % (type(exc).__name__, exc)})
        _emit({'k': 'seg', 'seg': name, 'phase': 'end',
               't': time.strftime('%H:%M:%S')})
    _emit({'k': 'meta', 'phase': 'done', 'tag': PROBE_TAG,
           't': time.strftime('%Y-%m-%d %H:%M:%S')})


def handlebar(C):
    # Required by QMT. Probe runs once in init() only.
    pass
