# coding=gbk
"""价值小盘V2微调版 — QMT单文件策略
行业中性BP(z-score)*1.0 (V2a 纯BP, hp已去除)
风控: 8%止损 / 60天持有上限 / 15%组合回撤
账号: 67014907
自动生成于 2026-08-04"""
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
Z_WEIGHT = 1.0
HP_WEIGHT = 0.0
HP_WINDOW = 36
HP_MIN = 12
CAPITAL_INIT = 100000.0   # 专属资金池初始资金（小资金测试，收益滚动）
MIN_POSITION_PCT = 0.6    # 持仓低于60%触发补仓

DATA_DIR = "D:/QMT_POOL"
STATE_FILE = os.path.join(DATA_DIR, "v2_holdings_state.json")
LOG_FILE = os.path.join(DATA_DIR, "strategy_log_v2.txt")

# 挂单巡检：委托超时未成交则撤单重下，避免错过交易黄金期
PENDING_TIMEOUT_MIN = 3      # 委托超过3分钟仍未成交，触发撤单重下
PENDING_MAX_RETRIES = 3      # 单只最多重下次数（含首次），超过则放弃（等收盘对账校准）
RETRY_COOLDOWN_MIN = 1       # 撤单重下的最小冷却间隔（分钟），防止同一分钟反复重下

# 构建版本标记：build.py 每次构建时自动替换为时间戳（YYYYmmdd-HHMMSS）
BUILD_TAG = "dev"

# ============ 全局状态 ============
_cash = CAPITAL_INIT       # 专属资金池现金（与账户其他策略资金完全隔离）
_holdings = {}
_entry_prices = {}
_entry_dates = {}
_nav_peak = 1.0
_last_rebal_month = -1
_last_rebal_log_min = -1   # 换仓日志节流（同一分钟内只打一次，防模拟端刷屏）
_last_hb_min = -1          # 心跳日志节流（每5分钟打一次）
_inited = False
_pending_orders = {}  # 待成交订单跟踪 {code: {"type": "buy/sell", "order_id": None, "time": datetime, "retries": 0}}
_today_orders = {}    # 当日下单记录（收盘对账校准用） {code: {"dir": "buy/sell", "amount": 下单量, "price": 估算价, "entry_price": 卖出前成本价, "entry_date": 卖出前买入日}}
_suspended_sells = []  # 跌停暂缓卖出队列
_last_pending_min = -1  # 挂单巡检节流（同一分钟只查一次，防模拟端刷屏）

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
    # 同时输出到 QMT 公式输出窗口，方便实时观察
    try:
        print(line)
    except Exception:
        pass
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

def _query_orders():
    """反查当日全部委托（QMT全局函数，非C方法；不可用时返回None）"""
    try:
        return get_trade_detail_data(ACCOUNT_ID, "STOCK", "order") or []
    except Exception:
        return None

def _norm_code(code):
    """规范化证券代码为6位（兼容 '600000' 与 '600000.SH' 两种格式）"""
    return str(code or "").split(".")[0]

def _find_order(orders, code, direction):
    """在委托列表中找 code+方向 的最近一笔订单（属性风格访问，兼容模拟端）
    direction: 'buy'/'sell'；返回 order 对象或 None"""
    if not orders:
        return None
    c6 = _norm_code(code)
    for o in reversed(orders):
        inst = getattr(o, "m_strInstrumentID", "") or ""
        if _norm_code(inst) != c6:
            continue
        op = getattr(o, "m_strOptName", "") or ""
        if direction == "buy" and "买" not in op and "buy" not in op.lower():
            continue
        if direction == "sell" and "卖" not in op and "sell" not in op.lower():
            continue
        return o
    return None

def _cancel_order_by(C, code, order_id):
    """按订单号撤单（生产惯用法：passorder(24,1101,acct,code,5,order_id,0,remark,2,"",C)）"""
    try:
        passorder(24, 1101, ACCOUNT_ID, code, 5, order_id, 0, "V2撤单", 2, "", C)
        _log("[cancel] 撤单 %s order_id=%s" % (code, order_id), C)
        return True
    except Exception as e:
        _log("[cancel error] %s order_id=%s: %s" % (code, order_id, str(e)), C)
        return False

