# coding=gbk
"""
ATR ????????? - ??? ????? ????棨QMT ????????????????

???????????? atr_lowvol_fw.yaml????? ???????? +18.69% ????棩??
  ???: ATR% ????λ(top N) + ??????[1,8]% + ??ST + ????>=252??
        + ???????(ROE>0) + ???????(12-1??????>0????????????)
  ????: ?????????????????????
  ??λ: ????????????? = ????? / N_HOLD?????????
  ???: ??????(100??) / ST+-5% / ???(QMT????????????)
        ???????? -8%???????????? stop_loss??

???? deploy/strategy_atr_lowvol.py ??????
  ??? = max_hold=3 + ????? + ?????Ч?????????? -5.85% ???????
  ???? = ??????????棺8???? + ???????? + max_price50
        + MAX5??????????? atr_10w_price50_a_max ???????

?????????QMT ?????????? C??get_market_data_ex / get_turnover_rate / get_stock_list_in_sector????
ROE ????????? xtdata.get_financial_data?????????????????????????????????????
"""
import json
import os
import time
import math
from datetime import datetime, timedelta

# ============================================================
# ?????????????????? config/atr_lowvol_equalweight_config.yaml ?????
# ============================================================
CONFIG = {
    'strategy': {
        'name': 'ATR_LOWVOL_EW',
        'display_name': 'ATR?????-????????',
        'capital_base': 1000000,
        'account_id': '67014907',
    },
    'screening': {
        'n_hold': 8,
        'atr_threshold': 6.0,
        'min_turnover': 1.0,
        'max_turnover': 8.0,
        'min_history': 252,
        'quality_gate': 1,
        'momentum_gate': 1,
        'max_price': 50.0,
        'max_exclude_pct': 0.20,
    },
    'rebalance': {
        'freq': 'quarterly',
        'stop_loss': -0.08,
    },
    'pool': {
        'holdings_file': 'D:/QMT_POOL/atr_ew_holdings.json',
        'nav_file': 'D:/QMT_POOL/atr_ew_nav.json',
        'trade_log_file': 'D:/QMT_POOL/atr_ew_trade_log.csv',
    },
}

# ?????汾????YYYYmmdd-HHMMSS????????????
BUILD_TAG = "20260819-133000"  # ?????? 3b ???: ????????????????(??????á?True+???? is True)  # 20260819???: _lookup_order??????+turnover_available?None+datetime.now??QMT???  # ???? position ??η??????(CPositionDetail ?? m_strCode/.get)  # 20260818 ???: ?????????????+fail-open+????get_trade_detail_data??????

# ============================================================
# ?????
# ============================================================
_g_my_codes = {}            # code -> {buy_price, buy_date, shares, peak_price, ...}
_g_cumulative_pnl = 0.0
_g_nav_history = []
_g_all_data = {}            # code -> DataFrame???????????г??????
_g_hold_pool_cache = None   # ?????????
_g_hold_pool_cache_date = ''
_g_initialized = False
_g_cooling_until = 0.0
_g_last_rebalance_key = ''  # ????????? ?????(?? 2026Q3)
_g_last_attempt_date = ''   # ?????????????? YYYYMMDD??????????????
_g_pending_sells = {}       # code -> {shares, price, reason, time}
_g_pending_buys = {}        # code -> {shares, price, time}
_g_roe_cache = {}           # code -> roe????????棩
_g_roe_api_ok = None        # None=δ??? True/False
_g_turnover_available = None  # ??? 20260819: ????????????True

# ?????ò??????? _load_config ?????config ?? capital_base ?趨?????????????????
_STRATEGY_CAPITAL = 100000
_ACCOUNT_ID = '67014907'
_N_HOLD = 8
_ATR_THRESHOLD = 6.0
_MIN_TURNOVER = 1.0
_MAX_TURNOVER = 8.0
_MIN_HISTORY = 252
_QUALITY_GATE = 1
_MOMENTUM_GATE = 1
_MAX_PRICE = 50.0                 # ?????????(?)??0=???
_MAX_EXCLUDE_PCT = 0.20           # MAX5?????????????20?????????????20%??λ(0=??)
_REBALANCE_FREQ = 'quarterly'
_STOP_LOSS = -0.08
_REBALANCE_RETRY_DAYS = 1   # ???????δ??????????????????Σ?1=???

_LOOKUP_RETRIES = 4
_LOOKUP_INTERVAL = 0.2
_HOLDINGS_FILE = 'D:/QMT_POOL/atr_ew_holdings.json'
_NAV_FILE = 'D:/QMT_POOL/atr_ew_nav.json'
_TRADE_LOG_FILE = 'D:/QMT_POOL/atr_ew_trade_log.csv'


