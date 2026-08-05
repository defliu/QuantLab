# coding=gbk
"""
ATR低波动策略 — 全QMT闭环，零外部依赖
选股: ATR%%<6%% + 换手1-8%% + 成交额排序 top N
退出: 止损-8%% / 止盈+20%% / 移动止盈-10%% / 条件失效退出
"""
import json
import os
import time
import math
from datetime import datetime, timedelta

# ============================================================
# 配置（内置默认值，可被 config/atr_lowvol_config.yaml 覆盖）
# ============================================================
CONFIG = {
    'strategy': {
        'name': 'ATR_LOWVOL',
        'display_name': 'ATR低波动策略',
        'capital_base': 100000,
        'max_hold': 3,
        'target_ratio': 0.30,
    },
    'screening': {
        'atr_threshold': 6.0,
        'min_turnover': 1.0,
        'max_turnover': 8.0,
        'min_bars': 60,
    },
    'sell': {
        'stop_loss_pct': -0.08,
        'take_profit_pct': 0.20,
        'trailing_stop_pct': -0.10,
        'condition_exit': True,
    },
    'account': {
        'id': '67014907',
    },
    'pool': {
        'holdings_file': 'D:/QMT_POOL/atr_holdings.json',
        'nav_file': 'D:/QMT_POOL/atr_nav.json',
        'trade_log_file': 'D:/QMT_POOL/atr_trade_log.csv',
    },
}

# ============================================================
# 全局状态
# ============================================================
_g_my_codes = {}          # code -> {buy_price, buy_date, shares, peak_price, ...}
_g_cumulative_pnl = 0.0
_g_nav_history = []       # [(date, nav), ...]
_g_hold_pool_cache = None
_g_hold_pool_cache_date = ''
_g_all_data = {}
_g_turnover_cache = {}   # code -> 最新换手率（百分比，如 2.5 = 2.5%）
_g_initialized = False
_g_cooling_until = 0.0
_STRATEGY_CAPITAL = 100000
_MAX_HOLD = 3
_TARGET_RATIO = 0.30
_MAX_TOTAL_RATIO = 0.90
_ATR_THRESHOLD = 6.0
_MIN_TURNOVER = 1.0
_MAX_TURNOVER = 8.0
_MIN_BARS = 60
_STOP_LOSS = -0.08
_TAKE_PROFIT = 0.20
_TRAILING_STOP = -0.10
_ENABLE_CONDITION_EXIT = True
_ACCOUNT_ID = '67014907'
_DEBUG_MODE = False
_TEST_MODE = False
_g_last_trade_date = ''
_g_sell_cooldown = {}      # code -> time.time() of last sell attempt
_g_pending_sells = {}      # code -> {shares, price, reason, time}
_g_pending_buys = {}       # code -> {shares, price, time}
_LOOKUP_RETRIES = 4
_LOOKUP_INTERVAL = 0.2     # 单次轮询间隔（4次×0.2s=0.8s覆盖QMT ~100ms异步延迟）

_VERSION = 'ATR_FIX_v4_20260802'   # WorkBuddy 修复版指纹：换手率fail-open + 去get_trade_detail_data依赖 + is_last_bar守卫 + 扫描节流
_g_has_trade_query = None          # 探测：context 是否支持 get_trade_detail_data（国金部分版本无此方法）
_g_last_scan_ts = 0.0              # 上次全市场扫描时间戳（节流用）
_SCAN_MIN_INTERVAL = 55            # 全市场数据最小刷新间隔(秒)，防止同一分钟内反复拉5000+只日线
_g_real_bars = 0                   # 实时bar计数器（守卫通过的bar数，用于远程日志确认守卫生效）
_g_replay_done = False             # 历史回放是否结束（首根实时bar到达后置True，只打印一次标记）


