# coding=gbk
"""价值小盘V2微调版 — QMT单文件策略
行业中性BP(z-score)*0.8 + 历史分位BP(hp)*0.2
风控: 8%止损 / 60天持有上限 / 15%组合回撤
账号: 67014907
自动生成于 2026-08-02"""
import math
import csv
import json
import os
import time as _time
from datetime import datetime

# ============ 参数 ============
ACCOUNT_ID = "67014907"
N_STOCKS = 80
REBALANCE_MONTHS = 2
STOP_LOSS = 0.08
MAX_HOLDING_DAYS = 60
MAX_DRAWDOWN = 0.15
TX_COST = 0.001
MV_MAX = 300000.0
Z_WEIGHT = 0.8
HP_WEIGHT = 0.2
HP_WINDOW = 36
HP_MIN = 12
CAPITAL_INIT = 100000.0   # 专属资金池初始资金（小资金测试，收益滚动）
MIN_POSITION_PCT = 0.6    # 持仓低于60%触发补仓

DATA_DIR = "D:/QMT_POOL"
STATE_FILE = os.path.join(DATA_DIR, "v2_holdings_state.json")
LOG_FILE = os.path.join(DATA_DIR, "strategy_log_v2.txt")

# ============ 全局状态 ============
_cash = CAPITAL_INIT       # 专属资金池现金（与账户其他策略资金完全隔离）
_holdings = {}
_entry_prices = {}
_entry_dates = {}
_nav_peak = 1.0
_last_rebal_month = -1
_inited = False
_pending_orders = {}  # 待成交订单跟踪 {code: {"type": "buy/sell", "order_id": None, "time": datetime, "retries": 0}}
_today_orders = {}    # 当日下单记录（收盘对账校准用） {code: {"dir": "buy/sell", "amount": 下单量, "price": 估算价, "entry_price": 卖出前成本价, "entry_date": 卖出前买入日}}
_suspended_sells = []  # 跌停暂缓卖出队列

def _get_market_time(C):
    """QMT行情时间（生产验证方案）: get_tick_timetag -> get_bar_timetag -> datetime.now()
    注意: QMT模拟端 __PyContext 无 get_current_time, 必须用 timetag 方案。"""
    try:
        tick_time = C.get_tick_timetag()
        if tick_time and tick_time > 0:
            return datetime.fromtimestamp(tick_time)
    except Exception:
        pass
    try:
        bar_time = C.get_bar_timetag(C.barpos)
        if bar_time and bar_time > 0:
            return datetime.fromtimestamp(bar_time)
    except Exception:
        pass
    return datetime.now()

def _log(msg, C=None):
    """日志函数，优先用QMT时间，fallback到datetime.now()"""
    if C is not None:
        try:
            ts = _get_market_time(C).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s %s" % (ts, msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def _load_pool(C):
    """从QMT实时获取沪深A股股票池"""
    try:
        pool = C.get_stock_list_in_sector("沪深A股")
        if pool:
            return pool
    except Exception as e:
        _log("[pool error] QMT获取股票池失败: %s" % str(e), C)
    # fallback: 从CSV加载（如果QMT不可用）
    path = os.path.join(DATA_DIR, "selected.txt")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []

