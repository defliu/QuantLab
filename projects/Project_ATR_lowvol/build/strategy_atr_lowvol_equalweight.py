# coding=gbk
# coding=gbk
"""
ATR 低波动策略 - 等权 不杠杆 部署版（QMT 全闭环，零外部依赖）

对应回测框架配置 atr_lowvol_fw.yaml（等权 不杠杆，年化 +18.69% 验证版）：
  选股: ATR% 最低分位(top N) + 换手率[1,8]% + 非ST + 上市>=252日
        + 质量门控(ROE>0) + 动量门控(12-1月收益>0，剔除近期输家)
  调仓: 季频（每季度的首个交易日）
  仓位: 等权（每票目标市值 = 总资产 / N_HOLD），不杠杆
  风控: 整数手(100股) / ST+-5% / 停牌(QMT成交层天然处理)
        持仓间际止损 -8%（可关，对应框架 stop_loss）

与旧版 deploy/strategy_atr_lowvol.py 的区别：
  旧版 = max_hold=3 + 止损止盈 + 条件失效退出（即回测 -5.85% 那套）。
  本版 = 框架真实约束版：8只等权 + 季频再平衡 + max_price50
        + MAX5彩票过滤（回测 atr_10w_price50_a_max 那套）。

数据来源：QMT 行情上下文 C（get_market_data_ex / get_turnover_rate / get_stock_list_in_sector）。
ROE 质量门控走 xtdata.get_financial_data，若接口不可用则自动跳过该门控（不崩溃）。
"""
import json
import os
import time
import math
from datetime import datetime, timedelta