def _cancel_pending_orders(C):
    """撤销所有待成交订单（换仓前清理；模拟端同样可用，全局函数反查）"""
    global _pending_orders
    orders = _query_orders()
    for code, info in list(_pending_orders.items()):
        if orders:
            order = _find_order(orders, code, info["type"])
            order_id = getattr(order, "m_nOrderID", None) if order else None
            if order_id:
                _cancel_order_by(C, code, order_id)
        # 无论是否反查到，先清跟踪（真实成交由收盘对账校准）
    _pending_orders = {}

def _rollback_pending(C, code, info):
    """重试耗尽：回滚估算记账（真实成交交给收盘对账校准）
    按 original_amount 全额反冲估算，并标记 _today_orders rolled_back 防止对账二次反冲"""
    global _cash, _holdings, _entry_prices, _entry_dates, _today_orders
    try:
        orig = info.get("original_amount", info.get("amount", 0))
        if info["type"] == "buy":
            _holdings.pop(code, None)
            _entry_prices.pop(code, None)
            _entry_dates.pop(code, None)
            _cash += orig * info["price"]
            _log("[pending] %s 买入放弃(重试%d次)，回滚估算 现金=%.0f"
                 % (code, info.get("retries", 0), _cash), C)
        else:
            # 卖出回滚：恢复未卖出部分持仓（已成交部分留给对账按真实成交处理）
            remain = info.get("amount", 0)
            if remain > 0:
                _holdings[code] = remain
                _entry_prices[code] = info.get("entry_price", 0) or info["price"]
                _entry_dates[code] = info.get("entry_date", "") or _get_market_time(C).strftime("%Y-%m-%d")
            _cash -= orig * info["price"]
            _log("[pending] %s 卖出放弃(重试%d次)，恢复持仓 %d股 现金=%.0f"
                 % (code, info.get("retries", 0), remain, _cash), C)
        # 标记对账跳过估算反冲（回滚已全额反冲；若有真实成交，对账仍按 deal 校准）
        if code in _today_orders:
            _today_orders[code]["rolled_back"] = True
    except Exception as e:
        _log("[rollback error] %s: %s" % (code, str(e)), C)

def _retry_pending(C, code, info, order_id=None):
    """撤单重下：撤旧单 + 重新市价委托，重置超时与冷却
    order_id 为已反查到的旧单号（None 表示反查不可用，不撤单也不重下，防重复下单）"""
    global _pending_orders
    try:
        # 1) 撤旧单
        if order_id is None:
            orders = _query_orders()
            if orders:
                order = _find_order(orders, code, info["type"])
                order_id = getattr(order, "m_nOrderID", None) if order else None
        if order_id is None:
            # 反查不可用或订单不存在：不重下（原单可能已成交，重下会重复买入/卖出）
            _log("[pending] %s 未反查到可撤订单，跳过重下（防重复下单，等收盘对账校准）" % code, C)
            return
        _cancel_order_by(C, code, order_id)
        # 2) 重新市价委托（与 _execute_buy/sell 相同的11参数格式）
        if info["type"] == "buy":
            passorder(23, 1101, ACCOUNT_ID, code, 5, -1, info["amount"], "V2买入", 2, "", C)
        else:
            passorder(24, 1101, ACCOUNT_ID, code, 5, -1, info["amount"], "V2卖出", 2, "", C)
        # 3) 更新跟踪：retries+1，重置 time（超时重计）
        info["retries"] = info.get("retries", 0) + 1
        info["time"] = _get_market_time(C)
        _pending_orders[code] = info
        _log("[pending] %s %s重下 数量=%d retry=%d"
             % (code, info["type"], info["amount"], info["retries"]), C)
    except Exception as e:
        _log("[retry error] %s: %s" % (code, str(e)), C)