def _load_financial():
    """从CSV加载PE/PB/circ_mv/行业"""
    result = {}
    for name in ["pe_ttm", "pb", "circ_mv", "industry"]:
        path = os.path.join(DATA_DIR, "financial_%s.csv" % name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row.get("ts_code", "")
                    val = row.get("value", "")
                    if code and val:
                        if code not in result:
                            result[code] = {}
                        try:
                            result[code][name] = float(val) if name != "industry" else val
                        except Exception:
                            pass
        except Exception:
            pass
    return result

def _load_bp_history():
    """从CSV加载BP月度历史分位"""
    path = os.path.join(DATA_DIR, "bp_hist_pct.csv")
    result = {}
    if not os.path.exists(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get("ts_code", "")
                date = row.get("date", "")
                pct = row.get("hp_pct", "")
                if code and date and pct:
                    if code not in result:
                        result[code] = []
                    try:
                        result[code].append((date, float(pct)))
                    except Exception:
                        pass
    except Exception:
        pass
    return result

def _load_industry_map():
    """从CSV加载行业映射"""
    path = os.path.join(DATA_DIR, "industry_map.csv")
    result = {}
    if not os.path.exists(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get("ts_code", "")
                ind = row.get("industry", "其他")
                if code:
                    result[code] = ind
    except Exception:
        pass
    return result

def _limit_pct(code):
    """涨跌停幅度"""
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10

def _is_limit_up(C, code):
    """检测是否涨停"""
    try:
        data = C.get_market_data_ex(["close", "pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0:
                pct = (close - pre_close) / pre_close
                limit = _limit_pct(code)
                if pct >= limit - 0.001:  # 允许0.1%误差
                    return True
    except Exception:
        pass
    return False

def _is_limit_down(C, code):
    """检测是否跌停"""
    try:
        data = C.get_market_data_ex(["close", "pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0:
                pct = (close - pre_close) / pre_close
                limit = _limit_pct(code)
                if pct <= -(limit - 0.001):  # 允许0.1%误差
                    return True
    except Exception:
        pass
    return False

def _is_suspended(C, code):
    """检测是否停牌"""
    try:
        data = C.get_market_data_ex(["volume"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            vol = float(data[code].iloc[-1].get("volume", 0))
            if vol == 0:
                return True
    except Exception:
        pass
    return False

def _get_order_id(C, code, direction, amount, retries=3, interval=0.2):
    """获取订单号，带重试（解决QMT 100ms延迟问题）"""
    for i in range(retries):
        try:
            orders = C.get_trade_detail_data(ACCOUNT_ID, "STOCK", "order")
            if orders:
                # 按时间倒序，找最近的匹配订单
                for order in orders:
                    if (order.get("m_strSecurityCode") == code and
                        order.get("m_nVolume") == amount and
                        direction in order.get("m_strOptName", "")):
                        return order.get("m_nOrderID")
        except Exception:
            pass
        _time.sleep(interval)
    return None

def _cancel_pending_orders(C):
    """撤销所有待成交订单"""
    global _pending_orders
    try:
        orders = C.get_trade_detail_data(ACCOUNT_ID, "STOCK", "order")
        if orders:
            for order in orders:
                status = order.get("m_nOrderStatus", 0)
                # 状态0=未成交，1=部分成交
                if status in [0, 1]:
                    order_id = order.get("m_nOrderID")
                    code = order.get("m_strSecurityCode")
                    if order_id and code:
                        try:
                            C.cancel_order(ACCOUNT_ID, "STOCK", order_id)
                            _log("[cancel] 撤单 %s order_id=%s" % (code, order_id))
                        except Exception as e:
                            _log("[cancel error] %s: %s" % (code, str(e)))
    except Exception as e:
        _log("[cancel error] %s" % str(e))
    _pending_orders = {}

def _save_state():
    state = {
        "cash": _cash,
        "holdings": _holdings,
        "entry_prices": _entry_prices,
        "entry_dates": _entry_dates,
        "nav_peak": _nav_peak,
        "today_orders": _today_orders,
        "suspended_sells": _suspended_sells,
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass

def _load_state():
    global _cash, _holdings, _entry_prices, _entry_dates, _nav_peak, _today_orders, _suspended_sells
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        _cash = state.get("cash", CAPITAL_INIT)
        _holdings = state.get("holdings", {})
        _entry_prices = state.get("entry_prices", {})
        _entry_dates = state.get("entry_dates", {})
        _nav_peak = state.get("nav_peak", 1.0)
        _today_orders = state.get("today_orders", {})
        _suspended_sells = state.get("suspended_sells", [])
    except Exception:
        pass

def _get_price(C, code, field="close"):
    """安全取价"""
    try:
        data = C.get_market_data_ex([field], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            return float(data[code][field].iloc[-1])
    except Exception:
        pass
    return None

def _score_stocks(C, pool, fin_data, bp_hist, ind_map, today_str):
    """评分：行业中性BP z-score * 0.8 + hp * 0.2"""
    scores = {}
    # 收集BP值
    bp_vals = {}
    for code in pool:
        fd = fin_data.get(code, {})
        pb = fd.get("pb", 0)
        if pb and pb > 0:
            bp_vals[code] = 1.0 / pb

    if not bp_vals:
        return scores

    # 行业中性 z-score
    ind_groups = {}
    for code, bp in bp_vals.items():
        ind = ind_map.get(code, "其他")
        if ind not in ind_groups:
            ind_groups[ind] = []
        ind_groups[ind].append((code, bp))

    z_scores = {}
    for ind, items in ind_groups.items():
        bps = [b for _, b in items]
        mean_bp = sum(bps) / len(bps)
        std_bp = max((sum((b - mean_bp) ** 2 for b in bps) / len(bps)) ** 0.5, 1e-9)
        for code, bp in items:
            z_scores[code] = (bp - mean_bp) / std_bp

    # 历史分位
    hp_vals = {}
    for code in bp_vals:
        hist = bp_hist.get(code, [])
        if len(hist) >= HP_MIN:
            latest = hist[-1][1] if hist else None
            if latest is not None:
                count = sum(1 for _, h in hist[-HP_WINDOW:] if h <= latest)
                hp_vals[code] = count / min(len(hist), HP_WINDOW)

    # 加权合成
    for code in bp_vals:
        z = z_scores.get(code, 0)
        hp = hp_vals.get(code, 0.5)
        scores[code] = z * Z_WEIGHT + hp * HP_WEIGHT

    return scores

def init(C):
    global _inited
    _load_state()
    _log("[init] 策略初始化完成, 持仓=%d" % len(_holdings))
    _inited = True

def handlebar(C):
    global _last_rebal_month, _nav_peak

    now = _get_market_time(C)
    today_str = now.strftime("%Y-%m-%d")
    current_month = now.month
    current_hour = now.hour
    current_minute = now.minute

    # ============ 时间过滤 ============
    # 只在09:35执行选股/下单，14:50保存状态
    # 其他时间只做止损检查（防止盘中跌停无法卖出）
    is_trading_time = (current_hour == 9 and current_minute == 35)
    is_save_time = (current_hour == 14 and current_minute >= 50)

    # ============ 换仓判断 ============
    need_rebal = False

    # 正常换仓：每2个月的第一个交易日
    if current_month != _last_rebal_month and current_month % REBALANCE_MONTHS == 1:
        need_rebal = True
        _last_rebal_month = current_month

    # 补仓触发：持仓低于60%时提前补仓（避免空仓资金闲置）
    current_pct = len(_holdings) / N_STOCKS if N_STOCKS > 0 else 0
    if current_pct < MIN_POSITION_PCT and current_month != _last_rebal_month:
        need_rebal = True
        # 只在交易时点打日志，避免每个 bar 刷屏
        if is_trading_time:
            _log("[rebal] 持仓比例=%.1f%% < %.0f%%, 触发补仓" % (current_pct * 100, MIN_POSITION_PCT * 100))

    # ============ 止损 + 持有期检查（每次bar都执行） ============
    sells = []
    for code in list(_holdings.keys()):
        price = _get_price(C, code, "close")
        if price is None:
            continue
        entry = _entry_prices.get(code, price)
        pnl = price / entry - 1.0 if entry > 0 else 0
        # 止损
        if pnl <= -STOP_LOSS:
            sells.append(code)
            continue
        # 持有期
        entry_d = _entry_dates.get(code, today_str)
        try:
            days_held = (now - datetime.strptime(entry_d, "%Y-%m-%d")).days
        except Exception:
            days_held = 0
        if days_held >= MAX_HOLDING_DAYS:
            sells.append(code)

    # 组合回撤检查
    nav = _calc_nav(C)
    if nav > _nav_peak:
        _nav_peak = nav
    dd = 1.0 - nav / _nav_peak if _nav_peak > 0 else 0
    if dd >= MAX_DRAWDOWN:
        sells = list(_holdings.keys())
        _nav_peak = nav
        _log("[risk] 触发组合回撤止损, dd=%.1f%%" % (dd * 100))

    # 执行卖出（每次bar都执行，防止跌停无法卖出）
    for code in sells:
        # 检查是否跌停/停牌，暂缓卖出
        if _is_limit_down(C, code):
            if code not in _suspended_sells:
                _suspended_sells.append(code)
                _log("[sell suspended] %s 跌停，暂缓卖出" % code, C)
            continue
        if _is_suspended(C, code):
            if code not in _suspended_sells:
                _suspended_sells.append(code)
                _log("[sell suspended] %s 停牌，暂缓卖出" % code, C)
            continue
        # 从暂缓队列移除（如果不在sells中但之前暂缓了）
        if code in _suspended_sells:
            _suspended_sells.remove(code)
        _execute_sell(C, code)

    # 处理暂缓队列中可以卖出的
    for code in list(_suspended_sells):
        if not _is_limit_down(C, code) and not _is_suspended(C, code):
            _suspended_sells.remove(code)
            if code in _holdings:
                _execute_sell(C, code)

    # ============ 换仓选股（只在09:35执行） ============
    if need_rebal and is_trading_time:
        pool = _load_pool(C)
        fin_data = _load_financial()
        bp_hist = _load_bp_history()
        ind_map = _load_industry_map()

        scores = _score_stocks(C, pool, fin_data, bp_hist, ind_map, today_str)
        if not scores:
            return

        # 排除已持仓，取top N
        target = []
        for code, sc in sorted(scores.items(), key=lambda x: -x[1]):
            if len(target) >= N_STOCKS:
                break
            if code not in _holdings:
                target.append(code)

        # 先撤旧单（避免挂单冲突）
        _cancel_pending_orders(C)

        # 买入
        for code in target:
            # 检查是否涨停/停牌，跳过
            if _is_limit_up(C, code):
                _log("[buy skip] %s 涨停，跳过买入" % code, C)
                continue
            if _is_suspended(C, code):
                _log("[buy skip] %s 停牌，跳过买入" % code, C)
                continue
            _execute_buy(C, code)

        _log("[rebal] 换仓完成, 持仓=%d, 候选=%d" % (len(_holdings), len(target)), C)

    # ============ 日终处理（14:50）：先对账校准，再保存状态 ============
    if is_save_time:
        _reconcile(C)
        _save_state()

def _holdings_value(C):
    """当前持仓市值（专属资金池口径）"""
    total = 0.0
    for code, amount in _holdings.items():
        price = _get_price(C, code, "close")
        if price and price > 0:
            total += amount * price
    return total

def _calc_nav(C):
    """专属资金池净值 = (现金 + 持仓市值) / 初始资金，收益滚动"""
    total = _cash + _holdings_value(C)
    return total / CAPITAL_INIT if CAPITAL_INIT > 0 else 1.0

def _reconcile(C):
    """收盘对账：用当日V2成交记录校准专属资金池（估算价→真实成交价）

    买入: 撤销估算扣减，按实际成交金额/数量回写持仓
    卖出: 撤销估算回笼，按实际成交金额回笼；部分成交恢复剩余持仓
    """
    global _cash, _holdings, _entry_prices, _entry_dates, _today_orders
    try:
        if not _today_orders:
            return
        # 获取当日全部成交，按code汇总（deal记录当日有效）
        deals = C.get_trade_detail_data(ACCOUNT_ID, "STOCK", "deal") or []
        traded = {}
        for d in deals:
            code = str(d.get("m_strSecurityCode", "") or "")
            direction = str(d.get("m_strOptName", "") or "")
            vol = float(d.get("m_nVolume", 0) or 0)
            price = float(d.get("m_nPrice", 0) or 0)
            if not code or vol <= 0 or price <= 0:
                continue
            if code not in traded:
                traded[code] = {"buy_vol": 0.0, "buy_amt": 0.0, "sell_vol": 0.0, "sell_amt": 0.0}
            if "买入" in direction:
                traded[code]["buy_vol"] += vol
                traded[code]["buy_amt"] += vol * price
            elif "卖出" in direction:
                traded[code]["sell_vol"] += vol
                traded[code]["sell_amt"] += vol * price

        for code, od in list(_today_orders.items()):
            tr = traded.get(code)
            if od["dir"] == "buy":
                # 撤销估算扣减，按实际成交校准
                _cash += od["amount"] * od["price"]
                if tr and tr["buy_vol"] > 0:
                    _cash -= tr["buy_amt"]
                    _holdings[code] = tr["buy_vol"]
                    _entry_prices[code] = tr["buy_amt"] / tr["buy_vol"]
                    _log("[reconcile] %s 买入校准 成交=%.0f股 均价=%.3f 现金=%.0f"
                         % (code, tr["buy_vol"], _entry_prices[code], _cash), C)
                else:
                    # 未成交（涨停买不进等）：撤销虚拟持仓
                    _holdings.pop(code, None)
                    _entry_prices.pop(code, None)
                    _entry_dates.pop(code, None)
                    _log("[reconcile] %s 买入未成交，撤销虚拟持仓" % code, C)
            elif od["dir"] == "sell":
                # 撤销估算回笼，按实际成交校准
                _cash -= od["amount"] * od["price"]
                if tr and tr["sell_vol"] > 0:
                    _cash += tr["sell_amt"]
                    remain = od["amount"] - tr["sell_vol"]
                    if remain > 0:
                        # 部分成交：剩余持仓恢复（保留原成本）
                        _holdings[code] = remain
                        _entry_prices[code] = od.get("entry_price", 0)
                        _entry_dates[code] = od.get("entry_date", "")
                        _log("[reconcile] %s 卖出部分成交 剩余=%.0f股" % (code, remain), C)
                else:
                    # 未成交（跌停卖不出等）：恢复持仓
                    _holdings[code] = od["amount"]
                    _entry_prices[code] = od.get("entry_price", 0)
                    _entry_dates[code] = od.get("entry_date", "")
                    _log("[reconcile] %s 卖出未成交，恢复持仓" % code, C)
        _today_orders = {}
        _log("[reconcile] 对账完成 持仓=%d 现金=%.0f" % (len(_holdings), _cash), C)
    except Exception as e:
        _log("[reconcile error] %s" % str(e), C)

def _execute_buy(C, code):
    """买入：从专属资金池等权分配，估算记账（收盘对账校准）"""
    global _pending_orders, _cash, _today_orders
    try:
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            return
        # 待买只数（补仓场景 < 80）
        n_held = len(_holdings)
        n_to_buy = N_STOCKS - n_held
        if n_to_buy <= 0:
            return
        # 等权分配：每只金额 = min(资金池总资产/80, 剩余现金/待买只数)
        port_value = _cash + _holdings_value(C)
        per_stock = min(port_value / N_STOCKS, _cash / n_to_buy)
        amount = int(per_stock / price / 100) * 100
        if amount <= 0:
            return
        # 现金兜底：不够买100股就跳过
        if amount * price > _cash:
            amount = int(_cash / price / 100) * 100
            if amount <= 0:
                return
        # 下单（remark 标记 V2，供对账识别专属仓）
        passorder(23, 1101, ACCOUNT_ID, code, 11, -1, amount, "V2买入", 1100)
        # 记录待成交订单 + 当日下单（对账用）
        _pending_orders[code] = {
            "type": "buy",
            "amount": amount,
            "price": price,
            "time": _get_market_time(C),
            "retries": 0
        }
        _today_orders[code] = {"dir": "buy", "amount": amount, "price": price}
        # 估算记账（真实成交价收盘对账校准）
        _holdings[code] = amount
        _entry_prices[code] = price
        _entry_dates[code] = _get_market_time(C).strftime("%Y-%m-%d")
        _cash -= amount * price
        _log("[buy] %s 数量=%d 价格=%.2f 现金=%.0f" % (code, amount, price, _cash), C)
    except Exception as e:
        _log("[buy error] %s: %s" % (code, str(e)), C)

def _execute_sell(C, code):
    """卖出：回笼资金到专属资金池，估算记账（收盘对账校准）"""
    global _pending_orders, _cash, _today_orders
    try:
        amount = _holdings.get(code, 0)
        if amount <= 0:
            return
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            price = _entry_prices.get(code, 0) or 0
        entry_price = _entry_prices.get(code, price)
        entry_date = _entry_dates.get(code, _get_market_time(C).strftime("%Y-%m-%d"))
        # 下单（remark 标记 V2，供对账识别专属仓）
        passorder(24, 1101, ACCOUNT_ID, code, 11, -1, amount, "V2卖出", 1100)
        # 记录待成交订单 + 当日下单（对账用，保留原成本价/日期用于部分成交恢复）
        _pending_orders[code] = {
            "type": "sell",
            "amount": amount,
            "price": price,
            "time": _get_market_time(C),
            "retries": 0
        }
        _today_orders[code] = {
            "dir": "sell", "amount": amount, "price": price,
            "entry_price": entry_price, "entry_date": entry_date,
        }
        # 估算记账（真实成交价收盘对账校准）
        _cash += amount * price
        _log("[sell] %s 数量=%d 价格=%.2f 现金=%.0f" % (code, amount, price, _cash), C)
        _holdings.pop(code, None)
        _entry_prices.pop(code, None)
        _entry_dates.pop(code, None)
    except Exception as e:
        _log("[sell error] %s: %s" % (code, str(e)), C)

def exit(C):
    _reconcile(C)
    _save_state()
    _log("[exit] 策略退出, 持仓=%d 现金=%.0f" % (len(_holdings), _cash))