# ============================================================
# 配置（内置默认值，可被 config/atr_lowvol_equalweight_config.yaml 覆盖）
# ============================================================
CONFIG = {
    'strategy': {
        'name': 'ATR_LOWVOL_EW',
        'display_name': 'ATR低波动-等权不杠杆',
        'capital_base': 1000000,
        'account_id': '70180771',   # 2026-08-23 换新模拟账号(诚哥提供)
    },
    'screening': {
        'n_hold': 8,
        'atr_threshold': 6.0,
        'min_turnover': 1.0,
        'max_turnover': 8.0,
        'min_history': 252,
        'quality_gate': 1,
        'momentum_gate': 1,
        'max_price': 50.0,          # 真实收盘价上限(元)：高价股小资金买不起整手
        'max_exclude_pct': 0.20,    # MAX5彩票过滤：剔除近20日最大单日涨幅最高20%分位(0=关)
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

# 构建版本标记（YYYYmmdd-HHMMSS），部署核对用
BUILD_TAG = "20260823-223613"  # P0: 盘前当日空bar(volume=0)误判停牌静默跳过->末两根无量才判停牌 + P0: 季度键死守卫(if selected:恒真)盘前假成功锁死整季->真实建仓后才锁键 + P1: 建仓限盘中窗口0933-1455 + P1: 空仓兜底改30分钟间隔重试(原每天一次被盘前失败烧毁) + P1: 账号换70180771(原67014907) + P2: ATR百分比剔除平填bar(volume<=0)对齐astock回测口径  # 历史tag: 20260819-201531(Fix4编码重放/R9三态/对账字段修复/换手尺度自适应)

# ============================================================
# 全局状态
# ============================================================
_g_my_codes = {}            # code -> {buy_price, buy_date, shares, peak_price, ...}
_g_cumulative_pnl = 0.0
_g_nav_history = []
_g_all_data = {}            # code -> DataFrame（再平衡日全市场快照）
_g_hold_pool_cache = None   # 选股结果缓存
_g_hold_pool_cache_date = ''
_g_initialized = False
_g_cooling_until = 0.0
_g_last_rebalance_key = ''  # 上次再平衡的 季度键(如 2026Q3)
_g_last_attempt_ts = 0.0    # 上次再平衡尝试时刻 time.time() 秒（空仓兜底限频用；修复20260823）
_RETRY_MIN_INTERVAL = 1800  # 空仓兜底重试最小间隔秒（防每bar刷屏；选股有日缓存，重试开销低）
_g_pending_sells = {}       # code -> {shares, price, reason, time}
_g_pending_buys = {}        # code -> {shares, price, time}
_g_roe_cache = {}           # code -> roe（季内缓存）
_g_roe_api_ok = None        # None=未探明 True/False
_g_turnover_available = None  # 修复 20260819: 首次筛选前不应默认True

# 可配置参数（被 _load_config 覆盖；config 中 capital_base 设定本策略锁定可用资金）
_STRATEGY_CAPITAL = 100000
_ACCOUNT_ID = '70180771'  # 2026-08-23 换新模拟账号（原67014907），外部config仍可覆盖
_N_HOLD = 8
_ATR_THRESHOLD = 6.0
_MIN_TURNOVER = 1.0
_MAX_TURNOVER = 8.0
_MIN_HISTORY = 252
_QUALITY_GATE = 1
_MOMENTUM_GATE = 1
_MAX_PRICE = 50.0                 # 真实价上限(元)，0=关闭
_MAX_EXCLUDE_PCT = 0.20           # MAX5彩票过滤：剔除近20日最大单日涨幅最高20%分位(0=关)
_REBALANCE_FREQ = 'quarterly'
_STOP_LOSS = -0.08
_REBALANCE_RETRY_DAYS = 1   # 空仓兜底：未建仓时每隔几天重试一次（1=每天）

_LOOKUP_RETRIES = 4
_LOOKUP_INTERVAL = 0.2
_HOLDINGS_FILE = 'D:/QMT_POOL/atr_ew_holdings.json'
_NAV_FILE = 'D:/QMT_POOL/atr_ew_nav.json'
_TRADE_LOG_FILE = 'D:/QMT_POOL/atr_ew_trade_log.csv'


# ============================================================
# 配置加载（简易 YAML 解析，不依赖 pyyaml）
# ============================================================
def _load_config():
    global _STRATEGY_CAPITAL, _ACCOUNT_ID, _N_HOLD, _ATR_THRESHOLD
    global _MIN_TURNOVER, _MAX_TURNOVER, _MIN_HISTORY, _QUALITY_GATE
    global _MOMENTUM_GATE, _MAX_PRICE, _MAX_EXCLUDE_PCT, _REBALANCE_FREQ, _STOP_LOSS
    global _HOLDINGS_FILE, _NAV_FILE, _TRADE_LOG_FILE

    config_path = 'D:/QMT_POOL/config/atr_lowvol_equalweight_config.yaml'
    if not os.path.exists(config_path):
        print("[ATR_EW] 无配置文件，使用内置默认值")
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
        print("[ATR_EW] 配置加载完成: N_HOLD=%d ATR<%.2f%% turnover[%.1f,%.1f] max_price=%.0f max_exclude_pct=%.2f freq=%s"
              % (_N_HOLD, _ATR_THRESHOLD, _MIN_TURNOVER, _MAX_TURNOVER, _MAX_PRICE, _MAX_EXCLUDE_PCT, _REBALANCE_FREQ))
    except Exception as e:
        print("[ATR_EW] 配置加载失败: %s, 使用默认值" % e)


# ============================================================
# 辅助函数
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
    """计算 ATR(14)/close * 100（与框架 factors/atr.atr_pct 同口径）。

    修复 20260823(P2)：先剔除平填bar(volume<=0：停牌平填/盘前未完成bar)再计算，
    对齐 astock 回测口径（仅真实成交bar）；否则ATR%被稀释12~37%选股漂移。
    """
    if df is None or len(df) < 15:
        return 999.0
    try:
        if 'volume' in df.columns:
            df = df[df['volume'] > 0]
            if len(df) < 15:
                return 999.0
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
    """换手率尺度自适应（对齐 src/strategy_atr.py）：返回已适配为百分比的值。
    尺度自适应：<1 视为小数（×100），>=1 视为已是百分比（如 2.5=2.5%）。
    修复 20260818：旧版无条件 ×100，若接口返回百分比（2.5）则 ×100=250 全杀候选。
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
    """质量门控：取最新 ROE(%)。接口不可用则自动跳过门控（返回通过）。"""
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
            print("[ATR_EW] ROE 接口不可用，质量门控自动跳过")
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
            # 账号戳校验（T-20260823-004）：账本只属于一个模拟账号，双账号并存防拿错档；
            # 不匹配或缺戳(历史遗留) -> 自动备份旧档并空仓起步(fail-safe)。
            stamp = str(data.get('account_id', ''))
            if stamp != str(_ACCOUNT_ID):
                bak = '%s.bak_acct_%s_%s' % (_HOLDINGS_FILE, stamp or 'nostamp', time.strftime('%Y%m%d_%H%M%S'))
                try:
                    fbk = open(_HOLDINGS_FILE, 'rb')
                    _raw_b = fbk.read()
                    fbk.close()
                    fbk2 = open(bak, 'wb')
                    fbk2.write(_raw_b)
                    fbk2.close()
                    print("[ATR_EW] [!] 账本账号戳不匹配(账本=%s 本策略=%s)，旧档已备份 %s，空仓起步" % (stamp or '无戳', _ACCOUNT_ID, os.path.basename(bak)))
                except Exception as e_bak:
                    print("[ATR_EW] [!] 账本账号戳不匹配(账本=%s 本策略=%s)，备份失败(%s)，空仓起步" % (stamp or '无戳', _ACCOUNT_ID, e_bak))
                _g_my_codes = {}
                _g_cumulative_pnl = 0.0
                _g_nav_history = []
                return
            _g_my_codes = data.get('holdings', {})
            _g_cumulative_pnl = data.get('cumulative_pnl', 0.0)
            _g_nav_history = data.get('nav_history', [])
            print("[ATR_EW] 加载持仓 %d 只, 累计盈亏 %.2f" % (len(_g_my_codes), _g_cumulative_pnl))
        except Exception as e:
            print("[ATR_EW] 持仓加载失败: %s" % e)
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
            'account_id': _ACCOUNT_ID,
            'holdings': _g_my_codes,
            'cumulative_pnl': _g_cumulative_pnl,
            'nav_history': _g_nav_history[-500:],
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(_HOLDINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[ATR_EW] 持仓保存失败: %s" % e)


def _log_trade(trade_type, code, price, shares, reason):
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = "%s,%s,%s,%.3f,%d,%s\n" % (now, trade_type, code, price, shares, reason)
        with open(_TRADE_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass


def _reconcile_own_holdings(C):
    """仅依据本策略 ledger 与账户对账：账户里已没有的(被外部卖出)就从 ledger 移除；
    绝不清管/接管别的策略的持仓，保证多策略共存时票与资金互不干扰。"""
    try:
        f_get = globals().get('get_trade_detail_data')
        if f_get is None:
            f_get = getattr(C, 'get_trade_detail_data', None)
        if f_get is None:
            print("[ATR_EW 对账] get_trade_detail_data 不可用，跳过本次对账")
            return
        positions = f_get(_ACCOUNT_ID, 'stock', 'position')
        if not positions:
            return
        account_pos = {}
        for pos in positions:
            # 远程QMT position 为 CPositionDetail 对象（字段 m_strInstrumentID/m_strSecurityCode，
            # 非 m_strCode、无 .get()，对齐 V2 _get_acct_position 读取方式）
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
        # 只移除本策略 ledger 中、账户已不存在的票（如被手动/外部卖出）
        to_remove = [c for c in _g_my_codes if c not in account_pos]
        for c in to_remove:
            print("[ATR_EW 对账] %s 账户已无此持仓(或已外部卖出), 移除本策略记录" % c)
            del _g_my_codes[c]
        # R5修复: 份额数向下校准（ledger 声称 > 账户实际时取 min，防多卖他策略/外部已减持部分；
        # 仅向下不向上，避免把别人票接进本策略）
        for c in list(_g_my_codes.keys()):
            info = _g_my_codes[c]
            acct_vol = account_pos.get(c, 0)
            if info.get('shares', 0) > acct_vol:
                print("[ATR_EW 对账] %s 份额校准 %d -> %d" % (c, info.get('shares', 0), int(acct_vol)))
                info['shares'] = int(acct_vol)
                if info['shares'] <= 0:
                    del _g_my_codes[c]
    except Exception as e:
        print("[ATR_EW 对账] 异常: %s" % e)


# ============================================================
# 订单反查与执行（复用旧版成熟脚手架）
# ============================================================
def _lookup_order(C, code, volume, direction, retries=None, interval=None):
    if retries is None:
        retries = _LOOKUP_RETRIES
    if interval is None:
        interval = _LOOKUP_INTERVAL
    # miniQMT(本地极简端) 的 ContextInfo 没有 get_trade_detail_data 方法，反查必然失败。
    # QMT passorder 是异步接口，返回0/None不代表下单失败；因此做"乐观确认"：
    # 反查方法不存在时直接按成功处理，调用方写/删 ledger、不进 pending 死循环。
    # （参考6+2策略的乐观确认修复2）
    # 修复 20260819: get_trade_detail_data 是全局函数(非C方法)，对齐V2 + 防坑指南
    f_get = globals().get('get_trade_detail_data')
    if f_get is None:
        f_get = getattr(C, 'get_trade_detail_data', None)
    if f_get is None:
        return ('OPTIMISTIC', None)
    dir_cn = '买入' if direction == 'buy' else '卖出'
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
    """撤单：生产级11参数格式（对齐 V2，order_id 为平台委托号，由 _lookup_order 取回）。"""
    if not order_id:
        return None
    try:
        r = passorder(24, 1101, _ACCOUNT_ID, code, 5, order_id, 0, 'ATR_EW撤单', 2, '', C)
        print("[ATR_EW 撤单] code=%s order_id=%s 返回:%s" % (code, order_id, r))
        return r
    except Exception as e:
        print("[ATR_EW 撤单失败] code=%s order_id=%s: %s" % (code, order_id, e))
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
            # R4修复: 超时保留 ledger 持仓（账户有货即真实持仓），待下次调仓/对账重试，防孤儿仓位
            print("[ATR_EW pending超时] %s 卖出未确认超时, 保留持仓待重试" % code)
            del _g_pending_sells[code]
            continue
        oid, matched = _lookup_order(C, code, pending['shares'], 'sell')
        if oid:
            print("[ATR_EW pending确认] %s 卖出订单%s已确认" % (code, oid))
            pnl = (pending['price'] - pending.get('buy_price', pending['price'])) * pending['shares']
            _g_cumulative_pnl += pnl
            _log_trade('卖出(迟确认)', code, pending['price'], pending['shares'], pending.get('reason', ''))
            del _g_pending_sells[code]
    for code in list(_g_pending_buys.keys()):
        pending = _g_pending_buys[code]
        if now - pending['time'] > 30:
            oid, _ = _lookup_order(C, code, pending['shares'], 'buy')
            if oid:
                _cancel_order(C, code, oid)
            # 乐观模式：委托可能已成交，保留 ledger 不回滚（避免误删已持仓）；
            # 若实为被拒，由 R5 对账份额校准兜底纠正
            print("[ATR_EW pending超时] %s 买入未确认超时, 保留持仓记录(乐观)" % code)
            del _g_pending_buys[code]
            continue
        oid, matched = _lookup_order(C, code, pending['shares'], 'buy')
        if oid:
            print("[ATR_EW pending确认] %s 买入订单%s已确认" % (code, oid))
            del _g_pending_buys[code]


def _execute_sells(C, to_sell, current_prices):
    """卖出列表（全部卖出）。"""
    for code, reason in to_sell:
        info = _g_my_codes.get(code)
        if info is None:
            continue
        shares = info.get('shares', 0)
        price = current_prices.get(code, 0)
        if shares <= 0 or price <= 0:
            continue
        try:
            # 只卖本策略 ledger 记录的量（不用 -1=全部），多策略重叠时不会误卖别人的仓位
            order_id = passorder(
                24,  # 24=卖出
                1101 if price >= 1.0 else 1102,
                _ACCOUNT_ID,
                code,
                5,   # 对手价，确保成交
                price,
                shares,  # 仅本策略持仓量（绝不用-1，防误卖他策略份额）
                'ATR_EW',  # R7: 策略标识（V2 对账按 remark 过滤，防串账）
                2,
                '',
                C,
            )
            print("[ATR_EW 卖出] %s %d股 @ %.3f 原因:%s 返回值:%s" % (code, shares, price, reason, order_id))
            oid, matched = _lookup_order(C, code, shares, 'sell')
            if oid:
                print("[ATR_EW 卖出确认] %s 订单%s已确认" % (code, oid))
                pnl = (price - info.get('buy_price', price)) * shares
                _g_cumulative_pnl += pnl
                _log_trade('卖出', code, price, shares, reason)
                del _g_my_codes[code]
            else:
                print("[ATR_EW 卖出反查失败] %s 登记pending, 保留持仓待重试" % code)
                _g_pending_sells[code] = {
                    'shares': shares, 'price': price, 'reason': reason, 'time': time.time(),
                }
                # R4修复: 确认失败不删 ledger（防停牌/部分成交产生孤儿仓位），由对账/下次调仓校准
        except Exception as e:
            print("[ATR_EW 卖出失败] %s: %s" % (code, e))


def _get_account_cash(C):
    """获取账户可用资金三级 fallback：
    1) C.get_account_info() (QMT官方接口优先)
    2) get_trade_detail_data 查询账户待单形式 (QMT新版本其他方式失效)
    3) C.get_cash() (其他QMT版本)
    全部失败时返回 1e18 作为 无空失资金允底
    """
    # 第1层: C.get_account_info() (QMT官方接口优先)
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
    # 第2层: get_trade_detail_data 查询账户待单形式 (QMT新版本其他方式失效)
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
    # 第3层: C.get_cash() (其他QMT版本)
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
    print("[ATR_EW] 获取账户可用资金全部失败(其他单位): 已用大倿值")
    return 1e18

def _is_suspended_bar(df):
    """真停牌判定（修复 20260823 P0）：末两根均无量才判停牌。

    盘前09:15起QMT即生成当日K线(volume=0/turnover=NaN)，旧逻辑只看末根 volume<=0
    就当停牌跳过且该分支无日志，导致09:16建仓时8只候选全部静默丢弃。
    单根无量视为盘前未完成bar放行；连续无量才是真停牌。
    """
    try:
        if df is None or len(df) == 0 or 'volume' not in df.columns:
            return False
        vols = df['volume'].astype(float).dropna()
        if len(vols) == 0:
            return False
        last_v = float(vols.iloc[-1])
        prev_v = float(vols.iloc[-2]) if len(vols) >= 2 else 1.0
        return last_v <= 0 and prev_v <= 0
    except Exception:
        return False


def _partial_sell(C, code, shares, price, reason):
    """卖出本策略持有的部分份额（调仓缩减），不影响别的策略的同票持仓。"""
    global _g_my_codes, _g_cumulative_pnl
    info = _g_my_codes.get(code)
    if info is None or shares <= 0 or price <= 0:
        return
    try:
        order_id = passorder(
            24,  # 24=卖出
            1101 if price >= 1.0 else 1102,
            _ACCOUNT_ID,
            code,
            5,   # 对手价，确保成交
            price,
            shares,  # R8修复: 原实现 6/7 位价格~股数颠倒（易废单），已纠正；仅卖本策略量
            'ATR_EW',
            2,
            '',
            C,
        )
        print("[ATR_EW 调仓卖出] %s %d股 @ %.3f 原因:%s 返回值:%s" % (code, shares, price, reason, order_id))
        oid, matched = _lookup_order(C, code, shares, 'sell')
        if oid:
            pnl = (price - info.get('buy_price', price)) * shares
            _g_cumulative_pnl += pnl
            _log_trade('卖出', code, price, shares, reason)
            info['shares'] -= shares
            if info['shares'] <= 0:
                del _g_my_codes[code]
        else:
            print("[ATR_EW 调仓卖出反查失败] %s 登记pending, 保留持仓待重试" % code)
            _g_pending_sells[code] = {'shares': shares, 'price': price, 'reason': reason, 'time': time.time()}
            # R4修复: 确认失败不缩减 ledger，待对账校准
    except Exception as e:
        print("[ATR_EW 调仓卖出失败] %s: %s" % (code, e))


def _execute_buys_equalweight(C, target_codes, prices):
    """等权再平衡（滚动 NAV）：对目标内每只票按 NAV/n_target 计算目标市值，
    只调整本策略自己的 delta（买增量 / 卖溢出），绝不碰别人的票。
    NAV = 本策略持仓当前市值(含未实现浮盈)，收益滚动加仓；账户现金仅作透支上限。"""
    global _g_my_codes, _g_cumulative_pnl
    # 本策略滚动 NAV：自有持仓当前市值（用实时价，含未实现），至少不低于本金基准
    holdings_value = sum(info.get('shares', 0) * prices.get(code, info.get('buy_price', 0))
                         for code, info in _g_my_codes.items())
    nav = max(holdings_value, _STRATEGY_CAPITAL)
    n_target = max(len(target_codes), 1)
    target_value = nav / n_target
    # 可动用资金 = min(本策略虚拟可用, 账户实际可用)；
    # 既不超过本策略额度（不抢占别人的资金），也不透支共享账户（不影响别的策略）
    virtual_cash = nav - holdings_value
    acct_cash = _get_account_cash(C)
    spendable = min(virtual_cash, acct_cash)

    # 修20260819: 自动缩减仓位当资金不足以买入所有目标股常，按最小1手成本从低到高选择可买股数，只买可买的前N股
    # 10万/100股=1000元/股 → 不足买1手的股就被删除＝按价格从低到高择序，只保留能买起的前N只
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
        print("[ATR_EW 仓位缩减] 资金期%.0f只能买%d股(目标%d股)，只取前%d股"
              % (spendable, affordable_n, n_target, affordable_n))
        target_codes = affordable_codes
        n_target = max(len(target_codes), 1)
        target_value = nav / n_target
        spendable = min(virtual_cash, acct_cash)

    for code in target_codes:
        price = prices.get(code)
        if price is None or price <= 0:
            continue
        # 停牌检查（修复 20260823 P0）：连续两根无量才判停牌；
        # 单根末根无量是盘前未完成bar，放行（详见 _is_suspended_bar）
        df = _g_all_data.get(code)
        if _is_suspended_bar(df):
            continue
        held = _g_my_codes.get(code, {}).get('shares', 0)
        desired = int(target_value / price / 100) * 100
        delta = desired - held
        if delta > 0:
            # 买增量，受 spendable 约束
            if delta * price > spendable + 1:
                delta = int(spendable / price / 100) * 100
                if delta <= 0:
                    continue
            # R6修复: 下单前再查一次账户现金，防共享账户被 V2 并发占用导致资金不足被拒、
            # 乐观写 ledger 产生幻影持仓
            try:
                _acct_cash_now = _get_account_cash(C)
                if delta * price > _acct_cash_now + 1:
                    print("[ATR_EW 资金不足跳过] %s 需%.0f 账户可用%.0f" % (code, delta * price, _acct_cash_now))
                    continue
            except Exception:
                pass
            try:
                order_id = passorder(
                    23,  # 23=买入
                    1101,
                    _ACCOUNT_ID,
                    code,
                    5,   # 对手价，确保成交
                    price,
                    delta,
                    'ATR_EW',
                    2,
                    '',
                    C,
                )
                print("[ATR_EW 买入] %s %d股 @ %.3f 目标市值=%.0f 返回值:%s"
                      % (code, delta, price, target_value, order_id))
                oid, matched = _lookup_order(C, code, delta, 'buy')
                if oid:
                    print("[ATR_EW 买入确认] %s 订单%s已确认" % (code, oid))
                else:
                    print("[ATR_EW 买入反查失败] %s 登记pending" % code)
                    _g_pending_buys[code] = {'shares': delta, 'price': price, 'time': time.time()}
                if held > 0:
                    # 加仓：更新加权成本
                    info = _g_my_codes[code]
                    new_shares = held + delta
                    info['buy_price'] = (info.get('buy_price', price) * held + price * delta) / new_shares
                    info['shares'] = new_shares
                    info['peak_price'] = max(info.get('peak_price', price), price)
                else:
                    _g_my_codes[code] = {
                        'buy_price': price,
                        'buy_date': _get_qmt_time(C).strftime('%Y%m%d'),  # 修复 20260819
                        'shares': delta,
                        'peak_price': price,
                    }
                _log_trade('买入', code, price, delta, 'ATR低波等权调仓')
                spendable -= delta * price
            except Exception as e:
                print("[ATR_EW 买入失败] %s: %s" % (code, e))
        elif delta < 0:
            # 卖自己多出的量（仅本策略 ledger 记录的量）
            _partial_sell(C, code, -delta, price, '调仓缩减(等权再平衡)')


# ============================================================
# 选股引擎（低 ATR% 前 N + 全套过滤）
# ============================================================
def _batch_get_roe(codes):
    """批量取 ROE(%)，一次 IPC 调用替代逐股调用（关键性能优化）。返回 {code: roe}。"""
    global _g_roe_api_ok, _g_roe_cache
    if _g_roe_api_ok is False:
        return {}
    if not codes:
        return {}
    result = {}
    # QMT内置 Python 无 xtdata，报失败自动放弃 ROE 质量门控 (fail-open)
    try:
        import xtdata
    except Exception as e:
        print("[ATR_EW] ROE接口xtdata不可用, 自动放弃质量门控(fail-open): %s" % e)
        _g_roe_api_ok = False
        return {}
    step = 200
    for i in range(0, len(codes), step):
        batch = codes[i:i + step]
        try:
            res = xtdata.get_financial_data(['roe'], batch)
        except Exception as e:
            print("[ATR_EW] 批量ROE获取失败(批次%d): %s" % (i // step, e))
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
    """全市场 ATR 低波动筛选：返回入选代码列表（低 ATR% 前 N_HOLD）。"""
    global _g_hold_pool_cache, _g_hold_pool_cache_date, _g_all_data, _g_turnover_available

    today_str = _get_qmt_time(C).strftime('%Y%m%d')  # 修复 20260819: 优先QMT行情时间(AGENTS红线)
    if _g_hold_pool_cache is not None and _g_hold_pool_cache_date == today_str:
        print("[ATR_EW] 选股缓存命中: %d 只" % len(_g_hold_pool_cache))
        return _g_hold_pool_cache

    try:
        all_codes = C.get_stock_list_in_sector('沪深A股')
        codes = [c for c in all_codes if c.endswith('.SH') or c.endswith('.SZ')]
        print("[ATR_EW] 全市场 %d 只, 开始筛选..." % len(codes))
    except Exception as e:
        print("[ATR_EW] get_stock_list_in_sector失败: %s" % e)
        return []

    # 一次性拉取全市场日线（含动量所需的 252 根）
    try:
        data = C.get_market_data_ex(stock_code=codes, period='1d', count=_MIN_HISTORY + 10)
        if not data:
            return []
        _g_all_data = data
    except Exception as e:
        print("[ATR_EW] 行情拉取失败: %s" % e)
        return []

    # 换手率：优先从日线列取，缺失再试接口。
    # 修复 20260818：尺度自适应(_scale_turnover) + 能力探测(hasattr) + 全空 fail-open。
    # 旧版：无条件×100（百分比会被放大250倍全杀候选）；依赖抛异常置位；空数据时
    # _g_turnover_available 仍为 True + map 为空 → 每只票 to=-1 → 全部淘汰（0候选）。
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
                start = (_get_qmt_time(C) - timedelta(days=120)).strftime('%Y%m%d')  # 修复 20260819
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
        _g_turnover_available = True  # 修复 20260819: 数据可用→启用过滤(三态不可用才跳过)
        print("[ATR_EW] 换手率数据可用: %d 只 (样例: %s)" % (len(turnover_map), list(turnover_map.items())[:3]))
    else:
        _g_turnover_available = False
        print("[ATR_EW] 警告: 换手率数据不可用，本次跳过换手过滤（fail-open，不误杀）")
    # ST 名单一次性拉取（避免对每只股票单独查名称，季频全市场会触发数千次 API）
    st_set = set()
    try:
        lst = C.get_stock_list_in_sector('风险警示板')
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
        print("[ATR_EW] 警告: ST板块名单获取失败，ST过滤本次跳过（保守降级）")

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
            # 真实价上限过滤（QMT 行情即真实价，非复权）：高价股小资金买不起整手
            if _MAX_PRICE > 0 and cl >= _MAX_PRICE:
                skip_price += 1
                continue
            # ATR% 过滤（低波动）；atr_pct<=0 多为数据平填/停牌(近14日ATR=0)
            atr_pct = _calc_atr_pct(df)
            if atr_pct <= 0:
                skip_atr_flat += 1
                continue
            if atr_pct >= _ATR_THRESHOLD:
                skip_atr_high += 1
                continue
            # 换手率过滤（修复 20260818: fail-open，缺该票数据则跳过此过滤，绝不误杀）
            if _g_turnover_available is True:
                to = turnover_map.get(code)
                if to is not None and (to < _MIN_TURNOVER or to > _MAX_TURNOVER):
                    skip_turn += 1
                    continue
            # 非 ST（板块集合判断，O(1)）
            if code in st_set:
                skip_st += 1
                continue
            # 动量门控：12-1 月收益 > 0（剔除近期输家，来自已拉取日线，零额外API）
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
    # 质量门控：ROE > 0（一次性批量取，替代逐股调用，避免上千次 IPC）
    if _QUALITY_GATE and eligible:
        print("[ATR_EW] 初筛通过 %d 只，批量取ROE..." % len(eligible))
        roe_map = _batch_get_roe([c for c, _ in eligible])
        if _g_roe_api_ok is False or not roe_map:
            # R9(2026-08-15 诚哥拍板): 接口不可用 或 整批空结果 均 fail-open（防整季空仓）
            print("[ATR_EW] 警告: ROE数据不可用或空结果，本次跳过ROE门控（R9 fail-open）")
        else:
            filtered = []
            for code, atr_pct in eligible:
                roe = roe_map.get(code)
                if roe is None or roe <= 0:
                    continue
                filtered.append((code, atr_pct))
            eligible = filtered

    # MAX5 彩票效应过滤：近20日最大单日涨幅（pct_change tail(20).max，与策略侧
    # strategy/atr_lowvol.py L216-224 同口径），剔除最高 _MAX_EXCLUDE_PCT 分位（0=关）
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
        print("[ATR_EW] MAX5过滤: 剔除最高%.0f%%分位 -> 剩余%d只 (阈值%.2f)"
              % (_MAX_EXCLUDE_PCT * 100, len(eligible), thr))

    # 按 ATR% 升序取前 N_HOLD（低波动优先）
    eligible.sort(key=lambda x: x[1])
    selected = [c for c, _ in eligible[:_N_HOLD]]

    _g_hold_pool_cache = selected
    _g_hold_pool_cache_date = today_str

    print("[ATR_EW] 筛选完成: 候选%d -> 入选%d只 (高价排除%d)" % (len(eligible), len(selected), skip_price))
    print("[ATR_EW] 过滤明细: 长度<252=%d 价<=0=%d ATR平填=%d ATR>=%.1f%%=%d 换手=%d ST=%d 动量=%d 异常=%d"
          % (skip_len, skip_close, skip_atr_flat, _ATR_THRESHOLD, skip_atr_high, skip_turn, skip_st, skip_mom, skip_exc))
    for c in selected[:15]:
        name = _get_stock_name_safe(C, c)
        print("    [ATR_EW 选股] %s %s ATR%%=%.2f" % (c, name, dict(eligible).get(c, 0)))
    if len(selected) > 15:
        print("    ... 其余 %d 只省略" % (len(selected) - 15))

    return selected


# ============================================================
# 再平衡 + 间际止损
# ============================================================
def _current_prices(C, codes):
    prices = {}
    if not codes:
        return prices
    # 优先用选股时拉到的全市场快照（count 大、已验证可用），避免 count=2 实时价返回空
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
            print("[ATR_EW] 实时价二次获取失败: %s" % e)
        still = [c for c in codes if c not in prices]
        if still:
            print("[ATR_EW] 警告: %d 只取不到价格: %s" % (len(still), ",".join(still)))
    return prices
def _quarter_key(now):
    q = (now.month - 1) // 3 + 1
    return "%dQ%d" % (now.year, q)


def _rebalance_to_target(C, target_codes):
    """卖出不在目标里的持仓（停牌跳过），等权买入目标里未持仓的。"""
    prices = _current_prices(C, list(set(list(_g_my_codes.keys()) + list(target_codes))))

    held_codes = list(_g_my_codes.keys())
    to_sell = [(c, '调出(不在目标)') for c in held_codes if c not in target_codes]
    if to_sell:
        print("[ATR_EW 再平衡] 卖出 %d 只调出标的" % len(to_sell))
        _execute_sells(C, to_sell, prices)
        _save_holdings()

    # 再平衡买入（卖出释放现金后重算）
    _execute_buys_equalweight(C, target_codes, prices)
    _save_holdings()

    nav = max(sum(info.get('shares', 0) * prices.get(code, info.get('buy_price', 0))
                   for code, info in _g_my_codes.items()), _STRATEGY_CAPITAL)
    print("[ATR_EW 再平衡完成] 目标%d只 当前持仓%d只 本策略NAV=%.0f"
          % (len(target_codes), len(_g_my_codes), nav))


def _evaluate_interim_stops(C, prices):
    """间际止损：持仓回撤超过 stop_loss 则卖出。"""
    if _STOP_LOSS is None or _STOP_LOSS >= 0:
        return []
    to_sell = []
    for code, info in _g_my_codes.items():
        price = prices.get(code)
        if price is None or price <= 0:
            continue
        bp = info.get('buy_price', 0)
        if bp > 0 and (price - bp) / bp <= _STOP_LOSS:
            to_sell.append((code, '间际止损 %.1f%%' % ((price - bp) / bp * 100)))
    return to_sell


# ============================================================
# 主循环
# ============================================================
def _main_loop(C):
    global _g_last_rebalance_key, _g_cooling_until, _g_last_attempt_ts

    # R2修复: 历史K线回放守卫（对齐 src 尾盘版 is_last_bar）；回测模式全量跑，实盘仅最后一根bar交易
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

    print("[ATR_EW] %s 心跳 持仓%d只[%s]" % (
        now_str, len(_g_my_codes), ','.join(_g_my_codes.keys()) if _g_my_codes else '空'))

    # cooling-off
    if _g_cooling_until > 0 and time.time() < _g_cooling_until:
        remaining = int(_g_cooling_until - time.time())
        print("[ATR_EW] cooling-off 中(%ds)" % remaining)
        return

    _check_pending_orders(C)

    # 再平衡触发：季度键变化 = 季度首个交易日；或 空仓兜底（中途部署/建仓失败重试）
    key = _quarter_key(now)
    today_y = now.strftime('%Y%m%d') if hasattr(now, 'strftime') else datetime.now().strftime('%Y%m%d')
    is_rebalance_day = (key != _g_last_rebalance_key)
    # 修复 20260823(P1)：建仓限盘中窗口(09:33~14:55)。盘前当日bar尚未成交(volume=0)，
    # 曾致09:16触发时全部候选被停牌检查静默丢弃并假成功。间际止损不受窗口限制（风控优先）。
    hhmm_int = int(now_str) if now_str.isdigit() else 0
    in_trade_window = (933 <= hhmm_int <= 1455)
    # 空仓兜底：尚未建仓时按最小间隔重试（不卡季频首日，防中途部署错过Q3首日永久空仓）。
    # 修复 20260823(P0)：由"每天一次"改为"间隔限频"，单次失败不再烧毁全天配额。
    force_retry = (len(_g_my_codes) == 0) and (
        _g_last_attempt_ts == 0.0 or (time.time() - _g_last_attempt_ts) >= _RETRY_MIN_INTERVAL)

    # 全局限流（修复 20260823 第二批）：距上次尝试不足间隔则整体不触发。
    # 必须同时盖住 is_rebalance_day（键未锁时每bar恒真，否则死循环复发）与 force_retry。
    throttled = (_g_last_attempt_ts != 0.0) and (
        time.time() - _g_last_attempt_ts) < _RETRY_MIN_INTERVAL

    if (is_rebalance_day or force_retry) and in_trade_window and not throttled:
        print("[ATR_EW] 再平衡触发(%s, 季频=%s, 空仓兜底=%s)" % (key, is_rebalance_day, force_retry))
        selected = _run_screening(C)
        if selected:
            _rebalance_to_target(C, selected)
            # 修复 20260823(P0)：真实建仓成功（持仓非空，含pending登记）才锁季度键；
            # 原写法 if selected: 恒真——盘前假成功把整季锁死不再重试。
            if len(_g_my_codes) > 0:
                _g_last_rebalance_key = key
            else:
                print("[ATR_EW] 建仓未成功(持仓仍空)，%d秒后重试" % _RETRY_MIN_INTERVAL)
        else:
            print("[ATR_EW] 选股池无候选，跳过本次再平衡（不刷新季度键，留待重试）")
        _g_last_attempt_ts = time.time()
    else:
        # 非再平衡日：仅做间际止损（轻量，只取持仓价）
        if _STOP_LOSS is not None and _STOP_LOSS < 0:
            prices = _current_prices(C, list(_g_my_codes.keys()))
            to_sell = _evaluate_interim_stops(C, prices)
            if to_sell:
                print("[ATR_EW 间际止损] %d只需卖出" % len(to_sell))
                _execute_sells(C, to_sell, prices)
                _save_holdings()
            else:
                print("[ATR_EW] 间际止损评估: 无")


# ============================================================
# QMT 生命周期
# ============================================================
class StrategyRunner(object):
    def __init__(self):
        self.initialized = False

    def init(self, C):
        global _g_initialized, _g_cooling_until, _g_last_rebalance_key
        print("[ATR_EW] =============================================")
        print("[ATR_EW] ATR低波动-等权不杠杆 初始化... build=%s" % BUILD_TAG)
        print("[ATR_EW] =============================================")

        _load_config()
        _load_holdings()
        _reconcile_own_holdings(C)

        print("[ATR_EW] 初始化完成 账号=%s" % _ACCOUNT_ID)
        print("[ATR_EW] 本金=%d N_HOLD=%d 季频=%s ROE门控=%d 动量门控=%d max_price=%.0f MAX5剔除=%.0f%% 止损=%.2f"
              % (_STRATEGY_CAPITAL, _N_HOLD, _REBALANCE_FREQ, _QUALITY_GATE,
                 _MOMENTUM_GATE, _MAX_PRICE, _MAX_EXCLUDE_PCT * 100, _STOP_LOSS))

        try:
            if C.do_back_test or C.do_backtest:
                _g_cooling_until = 0
                print("[ATR_EW] 回测模式，跳过 cooling-off")
            else:
                _g_cooling_until = time.time() + 60
                print("[ATR_EW] 启动 cooling-off 中(60s)")
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
            print("[ATR_EW 异常] %s" % e)

    def exit(self, C):
        _save_holdings()
        print("[ATR_EW] 策略退出，持仓已保存")


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
            print("[停止] 手动中断策略，正常退出...")
            raise


def exit(C):
    runner = getattr(C, 'runner', None)
    if runner is not None:
        runner.exit(C)