def _check_pending_orders(C):
    """挂单巡检（每bar调用，同分钟节流）：超时未成交 → 撤单重下，重试封顶后回滚
    避免错过交易黄金期。模拟端同样可用（全局函数反查订单）。"""
    global _pending_orders, _last_pending_min
    if not _pending_orders:
        return
    now = _get_market_time(C)
    log_min = now.hour * 100 + now.minute
    if log_min == _last_pending_min:
        return
    _last_pending_min = log_min
    orders = _query_orders()
    if orders is None:
        # 反查接口不可用：本分钟跳过动作（不撤单不重下，防重复下单）
        _log("[pending] 订单反查不可用，巡检跳过（防重复下单）", C)
        return
    for code, info in list(_pending_orders.items()):
        try:
            elapsed_min = (now - info["time"]).total_seconds() / 60.0
        except Exception:
            elapsed_min = 0
        if elapsed_min < PENDING_TIMEOUT_MIN:
            continue
        # 反查成交状态：已全部成交则移出跟踪
        order = _find_order(orders, code, info["type"])
        if order is not None:
            vol_traded = getattr(order, "m_nVolumeTraded", 0) or 0
            if vol_traded >= info["amount"]:
                _pending_orders.pop(code, None)
                _log("[pending] %s 已全部成交 %d股，移出跟踪" % (code, vol_traded), C)
                continue
            if vol_traded > 0:
                # 部分成交：撤剩余并重下剩余
                info["amount"] = info["amount"] - vol_traded
                info["already_traded"] = info.get("already_traded", 0) + vol_traded
                _pending_orders[code] = info
                _log("[pending] %s 部分成交 %d股，剩余 %d股" % (code, vol_traded, info["amount"]), C)
        else:
            # 订单不在委托列表：可能已成交被移除或已撤。不重下，等收盘对账校准
            _log("[pending] %s 未找到 %s 委托，跳过重下（等收盘对账校准）" % (code, info["type"]), C)
            continue
        # 冷却检查（time 已重置则天然满足，此处兜底防极端情况）
        if elapsed_min < RETRY_COOLDOWN_MIN:
            continue
        # 重试封顶：放弃并回滚估算记账
        if info.get("retries", 0) >= PENDING_MAX_RETRIES:
            _rollback_pending(C, code, info)
            _pending_orders.pop(code, None)
            continue
        # 撤单重下
        _retry_pending(C, code, info)

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
    _log("[init] 策略初始化完成, 持仓=%d, build=%s" % (len(_holdings), BUILD_TAG))
    _inited = True