# ============================================================
# ???ü???????? YAML ???????????? pyyaml??
# ============================================================
def _load_config():
    global _STRATEGY_CAPITAL, _ACCOUNT_ID, _N_HOLD, _ATR_THRESHOLD
    global _MIN_TURNOVER, _MAX_TURNOVER, _MIN_HISTORY, _QUALITY_GATE
    global _MOMENTUM_GATE, _MAX_PRICE, _MAX_EXCLUDE_PCT, _REBALANCE_FREQ, _STOP_LOSS
    global _HOLDINGS_FILE, _NAV_FILE, _TRADE_LOG_FILE

    config_path = 'D:/QMT_STRATEGIES/config/atr_lowvol_equalweight_config.yaml'
    if not os.path.exists(config_path):
        print("[ATR_EW] ??????????????????????")
        return

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        section = None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.endswith(':') and not stripped.startswith('-'):
                key = stripped.rstrip(':')
                if key in ('strategy', 'screening', 'rebalance', 'pool'):
                    section = key
                else:
                    section = None
                continue
            if ':' in stripped and not stripped.startswith('-'):
                parts = stripped.split(':', 1)
                k = parts[0].strip()
                v = parts[1].strip() if len(parts) > 1 else ''
                if '#' in v:
                    v = v[:v.index('#')].strip()
                if section == 'strategy':
                    if k == 'capital_base':
                        _STRATEGY_CAPITAL = int(float(v))
                    elif k == 'account_id':
                        _ACCOUNT_ID = str(v).strip("'\"")
                elif section == 'screening':
                    if k == 'n_hold':
                        _N_HOLD = int(float(v))
                    elif k == 'atr_threshold':
                        _ATR_THRESHOLD = float(v)
                    elif k == 'min_turnover':
                        _MIN_TURNOVER = float(v)
                    elif k == 'max_turnover':
                        _MAX_TURNOVER = float(v)
                    elif k == 'min_history':
                        _MIN_HISTORY = int(float(v))
                    elif k == 'quality_gate':
                        _QUALITY_GATE = int(float(v))
                    elif k == 'momentum_gate':
                        _MOMENTUM_GATE = int(float(v))
                    elif k == 'max_price':
                        _MAX_PRICE = float(v)
                    elif k == 'max_exclude_pct':
                        _MAX_EXCLUDE_PCT = float(v)
                elif section == 'rebalance':
                    if k == 'freq':
                        _REBALANCE_FREQ = str(v).strip("'\"")
                    elif k == 'stop_loss':
                        _STOP_LOSS = float(v)
                elif section == 'pool':
                    if k == 'holdings_file':
                        _HOLDINGS_FILE = str(v).strip("'\"")
                    elif k == 'nav_file':
                        _NAV_FILE = str(v).strip("'\"")
                    elif k == 'trade_log_file':
                        _TRADE_LOG_FILE = str(v).strip("'\"")
        print("[ATR_EW] ???ü??????: N_HOLD=%d ATR<%.2f%% turnover[%.1f,%.1f] max_price=%.0f max_exclude_pct=%.2f freq=%s"
              % (_N_HOLD, _ATR_THRESHOLD, _MIN_TURNOVER, _MAX_TURNOVER, _MAX_PRICE, _MAX_EXCLUDE_PCT, _REBALANCE_FREQ))
    except Exception as e:
        print("[ATR_EW] ???ü??????: %s, ???????" % e)


# ============================================================
# ????????
# ============================================================
def _get_qmt_time(C):
    try:
        return C.get_current_time()
    except Exception:
        return datetime.now()


def _get_stock_name_safe(C, code):
    try:
        info = C.get_stock_basic_info(code)
        if info is not None:
            return info.get('name', code)
    except Exception:
        pass
    return code


def _calc_atr_pct(df):
    """???? ATR(14)/close * 100?????? factors/atr.atr_pct ????????"""
    if df is None or len(df) < 15:
        return 999.0
    try:
        h = df['high']
        l = df['low']
        c = df['close']
        tr1 = (h - l).values
        tr2 = (h - c.shift(1)).abs().values
        tr3 = (l - c.shift(1)).abs().values
        tr = [max(tr1[i], tr2[i], tr3[i]) if not math.isnan(tr2[i]) else tr1[i]
              for i in range(len(tr1))]
        atr = sum(tr[-14:]) / 14.0
        close_price = float(c.iloc[-1])
        if close_price <= 0:
            return 999.0
        return atr / close_price * 100.0
    except Exception:
        return 999.0


def _scale_turnover(raw):
    """??????????????????? src/strategy_atr.py???????????????????????
    ??????????<1 ???С??????100????>=1 ????????????? 2.5=2.5%????
    ??? 20260818??????????? ??100??????????????2.5???? ??100=250 ???????
    """
    if raw is None:
        return None
    try:
        v = float(raw)
    except Exception:
        return None
    if v < 1.0:
        v = v * 100.0
    return v


def _get_roe_qmt(C, code):
    """????????????? ROE(%)??????????????????????????????????"""
    global _g_roe_api_ok
    if code in _g_roe_cache:
        return _g_roe_cache[code]
    if not _QUALITY_GATE:
        return 1.0
    if _g_roe_api_ok is False:
        return 1.0
    try:
        import xtdata
        val = None
        try:
            res = xtdata.get_financial_data(['roe'], code)
            if isinstance(res, dict) and code in res:
                d = res[code]
                if isinstance(d, dict):
                    val = d.get('roe')
                elif d is not None:
                    val = d
        except Exception:
            val = None
        if val is None:
            try:
                res = xtdata.get_financial_data(['roe'], code, report_date='')
                if isinstance(res, dict) and code in res:
                    d = res[code]
                    val = d.get('roe') if isinstance(d, dict) else d
            except Exception:
                val = None
        if val is None:
            _g_roe_api_ok = False
            print("[ATR_EW] ROE ???????????????????????")
            return 1.0
        try:
            roe = float(val)
        except Exception:
            _g_roe_api_ok = False
            return 1.0
        _g_roe_cache[code] = roe
        _g_roe_api_ok = True
        return roe
    except Exception:
        _g_roe_api_ok = False
        return 1.0