# ============================================================
# 辅助函数
# ============================================================
def _load_config():
    """加载YAML配置（简易解析，不依赖pyyaml）"""
    global _STRATEGY_CAPITAL, _MAX_HOLD, _TARGET_RATIO, _MAX_TOTAL_RATIO
    global _ATR_THRESHOLD, _MIN_TURNOVER, _MAX_TURNOVER, _MIN_BARS
    global _STOP_LOSS, _TAKE_PROFIT, _TRAILING_STOP, _ENABLE_CONDITION_EXIT
    global _ACCOUNT_ID, _DEBUG_MODE, _TEST_MODE

    config_path = 'D:/QMT_STRATEGIES/config/atr_lowvol_config.yaml'
    if not os.path.exists(config_path):
        print("  [ATR] 无配置文件，使用内置默认值")
        return

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        section = None
        sub_section = None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.endswith(':') and not stripped.startswith('-'):
                key = stripped.rstrip(':')
                if key in ('strategy', 'screening', 'sell', 'account', 'pool'):
                    section = key
                    sub_section = None
                elif section:
                    sub_section = key
                continue
            if ':' in stripped and not stripped.startswith('-'):
                parts = stripped.split(':', 1)
                k = parts[0].strip()
                v = parts[1].strip() if len(parts) > 1 else ''
                # 去掉行内注释（# 后面的内容）
                if '#' in v:
                    v = v[:v.index('#')].strip()
                if section == 'strategy':
                    if k == 'capital_base':
                        _STRATEGY_CAPITAL = int(v)
                    elif k == 'max_hold':
                        _MAX_HOLD = int(v)
                    elif k == 'target_ratio':
                        _TARGET_RATIO = float(v)
                    elif k == 'max_total_ratio':
                        _MAX_TOTAL_RATIO = float(v)
                elif section == 'screening':
                    if k == 'atr_threshold':
                        _ATR_THRESHOLD = float(v)
                    elif k == 'min_turnover':
                        _MIN_TURNOVER = float(v)
                    elif k == 'max_turnover':
                        _MAX_TURNOVER = float(v)
                    elif k == 'min_bars':
                        _MIN_BARS = int(v)
                elif section == 'sell':
                    if k == 'stop_loss_pct':
                        _STOP_LOSS = float(v)
                    elif k == 'take_profit_pct':
                        _TAKE_PROFIT = float(v)
                    elif k == 'trailing_stop_pct':
                        _TRAILING_STOP = float(v)
                    elif k == 'condition_exit':
                        _ENABLE_CONDITION_EXIT = v.lower() == 'true' if v else True
                elif section == 'account' and k == 'id':
                    _ACCOUNT_ID = str(v).strip("'\"")
        print("  [ATR] 配置加载完成")
    except Exception as e:
        print("  [ATR] 配置加载失败: %s, 使用默认值" % e)


def _get_qmt_time(C):
    """获取QMT行情时间"""
    try:
        return C.get_current_time()
    except Exception:
        return datetime.now()


def _get_stock_name_safe(C, code):
    """安全获取股票名称"""
    try:
        info = C.get_stock_basic_info(code)
        if info is not None:
            return info.get('name', code)
    except Exception:
        pass
    return code


def _is_today_data(df):
    """检查最后一行是否是今天的数据"""
    if df is None or len(df) < 1:
        return False
    try:
        last_date = str(df.index[-1])
        today = datetime.now().strftime('%Y-%m-%d')
        return today in last_date
    except Exception:
        return False


def _calc_atr_pct(df):
    """计算 ATR(14)/close * 100"""
    if df is None or len(df) < 15:
        return 999.0
    try:
        h = df['high']
        l = df['low']
        c = df['close']
        tr1 = (h - l).values
        tr2 = (h - c.shift(1)).abs().values
        tr3 = (l - c.shift(1)).abs().values
        tr = [max(tr1[i], tr2[i], tr3[i]) if not math.isnan(tr2[i]) else tr1[i] for i in range(len(tr1))]
        atr = sum(tr[-14:]) / 14.0
        close_price = float(c.iloc[-1])
        if close_price <= 0:
            return 999.0
        return atr / close_price * 100.0
    except Exception:
        return 999.0


def _load_holdings():
    """从文件加载持仓状态"""
    global _g_my_codes, _g_cumulative_pnl, _g_nav_history
    fpath = CONFIG['pool']['holdings_file']
    if os.path.exists(fpath):
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _g_my_codes = data.get('holdings', {})
            _g_cumulative_pnl = data.get('cumulative_pnl', 0.0)
            _g_nav_history = data.get('nav_history', [])
            print("  [ATR] 加载持仓 %d 只, 累计盈亏 %.2f" % (len(_g_my_codes), _g_cumulative_pnl))
        except Exception as e:
            print("  [ATR] 持仓加载失败: %s" % e)
            _g_my_codes = {}
            _g_cumulative_pnl = 0.0
            _g_nav_history = []
    else:
        _g_my_codes = {}
        _g_cumulative_pnl = 0.0
        _g_nav_history = []