def handlebar(C):
    global _last_rebal_month, _nav_peak, _last_rebal_log_min, _last_hb_min

    now = _get_market_time(C)
    today_str = now.strftime("%Y-%m-%d")
    current_month = now.month
    current_hour = now.hour
    current_minute = now.minute

    # ============ 时间过滤 ============
    # 模拟盘调试模式：解锁09:35/14:50时间锁，任何bar都允许交易和保存状态
    # 上线前需恢复为: is_trading_time = (current_hour == 9 and current_minute == 35)
    #              is_save_time   = (current_hour == 14 and current_minute >= 50)
    is_trading_time = True
    is_save_time = True
    # 日志节流：同一分钟内只打印一次换仓/补仓日志
    log_min = current_hour * 100 + current_minute
    log_gate = (log_min != _last_rebal_log_min)

    # ============ 心跳日志（模拟盘调试） ============
    # 每5分钟打一次，确认 handlebar 在跑；上线前可删除
    if log_min != _last_hb_min and log_min % 5 == 0:
        _last_hb_min = log_min
        _log("[hb] bar心跳 持仓=%d 现金=%.0f nav=%.3f" % (len(_holdings), _cash, _calc_nav(C)), C)

    # ============ 挂单巡检（每bar，内部分钟节流） ============
    # 委托超时未成交自动撤单重下，避免错过交易黄金期
    if is_trading_time:
        _check_pending_orders(C)

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
        _last_rebal_month = current_month
        # 只在交易时点打日志，避免每个 bar 刷屏
        if is_trading_time and log_gate:
            _last_rebal_log_min = log_min
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

    # ============ 换仓选股（模拟盘调试：任何bar都执行） ============
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

        if log_gate:
            _last_rebal_log_min = log_min
            _log("[rebal] 换仓完成, 持仓=%d, 候选=%d" % (len(_holdings), len(target)), C)

    # ============ 日终处理（模拟盘调试：任何bar都保存状态） ============
    if is_save_time:
        # 对账仅收盘时段执行（盘中估算记账未定型，过早对账会把未成交单误判为未成交）
        if current_hour == 14 and current_minute >= 50:
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
    global _cash, _holdings, _entry_prices, _entry_dates, _today_orders, _pending_orders
    try:
        if not _today_orders:
            return
        # 获取当日全部成交，按code汇总（deal记录当日有效）
        # 生产/模拟端统一走全局函数（C 对象无反查接口；qmt_wrapper 生产验证）
        try:
            deals = get_trade_detail_data(ACCOUNT_ID, "STOCK", "deal") or []
        except Exception:
            deals = []
        traded = {}
        for d in deals:
            code = _norm_code(getattr(d, "m_strSecurityCode", "") or getattr(d, "m_strInstrumentID", ""))
            direction = str(getattr(d, "m_strOptName", "") or "")
            vol = float(getattr(d, "m_nVolume", 0) or 0)
            price = float(getattr(d, "m_nPrice", 0) or 0)
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
            tr = traded.get(_norm_code(code))
            rolled = od.get("rolled_back", False)
            if od["dir"] == "buy":
                if not rolled:
                    # 撤销估算扣减，按实际成交校准
                    _cash += od["amount"] * od["price"]
                if tr and tr["buy_vol"] > 0:
                    _cash -= tr["buy_amt"]
                    _holdings[code] = tr["buy_vol"]
                    _entry_prices[code] = tr["buy_amt"] / tr["buy_vol"]
                    _log("[reconcile] %s 买入校准 成交=%.0f股 均价=%.3f 现金=%.0f"
                         % (code, tr["buy_vol"], _entry_prices[code], _cash), C)
                elif not rolled:
                    # 未成交（涨停买不进等）：撤销虚拟持仓
                    _holdings.pop(code, None)
                    _entry_prices.pop(code, None)
                    _entry_dates.pop(code, None)
                    _log("[reconcile] %s 买入未成交，撤销虚拟持仓" % code, C)
                # rolled 且无成交：回滚已恢复现金/撤销持仓，跳过
            elif od["dir"] == "sell":
                if not rolled:
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
                        # 全部成交：持仓清零（回滚曾恢复，此处按实际成交移除）
                        _holdings.pop(code, None)
                        _entry_prices.pop(code, None)
                        _entry_dates.pop(code, None)
                        _log("[reconcile] %s 卖出全部成交，持仓清零" % code, C)
                elif not rolled:
                    # 未成交（跌停卖不出等）：恢复持仓
                    _holdings[code] = od["amount"]
                    _entry_prices[code] = od.get("entry_price", 0)
                    _entry_dates[code] = od.get("entry_date", "")
                    _log("[reconcile] %s 卖出未成交，恢复持仓" % code, C)
                # rolled 且无成交：回滚已恢复持仓/现金，跳过
        _today_orders = {}
        _pending_orders = {}
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
        # 下单（生产级11参数格式，末尾必须传C：opType, orderType, accountid, orderCode, prType, modelprice, volume, remark, quickTrade, extra, C）
        # 参考: E:/QuantLab 内自包含的 11 参数 passorder 封装（生产验证过）
        # 买入用市价单(price_type=5, price=-1)，确保成交；不能用9参数无C格式（模拟端报 request_id 错误）
        passorder(23, 1101, ACCOUNT_ID, code, 5, -1, amount, "V2买入", 2, "", C)
        # 记录待成交订单 + 当日下单（对账用）
        _pending_orders[code] = {
            "type": "buy",
            "amount": amount,
            "original_amount": amount,
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
        _log("[buy error] %s: %s | %s" % (code, str(e), _exc_tb()), C)

def _exc_tb():
    """返回异常堆栈（单行，QMT3.6兼容）"""
    try:
        import traceback
        return traceback.format_exc().replace("\n", " | ")[:800]
    except Exception:
        return "no-tb"

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
        # 下单（生产级11参数格式，末尾必须传C；卖出用市价单 price_type=5 确保成交）
        # 参考: E:/QuantLab 内自包含的 11 参数 passorder 封装（生产验证过）
        passorder(24, 1101, ACCOUNT_ID, code, 5, -1, amount, "V2卖出", 2, "", C)
        # 记录待成交订单 + 当日下单（对账用，保留原成本价/日期用于部分成交/回滚恢复）
        _pending_orders[code] = {
            "type": "sell",
            "amount": amount,
            "original_amount": amount,
            "price": price,
            "time": _get_market_time(C),
            "retries": 0,
            "entry_price": entry_price,
            "entry_date": entry_date,
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
        _log("[sell error] %s: %s | %s" % (code, str(e), _exc_tb()), C)

def exit(C):
    _reconcile(C)
    _save_state()
    _log("[exit] 策略退出, 持仓=%d 现金=%.0f" % (len(_holdings), _cash))