def _load_holdings():
    global _g_my_codes, _g_cumulative_pnl, _g_nav_history
    if os.path.exists(_HOLDINGS_FILE):
        try:
            with open(_HOLDINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _g_my_codes = data.get('holdings', {})
            _g_cumulative_pnl = data.get('cumulative_pnl', 0.0)
            _g_nav_history = data.get('nav_history', [])
            print("[ATR_EW] ?????? %d ?, ?????? %.2f" % (len(_g_my_codes), _g_cumulative_pnl))
        except Exception as e:
            print("[ATR_EW] ?????????: %s" % e)
            _g_my_codes = {}
            _g_cumulative_pnl = 0.0
            _g_nav_history = []
    else:
        _g_my_codes = {}
        _g_cumulative_pnl = 0.0
        _g_nav_history = []


def _save_holdings():
    try:
        data = {
            'holdings': _g_my_codes,
            'cumulative_pnl': _g_cumulative_pnl,
            'nav_history': _g_nav_history[-500:],
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(_HOLDINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[ATR_EW] ?????????: %s" % e)


def _log_trade(trade_type, code, price, shares, reason):
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = "%s,%s,%s,%.3f,%d,%s\n" % (now, trade_type, code, price, shares, reason)
        with open(_TRADE_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass


def _reconcile_own_holdings(C):
    """??????????? ledger ???????????????????е?(????????)??? ledger ?????
    ???????/?????????????????????????????????????"""
    try:
        f_get = globals().get('get_trade_detail_data')
        if f_get is None:
            f_get = getattr(C, 'get_trade_detail_data', None)
        if f_get is None:
            print("[ATR_EW ????] get_trade_detail_data ??????????????ζ???")
            return
        positions = f_get(_ACCOUNT_ID, 'stock', 'position')
        if not positions:
            return
        account_pos = {}
        for pos in positions:
            # QMT position 为 CPositionDetail 对象，字段 m_strInstrumentID/m_strSecurityCode
            code = str(getattr(pos, 'm_strInstrumentID', '') or getattr(pos, 'm_strSecurityCode', '') or '')
            if not code:
                continue
            if not code.endswith('.SH') and not code.endswith('.SZ'):
                continue
            try:
                acct_vol = float(getattr(pos, 'm_nVolume', 0) or 0)
            except Exception:
                acct_vol = 0.0
            account_pos[code] = acct_vol
        # ?????????? ledger ?С????????????????类???/????????
        to_remove = [c for c in _g_my_codes if c not in account_pos]
        for c in to_remove:
            print("[ATR_EW ????] %s ??????????(??????????), ???????????" % c)
            del _g_my_codes[c]
        # R5???: ?????????У???ledger ???? > ???????? min??????????????/???????????
        # ?????2?????????????????????????
        for c in list(_g_my_codes.keys()):
            info = _g_my_codes[c]
            acct_vol = account_pos.get(c, 0)
            if info.get('shares', 0) > acct_vol:
                print("[ATR_EW ????] %s ???У? %d -> %d" % (c, info.get('shares', 0), int(acct_vol)))
                info['shares'] = int(acct_vol)
                if info['shares'] <= 0:
                    del _g_my_codes[c]
    except Exception as e:
        print("[ATR_EW ????] ??: %s" % e)


# ============================================================
# ????????????У????t??????????
# ============================================================
def _lookup_order(C, code, volume, direction, retries=None, interval=None):
    if retries is None:
        retries = _LOOKUP_RETRIES
    if interval is None:
        interval = _LOOKUP_INTERVAL
    # miniQMT(????????) ?? ContextInfo ??? get_trade_detail_data ????????????????
    # QMT passorder ????????????0/None???????μ??????????"??????"??
    # ???鷽?????????????????????????÷?д/? ledger?????? pending ???????
    # ???ο?6+2??????????????2??
    # ??? 20260819: get_trade_detail_data ????????(??C????)??????V2 + ???????
    f_get = globals().get('get_trade_detail_data')
    if f_get is None:
        f_get = getattr(C, 'get_trade_detail_data', None)
    if f_get is None:
        return ('OPTIMISTIC', None)
    dir_cn = '????' if direction == 'buy' else '????'
    for retry in range(retries):
        time.sleep(interval)
        try:
            deals = f_get(_ACCOUNT_ID, 'stock', 'order')
            if not deals:
                continue
            candidates = []
            for d in deals:
                try:
                    oid = str(d.m_strOrderID if hasattr(d, 'm_strOrderID') else '')
                    d_code = str(d.m_strInstrumentID if hasattr(d, 'm_strInstrumentID') else '')
                    d_vol = int(d.m_nOrderVolume if hasattr(d, 'm_nOrderVolume') else 0)
                    d_opt = str(d.m_strOptName if hasattr(d, 'm_strOptName') else '')
                    d_status = int(d.m_nOrderStatus if hasattr(d, 'm_nOrderStatus') else 0)
                except Exception:
                    continue
                if d_code != code:
                    continue
                if dir_cn not in d_opt:
                    continue
                if volume > 0 and d_vol > 0:
                    vol_diff = abs(d_vol - volume) / max(d_vol, volume)
                    if vol_diff > 0.10:
                        continue
                if d_status in (54, 55, 57):
                    continue
                candidates.append((oid, d))
            if candidates:
                candidates.sort(key=lambda x: getattr(x[1], 'm_strInsertTime', ''), reverse=True)
                return candidates[0][0], candidates[0][1]
        except Exception:
            continue
    return None, None


def _cancel_order(C, code, order_id):
    """????????????11????????????? V2??order_id ?????к???? _lookup_order ??????"""
    if not order_id:
        return None
    try:
        r = passorder(24, 1101, _ACCOUNT_ID, code, 5, order_id, 0, 'ATR_EW????', 2, '', C)
        print("[ATR_EW ????] code=%s order_id=%s ????:%s" % (code, order_id, r))
        return r
    except Exception as e:
        print("[ATR_EW ???????] code=%s order_id=%s: %s" % (code, order_id, e))
        return None


def _check_pending_orders(C):
    global _g_my_codes, _g_cumulative_pnl
    now = time.time()
    for code in list(_g_pending_sells.keys()):
        pending = _g_pending_sells[code]
        if now - pending['time'] > 30:
            oid, _ = _lookup_order(C, code, pending['shares'], 'sell')
            if oid:
                _cancel_order(C, code, oid)
            # R4???: ??????? ledger ????????л???????????????′ε???/????????????????λ
            print("[ATR_EW pending???] %s ????δ?????, ????????????" % code)
            del _g_pending_sells[code]
            continue
        oid, matched = _lookup_order(C, code, pending['shares'], 'sell')
        if oid:
            print("[ATR_EW pending???] %s ????????%s?????" % (code, oid))
            pnl = (pending['price'] - pending.get('buy_price', pending['price'])) * pending['shares']
            _g_cumulative_pnl += pnl
            _log_trade('????(?????)', code, pending['price'], pending['shares'], pending.get('reason', ''))
            del _g_pending_sells[code]
    for code in list(_g_pending_buys.keys()):
        pending = _g_pending_buys[code]
        if now - pending['time'] > 30:
            oid, _ = _lookup_order(C, code, pending['shares'], 'buy')
            if oid:
                _cancel_order(C, code, oid)
            # ?????????п????????????? ledger ?????????????????????
            # ??????????? R5 ??????У????????
            print("[ATR_EW pending???] %s ????δ?????, ?????????(???)" % code)
            del _g_pending_buys[code]
            continue
        oid, matched = _lookup_order(C, code, pending['shares'], 'buy')
        if oid:
            print("[ATR_EW pending???] %s ??????%s?????" % (code, oid))
            del _g_pending_buys[code]


def _execute_sells(C, to_sell, current_prices):
    """?????б??????????????"""
    for code, reason in to_sell:
        info = _g_my_codes.get(code)
        if info is None:
            continue
        shares = info.get('shares', 0)
        price = current_prices.get(code, 0)
        if shares <= 0 or price <= 0:
            continue
        try:
            # ????????? ledger ????????????? -1=??????????????????????????????λ
            order_id = passorder(
                24,  # 24=????
                1101 if price >= 1.0 else 1102,
                _ACCOUNT_ID,
                code,
                5,   # ????????????
                price,
                shares,  # ????????????????????-1????????????????
                'ATR_EW',  # R7: ????????V2 ????? remark ????????????
                2,
                '',
                C,
            )
            print("[ATR_EW ????] %s %d?? @ %.3f ???:%s ?????:%s" % (code, shares, price, reason, order_id))
            oid, matched = _lookup_order(C, code, shares, 'sell')
            if oid:
                print("[ATR_EW ???????] %s ????%s?????" % (code, oid))
                pnl = (price - info.get('buy_price', price)) * shares
                _g_cumulative_pnl += pnl
                _log_trade('????', code, price, shares, reason)
                del _g_my_codes[code]
            else:
                print("[ATR_EW ???????????] %s ???pending, ????????????" % code)
                _g_pending_sells[code] = {
                    'shares': shares, 'price': price, 'reason': reason, 'time': time.time(),
                }
                # R4???: ???????? ledger???????/???????????????λ?????????/?′ε???У?
        except Exception as e:
            print("[ATR_EW ???????] %s: %s" % (code, e))


def _get_account_cash(C):
    """取账户可用现金（本策略只花自己额度，但不透支账户影响别的策略）"""
    # 方法1: C.get_account_info() (QMT模拟端无此接口)
    try:
        info = C.get_account_info()
        if isinstance(info, dict):
            c = info.get('cash')
            if c is None:
                c = info.get('available_cash')
            if c is not None:
                return float(c)
        c = getattr(info, 'cash', None)
        if c is None:
            c = getattr(info, 'available_cash', None)
        if c is not None:
            return float(c)
    except Exception:
        pass
    # 方法2: get_trade_detail_data 查账户资金 (QMT模拟端标准接口)
    try:
        f_get = globals().get('get_trade_detail_data')
        if f_get is None:
            f_get = getattr(C, 'get_trade_detail_data', None)
        if f_get is not None:
            accts = f_get(_ACCOUNT_ID, 'stock', 'account')
            if accts:
                for a in accts:
                    avail = getattr(a, 'm_dAvailable', None)
                    if avail is not None:
                        return float(avail)
                    avail = getattr(a, 'm_dCash', None)
                    if avail is not None:
                        return float(avail)
    except Exception:
        pass
    # 方法3: C.get_cash() (部分QMT版本)
    try:
        c = C.get_cash()
        if isinstance(c, dict):
            avail = c.get('m_dAvailable', c.get('available', None))
            if avail is not None:
                return float(avail)
        elif c is not None:
            return float(c)
    except Exception:
        pass
    print("[ATR_EW] 取账户现金全部失败(降级:不设上限): 无可用接口")
    return 1e18


def _partial_sell(C, code, shares, price, reason):
    """????????????е???????????????????????????????????"""
    global _g_my_codes, _g_cumulative_pnl
    info = _g_my_codes.get(code)
    if info is None or shares <= 0 or price <= 0:
        return
    try:
        order_id = passorder(
            24,  # 24=????
            1101 if price >= 1.0 else 1102,
            _ACCOUNT_ID,
            code,
            5,   # ????????????
            price,
            shares,  # R8???: ???? 6/7 λ???~????????????????????????????????????
            'ATR_EW',
            2,
            '',
            C,
        )
        print("[ATR_EW ????????] %s %d?? @ %.3f ???:%s ?????:%s" % (code, shares, price, reason, order_id))
        oid, matched = _lookup_order(C, code, shares, 'sell')
        if oid:
            pnl = (price - info.get('buy_price', price)) * shares
            _g_cumulative_pnl += pnl
            _log_trade('????', code, price, shares, reason)
            info['shares'] -= shares
            if info['shares'] <= 0:
                del _g_my_codes[code]
        else:
            print("[ATR_EW ???????????????] %s ???pending, ????????????" % code)
            _g_pending_sells[code] = {'shares': shares, 'price': price, 'reason': reason, 'time': time.time()}
            # R4???: ??????????? ledger????????У?
    except Exception as e:
        print("[ATR_EW ???????????] %s: %s" % (code, e))


def _execute_buys_equalweight(C, target_codes, prices):
    """等权再平衡（基于 NAV 算出目标每只股票 NAV/n_target 的目标持仓值，
    只买入增量 delta，卖出 / 跳过已持仓或超配的股票。）
    NAV = 本策略持仓当前值(含未实现盈亏)，含虚拟现金（账户现金+透支额）。"""
    global _g_my_codes, _g_cumulative_pnl
    holdings_value = sum(info.get('shares', 0) * prices.get(code, info.get('buy_price', 0))
                         for code, info in _g_my_codes.items())
    nav = max(holdings_value, _STRATEGY_CAPITAL)
    n_target = max(len(target_codes), 1)
    target_value = nav / n_target
    virtual_cash = nav - holdings_value
    acct_cash = _get_account_cash(C)
    spendable = min(virtual_cash, acct_cash)
    # 修复20260819: 资金不足均分时，自动缩减持仓数确保每只至少能买1手(100股)
    # 10万/100只=1000元/只，大部分股票1手>1000元，全部跳过=零成交
    # 改为：找出最低价股票的1手成本，算出最多能买几只，截取target_codes前N只
    min_lot_costs = []
    for code in target_codes:
        p = prices.get(code, 0)
        if p and p > 0:
            min_lot_costs.append((code, p * 100))
    min_lot_costs.sort(key=lambda x: x[1])
    affordable_n = 0
    remaining_cash = spendable
    for code, lot_cost in min_lot_costs:
        if lot_cost <= remaining_cash:
            remaining_cash -= lot_cost
            affordable_n += 1
        else:
            break
    if affordable_n < n_target:
        affordable_codes = [c for c, _ in min_lot_costs[:affordable_n]]
        print("[ATR_EW 仓位缩减] 资金%.0f仅够买%d只(原目标%d只), 截取最低价%d只"
              % (spendable, affordable_n, n_target, affordable_n))
        target_codes = affordable_codes
        n_target = max(len(target_codes), 1)
        target_value = nav / n_target
        spendable = min(virtual_cash, acct_cash)

    for code in target_codes:
        price = prices.get(code)
        if price is None or price <= 0:
            continue
        # ????飺???3????? 0 ??????
        df = _g_all_data.get(code)
        if df is not None and len(df) > 0:
            try:
                if float(df['volume'].iloc[-1]) <= 0:
                    continue
            except Exception:
                pass
        held = _g_my_codes.get(code, {}).get('shares', 0)
        desired = int(target_value / price / 100) * 100
        delta = desired - held
        if delta > 0:
            # ?????????? spendable ???
            if delta * price > spendable + 1:
                delta = int(spendable / price / 100) * 100
                if delta <= 0:
                    continue
            # R6???: ?μ????????????????????????? V2 ??????????????????
            # ???д ledger ??????????
            try:
                _acct_cash_now = _get_account_cash(C)
                if delta * price > _acct_cash_now + 1:
                    print("[ATR_EW ?????????] %s ??%.0f ???????%.0f" % (code, delta * price, _acct_cash_now))
                    continue
            except Exception:
                pass
            try:
                order_id = passorder(
                    23,  # 23=????
                    1101,
                    _ACCOUNT_ID,
                    code,
                    5,   # ????????????
                    price,
                    delta,
                    'ATR_EW',
                    2,
                    '',
                    C,
                )
                print("[ATR_EW ????] %s %d?? @ %.3f ??????=%.0f ?????:%s"
                      % (code, delta, price, target_value, order_id))
                oid, matched = _lookup_order(C, code, delta, 'buy')
                if oid:
                    print("[ATR_EW ???????] %s ????%s?????" % (code, oid))
                else:
                    print("[ATR_EW ?????????] %s ???pending" % code)
                    _g_pending_buys[code] = {'shares': delta, 'price': price, 'time': time.time()}
                if held > 0:
                    # ?????????????
                    info = _g_my_codes[code]
                    new_shares = held + delta
                    info['buy_price'] = (info.get('buy_price', price) * held + price * delta) / new_shares
                    info['shares'] = new_shares
                    info['peak_price'] = max(info.get('peak_price', price), price)
                else:
                    _g_my_codes[code] = {
                        'buy_price': price,
                        'buy_date': _get_qmt_time(C).strftime('%Y%m%d'),  # ??? 20260819
                        'shares': delta,
                        'peak_price': price,
                    }
                _log_trade('????', code, price, delta, 'ATR??????????')
                spendable -= delta * price
            except Exception as e:
                print("[ATR_EW ???????] %s: %s" % (code, e))
        elif delta < 0:
            # ?????????????????????? ledger ?????????
            _partial_sell(C, code, -delta, price, '????????(????????)')


# ============================================================
# ??????棨?? ATR% ? N + ???????
# ============================================================
def _batch_get_roe(codes):
    """????? ROE(%)????? IPC ???????????????????????????????? {code: roe}??"""
    global _g_roe_api_ok, _g_roe_cache
    if _g_roe_api_ok is False:
        return {}
    if not codes:
        return {}
    result = {}
    # QMT???? Python ?? xtdata?????????????? ROE ??????? (fail-open)
    try:
        import xtdata
    except Exception as e:
        print("[ATR_EW] ROE???xtdata??????, ??????????????(fail-open): %s" % e)
        _g_roe_api_ok = False
        return {}
    step = 200
    for i in range(0, len(codes), step):
        batch = codes[i:i + step]
        try:
            res = xtdata.get_financial_data(['roe'], batch)
        except Exception as e:
            print("[ATR_EW] ????ROE??????(????%d): %s" % (i // step, e))
            _g_roe_api_ok = False
            continue
        if isinstance(res, dict):
            for code in batch:
                d = res.get(code)
                if isinstance(d, dict):
                    v = d.get('roe')
                else:
                    v = d
                if v is not None:
                    try:
                        result[code] = float(v)
                        _g_roe_cache[code] = result[code]
                    except Exception:
                        pass
    if result:
        _g_roe_api_ok = True
    return result


def _run_screening(C):
    """??г? ATR ?????????????????????б????? ATR% ? N_HOLD????"""
    global _g_hold_pool_cache, _g_hold_pool_cache_date, _g_all_data, _g_turnover_available

    today_str = _get_qmt_time(C).strftime('%Y%m%d')  # ??? 20260819: ????QMT???????(AGENTS????)
    if _g_hold_pool_cache is not None and _g_hold_pool_cache_date == today_str:
        print("[ATR_EW] ??????????: %d ?" % len(_g_hold_pool_cache))
        return _g_hold_pool_cache

    try:
        all_codes = C.get_stock_list_in_sector('????A??')
        codes = [c for c in all_codes if c.endswith('.SH') or c.endswith('.SZ')]
        print("[ATR_EW] ??г? %d ?, ?????..." % len(codes))
    except Exception as e:
        print("[ATR_EW] get_stock_list_in_sector???: %s" % e)
        return []

    # ??????????г????????????????? 252 ????
    try:
        data = C.get_market_data_ex(stock_code=codes, period='1d', count=_MIN_HISTORY + 10)
        if not data:
            return []
        _g_all_data = data
    except Exception as e:
        print("[ATR_EW] ??????????: %s" % e)
        return []

    # ??????????????????????????????
    # ??? 20260818??????????(_scale_turnover) + ???????(hasattr) + ??? fail-open??
    # ??棺????????100??????????250?????????????????????λ?????????
    # _g_turnover_available ??? True + map ??? ?? ??? to=-1 ?? ????????0???????
    turnover_map = {}
    for code, df in data.items():
        try:
            if df is not None and 'turnover_rate' in df.columns:
                s = df['turnover_rate'].dropna()
                if len(s) > 0:
                    to = _scale_turnover(float(s.iloc[-1]))
                    if to is not None:
                        turnover_map[code] = to
        except Exception:
            pass
    if not turnover_map:
        try:
            if hasattr(C, 'get_turnover_rate'):
                start = (_get_qmt_time(C) - timedelta(days=120)).strftime('%Y%m%d')  # ??? 20260819
                end = today_str
                td = C.get_turnover_rate(codes, start, end)
                if td is not None and hasattr(td, 'columns'):
                    for code in codes:
                        if code in td.columns:
                            s = td[code].dropna()
                            if len(s) > 0:
                                to = _scale_turnover(float(s.iloc[-1]))
                                if to is not None:
                                    turnover_map[code] = to
        except Exception:
            pass
    if turnover_map:
        _g_turnover_available = True  # ??? 20260819: ??????á????ù???(????????ò?????)
        print("[ATR_EW] ?????????????: %d ? (????: %s)" % (len(turnover_map), list(turnover_map.items())[:3]))
    else:
        _g_turnover_available = False
        print("[ATR_EW] ????: ????????????????????????????????fail-open?????????")
    # ST ????????????????????????????????????????г?????????? API??
    st_set = set()
    try:
        lst = C.get_stock_list_in_sector('????????')
        if lst:
            st_set = set(lst)
    except Exception:
        pass
    if not st_set:
        try:
            lst = C.get_stock_list_in_sector('ST')
            if lst:
                st_set = set(lst)
        except Exception:
            pass
    if not st_set:
        print("[ATR_EW] ????: ST??????????????ST??????????????????????")

    eligible = []
    skip_len = 0
    skip_close = 0
    skip_price = 0
    skip_atr_flat = 0
    skip_atr_high = 0
    skip_turn = 0
    skip_st = 0
    skip_mom = 0
    skip_exc = 0
    for code, df in data.items():
        if df is None or len(df) < _MIN_HISTORY:
            skip_len += 1
            continue
        try:
            cl = float(df['close'].iloc[-1])
            if cl <= 0:
                skip_close += 1
                continue
            # ?????????????QMT ???鼴??????????????????С???????????
            if _MAX_PRICE > 0 and cl >= _MAX_PRICE:
                skip_price += 1
                continue
            # ATR% ??????????????atr_pct<=0 ??????????/???(??14??ATR=0)
            atr_pct = _calc_atr_pct(df)
            if atr_pct <= 0:
                skip_atr_flat += 1
                continue
            if atr_pct >= _ATR_THRESHOLD:
                skip_atr_high += 1
                continue
            # ????????????? 20260818: fail-open???????????????????????????????
            if _g_turnover_available is True:
                to = turnover_map.get(code)
                if to is not None and (to < _MIN_TURNOVER or to > _MAX_TURNOVER):
                    skip_turn += 1
                    continue
            # ?? ST????鼯???ж??O(1)??
            if code in st_set:
                skip_st += 1
                continue
            # ????????12-1 ?????? > 0????????????????????????????????API??
            if _MOMENTUM_GATE:
                close = df['close'].astype(float).values
                if len(close) >= 252:
                    ret_12_1 = close[-21] / close[-252] - 1.0 if close[-252] > 0 else 0.0
                    if ret_12_1 <= 0:
                        skip_mom += 1
                        continue
            eligible.append((code, atr_pct))
        except Exception:
            skip_exc += 1
            continue
    # ????????ROE > 0????????????????????????????????? IPC??
    if _QUALITY_GATE and eligible:
        print("[ATR_EW] ?????? %d ????????ROE..." % len(eligible))
        roe_map = _batch_get_roe([c for c, _ in eligible])
        if _g_roe_api_ok is False or not roe_map:
            # R9(2026-08-15 ??????): ???????? ?? ???????? ?? fail-open????????????
            print("[ATR_EW] ????: ROE???????????????????????ROE????R9 fail-open??")
        else:
            filtered = []
            for code, atr_pct in eligible:
                roe = roe_map.get(code)
                if roe is None or roe <= 0:
                    continue
                filtered.append((code, atr_pct))
            eligible = filtered

    # MAX5 ???Ч????????20????????????pct_change tail(20).max????????
    # strategy/atr_lowvol.py L216-224 ?????????????? _MAX_EXCLUDE_PCT ??λ??0=???
    if _MAX_EXCLUDE_PCT > 0 and len(eligible) > _N_HOLD:
        max5_list = []
        for code, _ in eligible:
            try:
                df5 = _g_all_data[code]
                rr = df5['close'].astype(float).pct_change().dropna()
                max5_list.append(float(rr.tail(20).max()) if len(rr) > 0 else 0.0)
            except Exception:
                max5_list.append(0.0)
        max5_sorted = sorted(max5_list)
        thr = max5_sorted[max(0, int(len(max5_sorted) * (1.0 - _MAX_EXCLUDE_PCT)) - 1)]
        eligible = [e for e, m5 in zip(eligible, max5_list) if m5 <= thr]
        print("[ATR_EW] MAX5????: ??????%.0f%%??λ -> ???%d? (???%.2f)"
              % (_MAX_EXCLUDE_PCT * 100, len(eligible), thr))

    # ?? ATR% ?????? N_HOLD????????????
    eligible.sort(key=lambda x: x[1])
    selected = [c for c, _ in eligible[:_N_HOLD]]

    _g_hold_pool_cache = selected
    _g_hold_pool_cache_date = today_str

    print("[ATR_EW] ?????: ???%d -> ???%d? (??????%d)" % (len(eligible), len(selected), skip_price))
    print("[ATR_EW] ???????: ????<252=%d ??<=0=%d ATR???=%d ATR>=%.1f%%=%d ????=%d ST=%d ????=%d ??=%d"
          % (skip_len, skip_close, skip_atr_flat, _ATR_THRESHOLD, skip_atr_high, skip_turn, skip_st, skip_mom, skip_exc))
    for c in selected[:15]:
        name = _get_stock_name_safe(C, c)
        print("    [ATR_EW ???] %s %s ATR%%=%.2f" % (c, name, dict(eligible).get(c, 0)))
    if len(selected) > 15:
        print("    ... ???? %d ????" % (len(selected) - 15))

    return selected


# ============================================================
# ????? + ??????
# ============================================================
def _current_prices(C, codes):
    prices = {}
    if not codes:
        return prices
    # ??????????????????г??????count ?????????????????? count=2 ????????
    for code in codes:
        df = _g_all_data.get(code)
        if df is not None and len(df) > 0:
            try:
                prices[code] = float(df['close'].iloc[-1])
            except Exception:
                pass
    missing = [c for c in codes if c not in prices]
    if missing:
        try:
            data = C.get_market_data_ex(stock_code=list(missing), period='1d', count=2)
            if data:
                for code, df in data.items():
                    if df is not None and len(df) > 0:
                        prices[code] = float(df['close'].iloc[-1])
        except Exception as e:
            print("[ATR_EW] ??????λ?????: %s" % e)
        still = [c for c in codes if c not in prices]
        if still:
            print("[ATR_EW] ????: %d ?????????: %s" % (len(still), ",".join(still)))
    return prices
def _quarter_key(now):
    q = (now.month - 1) // 3 + 1
    return "%dQ%d" % (now.year, q)


def _rebalance_to_target(C, target_codes):
    """????????????????????????????????????????δ?????"""
    prices = _current_prices(C, list(set(list(_g_my_codes.keys()) + list(target_codes))))

    held_codes = list(_g_my_codes.keys())
    to_sell = [(c, '????(???????)') for c in held_codes if c not in target_codes]
    if to_sell:
        print("[ATR_EW ?????] ???? %d ????????" % len(to_sell))
        _execute_sells(C, to_sell, prices)
        _save_holdings()

    # ????????????????????????
    _execute_buys_equalweight(C, target_codes, prices)
    _save_holdings()

    nav = max(sum(info.get('shares', 0) * prices.get(code, info.get('buy_price', 0))
                   for code, info in _g_my_codes.items()), _STRATEGY_CAPITAL)
    print("[ATR_EW ????????] ???%d? ??????%d? ??????NAV=%.0f"
          % (len(target_codes), len(_g_my_codes), nav))


def _evaluate_interim_stops(C, prices):
    """??????????????? stop_loss ????????"""
    if _STOP_LOSS is None or _STOP_LOSS >= 0:
        return []
    to_sell = []
    for code, info in _g_my_codes.items():
        price = prices.get(code)
        if price is None or price <= 0:
            continue
        bp = info.get('buy_price', 0)
        if bp > 0 and (price - bp) / bp <= _STOP_LOSS:
            to_sell.append((code, '?????? %.1f%%' % ((price - bp) / bp * 100)))
    return to_sell


# ============================================================
# ?????
# ============================================================
def _main_loop(C):
    global _g_last_rebalance_key, _g_cooling_until, _g_last_attempt_date

    # R2???: ???K?????????????? src β??? is_last_bar?????????????????????????bar????
    try:
        if not (C.do_back_test or C.do_backtest):
            if getattr(C, 'is_last_bar', None) is not None and not C.is_last_bar():
                return
    except Exception:
        pass

    now = _get_qmt_time(C)
    now_str = now.strftime('%H%M') if hasattr(now, 'strftime') else str(now)
    weekday = now.weekday() if hasattr(now, 'weekday') else datetime.now().weekday()
    skip_weekday = False
    try:
        if C.do_back_test or C.do_backtest:
            skip_weekday = True
    except Exception:
        pass
    if not skip_weekday and weekday >= 5:
        return

    print("[ATR_EW] %s ???? ???%d?[%s]" % (
        now_str, len(_g_my_codes), ','.join(_g_my_codes.keys()) if _g_my_codes else '??'))

    # cooling-off
    if _g_cooling_until > 0 and time.time() < _g_cooling_until:
        remaining = int(_g_cooling_until - time.time())
        print("[ATR_EW] cooling-off ??(%ds)" % remaining)
        return

    _check_pending_orders(C)

    # ???????????????仯 = ???????????????? ??????????????/????????????
    key = _quarter_key(now)
    today_y = now.strftime('%Y%m%d') if hasattr(now, 'strftime') else datetime.now().strftime('%Y%m%d')
    is_rebalance_day = (key != _g_last_rebalance_key)
    # ?????????δ????????????????????Σ???????????????
    # ????"??????????Q3???? + ?????????"???????????????????
    force_retry = (len(_g_my_codes) == 0) and (_g_last_attempt_date != today_y)

    if is_rebalance_day or force_retry:
        print("[ATR_EW] 再平衡触发(%s, 季频=%s, 空仓兜底=%s)" % (key, is_rebalance_day, force_retry))
        selected = _run_screening(C)
        if selected:
            _rebalance_to_target(C, selected)
        else:
            print("[ATR_EW] 选股无候选，跳过再平衡（不刷新季度键，允许空仓兜底重试）")
        # 修复20260819: 无论买入是否成功，都刷新季度键，防止每bar重复选股+买入死循环
        # 原逻辑：_g_my_codes为空时不刷新 -> force_retry=True -> 无限循环
        # 新逻辑：季度内只执行一次选股；空仓兜底(force_retry)每天只执行一次
        if selected:
            _g_last_rebalance_key = key
        _g_last_attempt_date = today_y
    else:
        # ?????????????????????????????????
        if _STOP_LOSS is not None and _STOP_LOSS < 0:
            prices = _current_prices(C, list(_g_my_codes.keys()))
            to_sell = _evaluate_interim_stops(C, prices)
            if to_sell:
                print("[ATR_EW ??????] %d???????" % len(to_sell))
                _execute_sells(C, to_sell, prices)
                _save_holdings()
            else:
                print("[ATR_EW] ??????????: ??")


# ============================================================
# QMT ????????
# ============================================================
class StrategyRunner(object):
    def __init__(self):
        self.initialized = False

    def init(self, C):
        global _g_initialized, _g_cooling_until, _g_last_rebalance_key
        print("[ATR_EW] =============================================")
        print("[ATR_EW] ATR?????-???????? ?????... build=%s" % BUILD_TAG)
        print("[ATR_EW] =============================================")

        _load_config()
        _load_holdings()
        _reconcile_own_holdings(C)

        print("[ATR_EW] ???????? ???=%s" % _ACCOUNT_ID)
        print("[ATR_EW] ????=%d N_HOLD=%d ???=%s ROE???=%d ???????=%d max_price=%.0f MAX5???=%.0f%% ???=%.2f"
              % (_STRATEGY_CAPITAL, _N_HOLD, _REBALANCE_FREQ, _QUALITY_GATE,
                 _MOMENTUM_GATE, _MAX_PRICE, _MAX_EXCLUDE_PCT * 100, _STOP_LOSS))

        try:
            if C.do_back_test or C.do_backtest:
                _g_cooling_until = 0
                print("[ATR_EW] ??????????? cooling-off")
            else:
                _g_cooling_until = time.time() + 60
                print("[ATR_EW] ???? cooling-off ??(60s)")
        except Exception:
            _g_cooling_until = time.time() + 60

        _g_initialized = True
        self.initialized = True

    def handlebar(self, C):
        if not self.initialized:
            return
        try:
            _main_loop(C)
        except Exception as e:
            print("[ATR_EW ??] %s" % e)

    def exit(self, C):
        _save_holdings()
        print("[ATR_EW] ?????????????????")


def init(C):
    C.runner = StrategyRunner()
    C.runner.init(C)


def after_init(C):
    pass


def handlebar(C):
    runner = getattr(C, 'runner', None)
    if runner is not None:
        try:
            runner.handlebar(C)
        except KeyboardInterrupt:
            print("[??] ????ж????????????...")
            raise


def exit(C):
    runner = getattr(C, 'runner', None)
    if runner is not None:
        runner.exit(C)