def _save_holdings():
    """保存持仓状态到文件"""
    fpath = CONFIG['pool']['holdings_file']
    try:
        data = {
            'holdings': _g_my_codes,
            'cumulative_pnl': _g_cumulative_pnl,
            'nav_history': _g_nav_history[-500:],  # 保留最近500条
            'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("  [ATR] 持仓保存失败: %s" % e)


def _log_trade(trade_type, code, price, shares, reason):
    """记录成交到CSV"""
    fpath = CONFIG['pool']['trade_log_file']
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = "%s,%s,%s,%.3f,%d,%s\n" % (now, trade_type, code, price, shares, reason)
        with open(fpath, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass


def _sync_account_holdings(C):
    """从QMT账户同步持仓到 _g_my_codes（解决孤儿持仓问题）"""
    # 修复2b：部分国金QMT的 context 无 get_trade_detail_data，此处显式降级而非抛异常
    if not hasattr(C, 'get_trade_detail_data'):
        print("  [ATR纳管] 本环境无 get_trade_detail_data API，跳过账户持仓反查")
        print("  [ATR纳管]   降级策略：以本地持仓记录文件为准（孤儿持仓需人工核对）")
        return
    try:
        account_info = C.get_trade_detail_data(_ACCOUNT_ID, 'stock', 'account')
        if not account_info:
            return
        # 获取账户持仓
        positions = C.get_trade_detail_data(_ACCOUNT_ID, 'stock', 'position')
        if not positions:
            return
        account_codes = set()
        for pos in positions:
            code = str(pos.m_strCode if hasattr(pos, 'm_strCode') else pos.get('code', ''))
            if not code:
                continue
            if not code.endswith('.SH') and not code.endswith('.SZ'):
                continue
            account_codes.add(code)
            if code not in _g_my_codes:
                # 账户有但策略无记录 — 孤儿持仓纳管
                volume = int(pos.m_nCanUseVolume if hasattr(pos, 'm_nCanUseVolume') else pos.get('can_use_volume', 0))
                cost = float(pos.m_dOpenPrice if hasattr(pos, 'm_dOpenPrice') else pos.get('open_price', 0))
                if volume > 0:
                    _g_my_codes[code] = {
                        'buy_price': cost,
                        'buy_date': datetime.now().strftime('%Y%m%d'),
                        'shares': volume,
                        'peak_price': cost,
                        'orphan': True,
                    }
                    print("  [ATR纳管] %s 孤儿持仓 %d 股, 成本 %.3f" % (code, volume, cost))
        # 移除账户已不存在的持仓
        to_remove = [c for c in _g_my_codes if c not in account_codes and _g_my_codes[c].get('orphan', False)]
        for c in to_remove:
            print("  [ATR清理] %s 账户已无此持仓, 移除记录" % c)
            del _g_my_codes[c]
    except Exception as e:
        print("  [ATR纳管] 异常: %s" % e)


# ============================================================
# 换手率获取（fail-open，兼容多种返回结构）
# ============================================================
_g_turnover_method_checked = False
_g_turnover_method_available = False


def _scale_turnover(raw):
    """换手率尺度自适应：返回值<1视为小数(×100)，>=1视为已是百分比。"""
    if raw is None:
        return None
    if raw < 1.0:
        return raw * 100.0
    return raw


def _extract_turnover_value(code, turnover_data):
    """从 get_turnover_rate 返回里提取单只股票最新换手率（百分比，如 2.5=2.5%）。

    返回 float（百分比）或 None（取不到/结构不兼容/无需过滤）。
    fail-open 原则：任何时候取不到都返回 None（放行），绝不让换手率把股票误杀。
    兼容列名多种格式（带/不带 .SH/.SZ 后缀），兼容三种返回结构：
      形态1  DataFrame，列为股票代码        turnover_data[code]
      形态2  DataFrame，index 为股票代码    turnover_data.loc[code]
      形态3  dict {code: 值}
    数值尺度自适应（见 _scale_turnover）。
    """
    if turnover_data is None:
        return None
    candidates = [code]
    if code.endswith('.SH'):
        candidates.append(code[:-3])
        candidates.append('SH' + code[:-3])
    elif code.endswith('.SZ'):
        candidates.append(code[:-3])
        candidates.append('SZ' + code[:-3])
    try:
        if hasattr(turnover_data, 'columns'):
            for key in candidates:
                if key in turnover_data.columns:
                    series = turnover_data[key].dropna()
                    if len(series) > 0:
                        return _scale_turnover(float(series.iloc[-1]))
        if hasattr(turnover_data, 'index') and hasattr(turnover_data, 'loc'):
            for key in candidates:
                if key in turnover_data.index:
                    series = turnover_data.loc[key].dropna()
                    if len(series) > 0:
                        return _scale_turnover(float(series.iloc[-1]))
        if isinstance(turnover_data, dict):
            for key in candidates:
                if key in turnover_data:
                    v = turnover_data[key]
                    if v is not None:
                        return _scale_turnover(float(v))
    except Exception:
        return None
    return None


def _try_fetch_turnover(C, codes, start, end):
    """调用 get_turnover_rate；国金QMT官方API真实存在（返回DataFrame）。

    返回 DataFrame 或 None。任何异常（含方法不存在）都返回 None（fail-open）。
    首次调用探测方法可用性，避免每批次重复抛 AttributeError。
    """
    global _g_turnover_method_checked, _g_turnover_method_available
    if not _g_turnover_method_checked:
        _g_turnover_method_available = hasattr(C, 'get_turnover_rate')
        _g_turnover_method_checked = True
    if not _g_turnover_method_available:
        return None
    try:
        return C.get_turnover_rate(codes, start, end)
    except Exception:
        return None


# ============================================================
# 选股引擎
# ============================================================
def _run_screening(C):
    """全市场ATR低波动筛选: ATR%%<6%% + 换手1-8%% + 成交额排序"""
    global _g_hold_pool_cache, _g_hold_pool_cache_date

    today_str = datetime.now().strftime('%Y%m%d')
    # 缓存命中检查
    if _g_hold_pool_cache is not None and _g_hold_pool_cache_date == today_str:
        print("  [ATR] 缓存命中: %d 只" % len(_g_hold_pool_cache))
        return _g_hold_pool_cache

    # 获取全市场股票
    try:
        all_codes = C.get_stock_list_in_sector('沪深A股')
        codes = [c for c in all_codes if c.endswith('.SH') or c.endswith('.SZ')]
        print("  [ATR] 全市场 %d 只, 开始筛选..." % len(codes))
    except Exception as e:
        print("  [ATR] get_stock_list_in_sector失败: %s" % e)
        return []

    # 换手率查询的日期范围（取近90天覆盖 _MIN_BARS=60个交易日）
    _turnover_start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
    _turnover_end = today_str

    result = []
    stats = {'skip_data': 0, 'skip_atr': 0, 'skip_turnover': 0, 'turnover_unknown': 0}
    BATCH = 200
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        try:
            data = C.get_market_data_ex(stock_code=batch, period='1d', count=_MIN_BARS)
            if not data:
                continue
            # 批量获取换手率（国金QMT官方API真实存在，返回DataFrame；
            # 因返回结构/列名格式可能与预期不同，采用 fail-open：取不到该票
            # 换手率就放行，绝不整只丢弃，避免把全部候选杀光）
            turnover_data = _try_fetch_turnover(C, batch, _turnover_start, _turnover_end)
            for code, df in data.items():
                if df is None or len(df) < _MIN_BARS:
                    stats['skip_data'] += 1
                    continue
                try:
                    c = df['close']
                    cl = float(c.iloc[-1])
                    if cl <= 0:
                        continue

                    # ATR% < 阈值
                    atr_pct = _calc_atr_pct(df)
                    if atr_pct >= _ATR_THRESHOLD:
                        stats['skip_atr'] += 1
                        continue

                    # 换手率 1-8%（fail-open：取不到该票值则跳过此过滤，仅按ATR+成交额选股）
                    turnover_rate = _extract_turnover_value(code, turnover_data)
                    if turnover_rate is not None:
                        if turnover_rate < _MIN_TURNOVER or turnover_rate > _MAX_TURNOVER:
                            stats['skip_turnover'] += 1
                            continue
                    else:
                        stats['turnover_unknown'] += 1

                    # 5日成交额合计
                    amt = float(df['amount'].tail(5).sum()) if 'amount' in df.columns else 0

                    result.append({
                        'code': code,
                        'signal': 'ATR低波动',
                        'buy_type': 'atr_lowvol',
                        'amount': amt,
                        'atr_pct': atr_pct,
                        'turnover': turnover_rate if turnover_rate is not None else 0.0,
                    })
                except Exception:
                    continue
        except Exception as e:
            print("  [ATR] 批次 %d 异常: %s" % (i, e))
            continue
        if (i // BATCH) % 3 == 0:
            print("  [ATR] 进度: %d/%d (通过%d)" % (min(i + BATCH, len(codes)), len(codes), len(result)))

    # 排除所有账户已有持仓（包括其他策略的票）
    held = set(_g_my_codes.keys()) if isinstance(_g_my_codes, dict) else set()
    try:
        positions = C.get_trade_detail_data(_ACCOUNT_ID, 'stock', 'position')
        if positions:
            for pos in positions:
                try:
                    p_code = str(pos.m_strCode if hasattr(pos, 'm_strCode') else '')
                    p_vol = int(pos.m_nCanUseVolume if hasattr(pos, 'm_nCanUseVolume') else 0)
                    if p_code and p_vol > 0:
                        held.add(p_code)
                except Exception:
                    continue
    except Exception:
        pass
    if len(held) > len(_g_my_codes):
        print("  [ATR] 排除其他策略持仓: %d只" % (len(held) - len(_g_my_codes)))
    result = [r for r in result if r['code'] not in held]

    # 按成交额排序取前 MAX_HOLD
    result.sort(key=lambda r: r.get('amount', 0), reverse=True)
    result = result[:_MAX_HOLD]

    _g_hold_pool_cache = result
    _g_hold_pool_cache_date = today_str

    print("  [ATR] 筛选完成: %d 只通过 (数据不足%d, ATR过高%d, 换手越界%d, 换手未知%d)" % (
        len(result), stats['skip_data'], stats['skip_atr'], stats['skip_turnover'], stats['turnover_unknown']))
    for r in result:
        name = _get_stock_name_safe(C, r['code'])
        amt_yi = r.get('amount', 0) / 1e8
        print("    [ATR选股] %s %s  ATR%%=%.2f 换手=%.2f%%  5日成交额=%.2f亿" % (
            r['code'], name, r.get('atr_pct', 0), r.get('turnover', 0), amt_yi))

    return result


# ============================================================
# 卖出引擎
# ============================================================
def _evaluate_sells(C, current_prices):
    """评估卖出条件，返回需要卖出的列表 [(code, reason), ...]"""
    to_sell = []
    now = time.time()

    for code, info in list(_g_my_codes.items()):
        price = current_prices.get(code)
        if price is None or price <= 0:
            continue

        buy_price = info.get('buy_price', price)
        shares = info.get('shares', 0)
        if shares <= 0:
            continue

        pnl_pct = (price - buy_price) / buy_price if buy_price > 0 else 0

        # 更新最高价
        old_peak = info.get('peak_price', buy_price)
        if price > old_peak:
            info['peak_price'] = price
            old_peak = price

        # 1) 止损
        if pnl_pct <= _STOP_LOSS:
            to_sell.append((code, '止损 %.1f%%' % (pnl_pct * 100)))
            continue

        # 2) 止盈
        if pnl_pct >= _TAKE_PROFIT:
            to_sell.append((code, '止盈 %.1f%%' % (pnl_pct * 100)))
            continue

        # 3) 移动止盈（从最高点回落）
        if old_peak > buy_price:
            drawdown = (price - old_peak) / old_peak
            if drawdown <= _TRAILING_STOP:
                to_sell.append((code, '移动止盈 回落 %.1f%%' % (drawdown * 100)))
                continue

        # 4) 条件失效退出
        if _ENABLE_CONDITION_EXIT:
            df = _g_all_data.get(code)
            if df is not None and len(df) >= _MIN_BARS:
                atr_pct = _calc_atr_pct(df)
                # 换手率条件：仅当换手率API有数据时检查
                turnover = _g_turnover_cache.get(code, -1)
                turnover_failed = False
                if turnover >= 0:
                    turnover_failed = (turnover < _MIN_TURNOVER or turnover > _MAX_TURNOVER)
                if atr_pct >= _ATR_THRESHOLD or turnover_failed:
                    to_sell.append((code, '条件失效 ATR%%=%.2f 换手=%.2f%%' % (atr_pct, turnover if turnover >= 0 else 0)))
                    continue

    return to_sell


def _lookup_order(C, code, volume, direction, retries=None, interval=None):
    """反查订单确认。
    passorder()返回0/None不代表下单失败，必须反查get_trade_detail_data('order')。
    匹配条件: code + volume(±10%) + direction(中文"买入"/"卖出")，remark不硬卡。
    返回: (order_id, matched_order) 或 (None, None)
    """
    if retries is None:
        retries = _LOOKUP_RETRIES
    if interval is None:
        interval = _LOOKUP_INTERVAL
    dir_cn = '买入' if direction == 'buy' else '卖出'

    # 修复2: 本环境若无 get_trade_detail_data，无法反查 -> 返回 'OPTIMISTIC' 让调用方乐观确认（不进 pending 回滚）
    if _g_has_trade_query is False:
        return ('OPTIMISTIC', None)

    for retry in range(retries):
        time.sleep(interval)
        try:
            deals = C.get_trade_detail_data(_ACCOUNT_ID, 'stock', 'order')
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

                # code匹配
                if d_code != code:
                    continue
                # direction匹配（中文"买入"/"卖出"）
                if dir_cn not in d_opt:
                    continue
                # volume匹配（±10%容差，-1=全部卖出时不卡volume）
                if volume > 0 and d_vol > 0:
                    vol_diff = abs(d_vol - volume) / max(d_vol, volume)
                    if vol_diff > 0.10:
                        continue
                # status: 54/55/57=已撤/已废/已拒，排除
                if d_status in (54, 55, 57):
                    continue
                candidates.append((oid, d))
            if candidates:
                # 多候选：按时间排序取最新
                candidates.sort(key=lambda x: getattr(x[1], 'm_strInsertTime', ''), reverse=True)
                return candidates[0][0], candidates[0][1]
        except Exception:
            continue
    return None, None


def _check_pending_orders(C):
    """检查pending卖单/买单（上次反查失败的订单）"""
    global _g_my_codes, _g_cumulative_pnl
    # 修复2: 无反查能力时不会登记pending（_lookup_order返回OPTIMISTIC走确认分支），直接跳过避免误回滚
    if _g_has_trade_query is False:
        return
    now = time.time()
    for code in list(_g_pending_sells.keys()):
        pending = _g_pending_sells[code]
        if now - pending['time'] > 30:
            print("  [ATR pending超时] %s 卖出未确认超时, 放弃" % code)
            del _g_pending_sells[code]
            continue
        oid, matched = _lookup_order(C, code, pending['shares'], 'sell')
        if oid:
            print("  [ATR pending确认] %s 卖出订单%s已确认" % (code, oid))
            pnl = (pending['price'] - pending.get('buy_price', pending['price'])) * pending['shares']
            _g_cumulative_pnl += pnl
            _log_trade('卖出(迟确认)', code, pending['price'], pending['shares'], pending.get('reason', ''))
            del _g_pending_sells[code]
    for code in list(_g_pending_buys.keys()):
        pending = _g_pending_buys[code]
        if now - pending['time'] > 30:
            print("  [ATR pending超时] %s 买入未确认超时, 回滚持仓" % code)
            if code in _g_my_codes:
                del _g_my_codes[code]
            del _g_pending_buys[code]
            continue
        oid, matched = _lookup_order(C, code, pending['shares'], 'buy')
        if oid:
            print("  [ATR pending确认] %s 买入订单%s已确认" % (code, oid))
            del _g_pending_buys[code]


def _execute_sells(C, to_sell, current_prices):
    """执行卖出，带反查确认 + pending兜底"""
    for code, reason in to_sell:
        info = _g_my_codes.get(code)
        if info is None:
            continue
        shares = info.get('shares', 0)
        price = current_prices.get(code, 0)
        if shares <= 0 or price <= 0:
            continue

        # 检查冷却
        now = time.time()
        if code in _g_sell_cooldown and now - _g_sell_cooldown[code] < 60:
            print("  [ATR冷却] %s 卖出冷却中, 跳过" % code)
            continue

        # 调用QMT卖出
        try:
            order_id = C.passorder(
                24,  # 24=卖出
                1101 if price >= 1.0 else 1102,  # 1101=限价 1102=市价
                _ACCOUNT_ID,
                code,
                5,   # 卖5价类型
                -1,  # -1=全部
                price,
                code,
                1,   # 1=普通订单
            )
            print("  [ATR卖出] %s %d股 @ %.3f  原因:%s  订单返回值:%s" % (code, shares, price, reason, order_id))

            # 反查确认（不依赖passorder返回值，用code+volume+direction匹配）
            oid, matched = _lookup_order(C, code, shares, 'sell')
            if oid:
                if oid == 'OPTIMISTIC':
                    print("  [ATR卖出-乐观确认] %s 本环境无成交反查, 按passorder提交成功删除持仓(修复2)" % code)
                else:
                    print("  [ATR卖出确认] %s 订单%s已确认" % (code, oid))
                pnl = (price - info.get('buy_price', price)) * shares
                _g_cumulative_pnl += pnl
                _log_trade('卖出', code, price, shares, reason)
                del _g_my_codes[code]
            else:
                print("  [ATR卖出反查失败] %s 登记pending等待后续确认" % code)
                _g_pending_sells[code] = {
                    'shares': shares,
                    'price': price,
                    'reason': reason,
                    'time': time.time(),
                }
                del _g_my_codes[code]
            _g_sell_cooldown[code] = time.time()

        except Exception as e:
            print("  [ATR卖出失败] %s: %s" % (code, e))
            _g_sell_cooldown[code] = time.time()


def _execute_buys(C, candidates):
    """执行买入"""
    if not candidates:
        return

    # 计算可用资金
    total_asset = _STRATEGY_CAPITAL + _g_cumulative_pnl
    holdings_value = sum(
        info.get('shares', 0) * info.get('buy_price', 0)
        for info in _g_my_codes.values()
    )
    current_count = len(_g_my_codes)
    available_slots = _MAX_HOLD - current_count

    if available_slots <= 0:
        print("  [ATR买入] 无可用仓位")
        return

    # 总仓位上限检查
    max_position_value = total_asset * _MAX_TOTAL_RATIO
    remaining_headroom = max_position_value - holdings_value
    if remaining_headroom <= 0:
        print("  [ATR买入] 总仓位已达上限(%.0f/%.0f)" % (holdings_value, max_position_value))
        return

    # 每个空位固定按总资产的 target_ratio 分配
    per_slot_budget = total_asset * _TARGET_RATIO
    # 不超过剩余可用头寸
    per_slot_budget = min(per_slot_budget, remaining_headroom)
    # 不超过剩余现金
    cash = total_asset - holdings_value
    per_slot_budget = min(per_slot_budget, cash)
    if per_slot_budget <= 0:
        print("  [ATR买入] 预算不足")
        return

    for cand in candidates[:available_slots]:
        code = cand['code']
        if code in _g_my_codes:
            continue

        price = None
        try:
            data = C.get_market_data_ex(stock_code=[code], period='1d', count=1)
            if data and code in data and data[code] is not None:
                price = float(data[code]['close'].iloc[-1])
        except Exception:
            pass

        if price is None or price <= 0:
            continue

        # 计算可买股数（100的整数倍）
        max_shares = int(per_slot_budget / price / 100) * 100
        if max_shares < 100:
            print("  [ATR买入] %s 资金不足100股 (%.2f/%.2f)" % (code, per_slot_budget, price))
            continue

        # 调用QMT买入
        try:
            order_id = C.passorder(
                23,  # 23=买入
                1101,  # 限价
                _ACCOUNT_ID,
                code,
                5,   # 卖5价类型（实际是买1）
                max_shares,
                price,
                code,
                1,
            )
            print("  [ATR买入] %s %d股 @ %.3f  预算=%.2f  订单返回值:%s" % (code, max_shares, price, per_slot_budget, order_id))

            # 反查确认（不依赖passorder返回值）
            oid, matched = _lookup_order(C, code, max_shares, 'buy')
            if oid:
                if oid == 'OPTIMISTIC':
                    print("  [ATR买入-乐观确认] %s 本环境无成交反查, 按passorder提交成功记录(修复2)" % code)
                else:
                    print("  [ATR买入确认] %s 订单%s已确认" % (code, oid))
            else:
                print("  [ATR买入反查失败] %s 登记pending等待后续确认" % code)
                _g_pending_buys[code] = {
                    'shares': max_shares,
                    'price': price,
                    'time': time.time(),
                }

            # 更新持仓（即使反查失败也乐观记录，pending确认失败再回滚）
            _g_my_codes[code] = {
                'buy_price': price,
                'buy_date': datetime.now().strftime('%Y%m%d'),
                'shares': max_shares,
                'peak_price': price,
            }
            _log_trade('买入', code, price, max_shares, 'ATR低波动选股')

        except Exception as e:
            print("  [ATR买入失败] %s: %s" % (code, e))


# ============================================================
# 主循环
# ============================================================
def _main_loop(C):
    """策略主循环 - 每个handlebar调用"""
    global _g_all_data

    now = _get_qmt_time(C)
    now_str = now.strftime('%H%M') if hasattr(now, 'strftime') else str(now)
    today_str = now.strftime('%Y%m%d') if hasattr(now, 'strftime') else datetime.now().strftime('%Y%m%d')

    # 只在工作日交易（回测模式跳过：C.get_current_time()返回墙钟时间，不反映K线日期）
    weekday = now.weekday() if hasattr(now, 'weekday') else datetime.now().weekday()
    _skip_weekday_check = False
    try:
        if C.do_back_test or C.do_backtest:
            _skip_weekday_check = True
    except Exception:
        pass
    if not _skip_weekday_check and weekday >= 5:
        return

    print("  [ATR][v3] %s 心跳 持仓%d只[%s] 实时bar=%d" % (
        now_str, len(_g_my_codes), ','.join(_g_my_codes.keys()) if _g_my_codes else '空', _g_real_bars))

    # 检查冷却（回测模式 time.time() 走真实时间而K线快速回放，跳过）
    global _g_cooling_until
    if _g_cooling_until <= 0:
        cooling_active = False
    else:
        cooling_active = time.time() < _g_cooling_until
    if cooling_active:
        remaining = int(_g_cooling_until - time.time())
        print("  [ATR] cooling-off 中（%ds 剩余）" % remaining)
        return

    # 处理pending卖出（反查之前反查失败的卖单）
    _check_pending_orders(C)

    # 刷新全市场数据（修复4：节流，避免同一分钟内多次回调重复拉取5000+只日线）
    global _g_last_scan_ts
    _now_ts = time.time()
    _need_scan = (not _g_all_data) or (_now_ts - _g_last_scan_ts) >= _SCAN_MIN_INTERVAL
    try:
        if _need_scan:
            all_codes = C.get_stock_list_in_sector('沪深A股')
            codes = [c for c in all_codes if c.endswith('.SH') or c.endswith('.SZ')]
            data = C.get_market_data_ex(stock_code=codes, period='1d', count=_MIN_BARS)
            if data:
                _g_all_data = data
            _g_last_scan_ts = _now_ts
        # 同步刷新持仓换手率缓存（供卖出条件评估使用，API不可用时跳过）
        if _g_my_codes:
            held_codes = list(_g_my_codes.keys())
            _turnover_start = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
            _turnover_end = datetime.now().strftime('%Y%m%d')
            try:
                held_turnover_df = C.get_turnover_rate(held_codes, _turnover_start, _turnover_end)
                if held_turnover_df is not None:
                    for code in held_codes:
                        # fail-open：取不到或结构不兼容 → -1（数据不可用，条件退出跳过
                        # 换手检查，绝不误杀持仓）；只有确有越界值才判定 turnover_failed
                        tv = _extract_turnover_value(code, held_turnover_df)
                        _g_turnover_cache[code] = tv if tv is not None else -1
            except AttributeError:
                # get_turnover_rate 不存在（个别QMT版本），设置 -1 跳过换手率检查
                for code in held_codes:
                    _g_turnover_cache[code] = -1
            except Exception:
                pass
    except Exception as e:
        print("  [ATR] 数据加载异常: %s" % e)

    # 获取当前价格
    current_prices = {}
    for code in _g_my_codes:
        df = _g_all_data.get(code)
        if df is not None and len(df) > 0:
            current_prices[code] = float(df['close'].iloc[-1])

    # 卖出评估（每次心跳都执行）
    to_sell = _evaluate_sells(C, current_prices)
    if to_sell:
        print("  [ATR卖出评估] %d只需卖出:" % len(to_sell))
        for code, reason in to_sell:
            print("    %s 原因:%s" % (code, reason))
        _execute_sells(C, to_sell, current_prices)
        _save_holdings()
    else:
        print("  [ATR卖出评估] 无卖出信号")

    # 买入窗口判断（14:50-15:00 尾盘买入）
    is_buy_window = '1450' <= now_str <= '1500'
    if _DEBUG_MODE:
        is_buy_window = True  # 调试模式全天可买

    if is_buy_window:
        print("  [ATR] 买入窗口 %s, 运行选股..." % now_str)
        candidates = _run_screening(C)
        if candidates:
            _execute_buys(C, candidates)
            _save_holdings()
        else:
            print("  [ATR] 选股池无候选")
    else:
        print("  [ATR] 等待买入窗口 14:50-15:00...")


# ============================================================
# QMT 生命周期
# ============================================================
class StrategyRunner:
    """ATR低波动策略运行器"""

    def __init__(self):
        self.initialized = False

    def init(self, C):
        global _g_initialized, _STRATEGY_CAPITAL, _MAX_HOLD, _g_cooling_until
        print("  [ATR] =============================================")
        print("  [ATR] ATR低波动策略 初始化...")
        print("  [ATR] =============================================")
        # ---- WorkBuddy 修复版指纹（日志 grep 'ATR_FIX_v3_20260802' 即可确认是修复版）----
        global _g_has_trade_query
        _g_has_trade_query = hasattr(C, 'get_trade_detail_data')
        print("  [ATR] ★★★ 修复版 WorkBuddy %s ★★★" % _VERSION)
        print("  [ATR]   修复1: 换手率 fail-open（取不到放行，杜绝静默误杀 0 候选）")
        print("  [ATR]   修复2: 成交确认去除 get_trade_detail_data 依赖（本环境支持=%s -> 不支持则降级乐观确认）" % _g_has_trade_query)
        print("  [ATR]   修复3: handlebar 加 is_last_bar 守卫（跳过历史K线回放，杜绝日志/性能爆炸）")
        print("  [ATR]   修复4: 全市场扫描节流 >=%ds（避免每根bar重复拉取5000+只日线）" % _SCAN_MIN_INTERVAL)
        print("  [ATR] =============================================")

        _load_config()
        _load_holdings()

        # 同步账户持仓
        _sync_account_holdings(C)

        print("  [ATR] 初始化完成  账号=%s" % _ACCOUNT_ID)
        print("  [ATR] 策略本金=%d  累计盈亏=%.0f  当前净值=%.0f" % (
            _STRATEGY_CAPITAL, _g_cumulative_pnl, _STRATEGY_CAPITAL + _g_cumulative_pnl))
        print("  [ATR] 持仓上限=%d只  买入窗口=14:50-15:00" % _MAX_HOLD)

        # cooling-off 60秒（回测模式跳过：time.time()走真实时间而K线快速回放）
        try:
            if C.do_back_test or C.do_backtest:
                _g_cooling_until = 0
                print("  [ATR] 回测模式，跳过 cooling-off")
            else:
                _g_cooling_until = time.time() + 60
                print("  [ATR] 启动 cooling-off 中（60s 内屏蔽所有交易）...")
        except Exception:
            _g_cooling_until = time.time() + 60
            print("  [ATR] 启动 cooling-off 中（60s 内屏蔽所有交易）...")

        _g_initialized = True
        self.initialized = True

    def handlebar(self, C):
        if not self.initialized:
            return
        # 修复3：QMT 启动时会对历史K线逐根回放调用 handlebar（瞬间数千次），
        # 只在最后一根（实时bar）执行业务逻辑；无该API的环境则不拦截。
        try:
            if not C.is_last_bar():
                return
        except Exception:
            pass
        # 守卫通过 = 实时bar到达。计数 + 首根一次性标记，便于远程日志确认守卫生效
        global _g_real_bars, _g_replay_done
        _g_real_bars += 1
        if not _g_replay_done:
            _g_replay_done = True
            print("  [ATR] 历史K线回放结束，进入实时模式（实时bar计数=%d）" % _g_real_bars)
        try:
            _main_loop(C)
        except Exception as e:
            print("  [ATR异常] %s" % e)

    def exit(self, C):
        _save_holdings()
        print("  [ATR] 策略退出，持仓已保存")


# ============================================================
# QMT 生命周期（模块级函数，QMT引擎通过此入口调用策略）
# ============================================================
def init(C):
    """QMT初始化入口"""
    C.runner = StrategyRunner()
    C.runner.init(C)


def after_init(C):
    pass


def handlebar(C):
    """QMT每根K线回调"""
    runner = getattr(C, 'runner', None)
    if runner is not None:
        try:
            runner.handlebar(C)
        except KeyboardInterrupt:
            print("  [停止] 手动中断策略，正常退出...")
            raise


def exit(C):
    """QMT退出回调"""
    runner = getattr(C, 'runner', None)
    if runner is not None:
        runner.exit(C)
