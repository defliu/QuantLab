# coding=gbk
# coding=gbk
"""黄氏529主升浪精选 —— QMT 单文件策略（本地预计算信号，QMT 只读 CSV）

选股: 黄氏529版公式信号（本地 D:/Python311 预计算，PIT 安全）按 ATR% 升序 top16,
      每日 T-1 收盘后写入 D:/QMT_POOL/529_signal_top16_YYYYMMDD.csv。
      策略 T 日 09:35 读最新信号 CSV（日期 < 今日），剔已持有，按 rank 补槽至 12 只，
      每只 min(total_asset/12, cash/当日新买数)；涨停不追、T+1、整数手。
卖出: 止损 -12%（收盘口径）/ 移动止损 trail15（默认关，T1 判定未启用）/ 到期 ≥60 交易日。
fail-open: 当日信号 CSV 缺失/损坏 -> 当日不买入（现金闲置），日志高亮 WARN，
           持仓管理（卖出）不受影响照常执行。
风控: 卖出优先级高于买入；先卖后买；跌停/停牌卖出进暂缓队列，解封自动补卖。
账本: D:/QMT_POOL/p13_huang529_holdings.json（code/entry_date/cost/peak_close/hold_days/volume）。
对账: 以账户 position 唯一真相 + 三态兜底；回滚先撤活单；每日成交/持仓 CSV 导出。
账号: 70180771（硬编码）；remark=P13H529（防串账户）。
"""
import json
import os
import time as _time
import glob
from datetime import datetime

# ============ 参数 ============
ACCOUNT_ID = "70180771"
CAPITAL_BASE = 100000.0      # 专属资金池本金（资金分配表登记 huang529_breakout）
N_HOLD = 12                  # 持仓上限
MAX_HOLDING_DAYS = 60        # 最长持有（交易日）
STOP_LOSS = -0.12            # 止损（收盘口径）
TRAILING_STOP = None         # 移动止损（None=关；0.15=收盘<=持有期最高收盘×0.85 触发，T1 判定未启用）
MAX_SINGLE_PCT = 1.0 / 12.0  # 单票仓位上限
SIGNAL_PREFIX = "529_signal_top16_"
SIGNAL_DIR = "D:/QMT_POOL"
DATA_DIR = "D:/QMT_POOL/config"
HOLDINGS_FILE = os.path.join(DATA_DIR, "p13_huang529_holdings.json")
NAV_FILE = os.path.join(DATA_DIR, "p13_huang529_nav.json")
TRADE_LOG_FILE = os.path.join(DATA_DIR, "p13_huang529_trade_log.csv")
REMARK = "P13H529"

# 挂单巡检：委托超时未成交则撤单重下，避免错过交易黄金期
PENDING_TIMEOUT_MIN = 3
PENDING_MAX_RETRIES = 3
RETRY_COOLDOWN_MIN = 1

# 构建版本标记：build.py 每次构建时自动替换为时间戳（YYYYmmdd-HHMMSS）
BUILD_TAG = "20260823-223613"

# ============ 全局状态 ============
_cash = CAPITAL_BASE
_holdings = {}              # code -> {volume, cost, entry_date, peak_close, hold_days, orphan}
_pending_orders = {}        # code -> {type, order_id, amount, original_amount, price, time, retries}
_today_orders = {}          # code -> {dir, amount, price, entry_price, entry_date}（对账用）
_suspended_sells = []       # 跌停/停牌暂缓卖出队列
_last_reconcile_date = ""   # 收盘对账每日闸门（同一天只跑一次）
_last_decision_date = ""    # 当日交易决策闸门（09:35 只执行一次）
_last_pending_min = -1      # 挂单巡检节流
_last_hb_min = -1           # 心跳日志节流
_last_close_task_date = ""  # 收盘任务（对账/导出）每日闸门
_inited = False
_ACCT_QUERY_FAIL = object()  # 账户持仓查询失败哨兵（对账兜底：无法判定时保守处理）

# ============ 配置（内置默认值，可被 config/huang529.yaml 覆盖） ============
_DEFAULT_CONFIG = {
    "strategy": {
        "name": "HUANG529",
        "display_name": "黄氏529主升浪",
        "capital_base": 100000.0,
        "account_id": "70180771",
    },
    "signal": {
        "n_hold": 12,
        "max_single_pct": 1.0 / 12.0,
        "signal_dir": "D:/QMT_POOL",
        "signal_prefix": "529_signal_top16_",
    },
    "sell": {
        "stop_loss": -0.12,
        "max_holding_days": 60,
        "trailing_stop": None,
    },
    "pool": {
        "holdings_file": "D:/QMT_POOL/p13_huang529_holdings.json",
        "nav_file": "D:/QMT_POOL/p13_huang529_nav.json",
        "trade_log_file": "D:/QMT_POOL/p13_huang529_trade_log.csv",
    },
}


def _load_config():
    """加载配置（简易 YAML 解析，不依赖 pyyaml；读不到用 _DEFAULT_CONFIG 完整 fallback）。
    策略自包含：config 读取不依赖 __file__，QMT 部署/本地验证共用。"""
    global _cash, _holdings, N_HOLD, MAX_SINGLE_PCT, STOP_LOSS, MAX_HOLDING_DAYS, TRAILING_STOP
    global SIGNAL_DIR, SIGNAL_PREFIX, HOLDINGS_FILE, NAV_FILE, TRADE_LOG_FILE, ACCOUNT_ID, CAPITAL_BASE
    cfg = _DEFAULT_CONFIG
    # 尝试读取 QMT_POOL 下的配置（QMT 环境通常无 pyyaml，手写解析足够）
    path = os.path.join(DATA_DIR, "p13_huang529_config.yaml")
    if not os.path.exists(path):
        return
    try:
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        section = None
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.endswith(":") and not s.startswith("-"):
                section = s.rstrip(":")
                continue
            if ":" in s:
                k, v = [x.strip() for x in s.split(":", 1)]
                if section == "strategy":
                    if k == "capital_base":
                        CAPITAL_BASE = float(v)
                    elif k == "account_id":
                        ACCOUNT_ID = str(v).strip("'\"")
                elif section == "signal":
                    if k == "n_hold":
                        N_HOLD = int(v)
                    elif k == "max_single_pct":
                        MAX_SINGLE_PCT = float(v)
                    elif k == "signal_dir":
                        SIGNAL_DIR = str(v).strip("'\"")
                    elif k == "signal_prefix":
                        SIGNAL_PREFIX = str(v).strip("'\"")
                elif section == "sell":
                    if k == "stop_loss":
                        STOP_LOSS = float(v)
                    elif k == "max_holding_days":
                        MAX_HOLDING_DAYS = int(v)
                    elif k == "trailing_stop":
                        vv = str(v).strip().lower()
                        TRAILING_STOP = float(v) if vv not in ("none", "null", "") else None
                elif section == "pool":
                    if k == "holdings_file":
                        HOLDINGS_FILE = str(v).strip("'\"")
                    elif k == "nav_file":
                        NAV_FILE = str(v).strip("'\"")
                    elif k == "trade_log_file":
                        TRADE_LOG_FILE = str(v).strip("'\"")
        _cash = CAPITAL_BASE
        print("[P13] 配置加载完成 signal_dir=%s n_hold=%d stop_loss=%.2f trail=%s" % (
            SIGNAL_DIR, N_HOLD, STOP_LOSS, TRAILING_STOP))
    except Exception as e:
        print("[P13] 配置加载失败 %s, 使用内置默认值" % e)


def _get_market_time(C):
    """QMT 行情时间（生产验证方案）: get_current_time -> get_tick_timetag -> datetime.now()
    AGENTS.md 红线：策略时间必须用 QMT 行情时间，不能读 datetime.now()（设备 CMOS 可能错乱）。"""
    try:
        t = C.get_current_time()
        if t:
            return t
    except Exception:
        pass
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
    """日志：QMT 行情时间 + 文件落盘（自包含，无外部依赖）。"""
    if C is not None:
        try:
            ts = _get_market_time(C).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s %s" % (ts, msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        log_path = os.path.join(DATA_DIR, "strategy_log_p13_huang529.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_holdings():
    """从文件加载持仓状态（账本唯一事实源，与 QMT 账户隔离）。"""
    global _cash, _holdings
    if os.path.exists(HOLDINGS_FILE):
        try:
            with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 账号戳校验（T-20260823-004）：不匹配或缺戳 -> 备份旧档并空仓起步(fail-safe)
            stamp = str(data.get("account_id", ""))
            if stamp != str(ACCOUNT_ID):
                bak = "%s.bak_acct_%s_%s" % (HOLDINGS_FILE, stamp or "nostamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
                try:
                    fbk = open(HOLDINGS_FILE, "rb")
                    _raw_b = fbk.read()
                    fbk.close()
                    fbk2 = open(bak, "wb")
                    fbk2.write(_raw_b)
                    fbk2.close()
                    print("[P13] [!] 账本账号戳不匹配(账本=%s 本策略=%s)，旧档已备份 %s，空仓起步" % (stamp or "无戳", ACCOUNT_ID, os.path.basename(bak)))
                except Exception as e_bak:
                    print("[P13] [!] 账本账号戳不匹配(账本=%s 本策略=%s)，备份失败，空仓起步" % (stamp or "无戳", ACCOUNT_ID))
                _holdings = {}
                _cash = CAPITAL_BASE
                return
            _holdings = data.get("holdings", {})
            _cash = float(data.get("cash", CAPITAL_BASE))
            print("[P13] 加载持仓 %d 只, 现金=%.0f" % (len(_holdings), _cash))
        except Exception as e:
            print("[P13] 持仓加载失败: %s" % e)
            _holdings = {}
            _cash = CAPITAL_BASE
    else:
        _holdings = {}
        _cash = CAPITAL_BASE


def _save_holdings():
    """保存持仓状态到文件（每笔决策/成交后调用）。"""
    try:
        data = {
            "account_id": ACCOUNT_ID,
            "cash": _cash,
            "holdings": _holdings,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(HOLDINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[P13] 持仓保存失败: %s" % e)


def _log_trade(trade_type, code, price, shares, reason):
    """记录成交到 CSV（日终导出 + 实时追写）。"""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = "%s,%s,%s,%.3f,%d,%s\n" % (now, trade_type, code, price, shares, reason)
        with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ============ 信号 CSV 读取（fail-open） ============
def _latest_signal_date():
    """取最新信号 CSV 的日期（文件名 YYYYMMDD），不含今天（T-1 及更早）。
    返回 YYYYMMDD 字符串 或 None。"""
    best = None
    try:
        today = datetime.now().strftime("%Y%m%d")
        for p in glob.glob(os.path.join(SIGNAL_DIR, SIGNAL_PREFIX + "*.csv")):
            base = os.path.basename(p)
            ds = base.replace(SIGNAL_PREFIX, "").replace(".csv", "")
            if len(ds) != 8 or not ds.isdigit():
                continue
            if ds >= today:
                continue
            if best is None or ds > best:
                best = ds
    except Exception:
        pass
    return best


def _load_signal_csv():
    """读 T-1 信号 CSV（fail-open）。
    返回 [(code, atr_pct, rank), ...] 按 rank 升序；缺失/损坏返回 [] 并记 WARN。
    """
    ds = _latest_signal_date()
    if ds is None:
        _log("[P13][WARN] 未找到信号 CSV（%s*，<今日），fail-open: 当日不买入，卖出照常" % SIGNAL_PREFIX)
        return []
    path = os.path.join(SIGNAL_DIR, "%s%s.csv" % (SIGNAL_PREFIX, ds))
    signals = []
    try:
        import csv
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("ts_code") or "").strip()
                if not code:
                    continue
                try:
                    atr = float(row.get("atr_pct") or 0)
                    rank = int(row.get("rank") or 0)
                except Exception:
                    atr = 0
                    rank = 0
                signals.append((code, atr, rank))
    except Exception as e:
        _log("[P13][WARN] 信号 CSV 读取失败 %s: %s, fail-open: 当日不买入，卖出照常" % (path, e))
        return []
    if not signals:
        _log("[P13][WARN] 信号 CSV 为空（%s），fail-open: 当日不买入" % path)
        return []
    signals.sort(key=lambda x: (x[2] if x[2] > 0 else 999, x[1]))
    _log("[P13] 信号 CSV %s 共 %d 只" % (ds, len(signals)))
    return signals


# ============ 限价/涨跌停/停牌 ============
def _limit_pct(code):
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith("8") or code.startswith("4") or code.startswith("92"):
        return 0.30
    return 0.10


def _is_limit_up(C, code):
    try:
        data = C.get_market_data_ex(["close", "pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0:
                pct = (close - pre_close) / pre_close
                if pct >= _limit_pct(code) - 0.001:
                    return True
    except Exception:
        pass
    return False


def _is_limit_down(C, code):
    try:
        data = C.get_market_data_ex(["close", "pre_close"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0:
                pct = (close - pre_close) / pre_close
                if pct <= -(_limit_pct(code) - 0.001):
                    return True
    except Exception:
        pass
    return False


def _is_suspended(C, code):
    """停牌判定：集合竞价早段 volume 可能为 0，综合判据 volume==0 且无有效开盘价。"""
    try:
        data = C.get_market_data_ex(["open", "volume"], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            row = data[code].iloc[-1]
            vol = float(row.get("volume", 0) or 0)
            opn = float(row.get("open", 0) or 0)
            if vol == 0 and opn <= 0:
                return True
    except Exception:
        pass
    return False


# ============ 价格/账户 ============
def _get_price(C, code, field="close"):
    try:
        data = C.get_market_data_ex([field], [code], period="1d")
        if data and code in data and len(data[code]) > 0:
            return float(data[code][field].iloc[-1])
    except Exception:
        pass
    return None


def _query_orders():
    """反查当日全部委托（QMT 全局函数，非 C 方法；不可用时返回 None）。"""
    try:
        return get_trade_detail_data(ACCOUNT_ID, "STOCK", "order") or []
    except Exception:
        return None


def _norm_code(code):
    return str(code or "").split(".")[0]


def _get_acct_position(code):
    """查询账户真实持仓量（position 接口，三态兜底）。
    返回 POS 对象 / None（确无持仓）/ _ACCT_QUERY_FAIL（查询失败，调用方保守处理）。"""
    try:
        c6 = _norm_code(code)
        for p in (get_trade_detail_data(ACCOUNT_ID, "STOCK", "position") or []):
            inst = getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or ""
            if _norm_code(inst) == c6:
                return p
        return None
    except Exception:
        return _ACCT_QUERY_FAIL


def _find_order(orders, code, direction):
    """在委托列表中找 code+方向 的订单（兼容模拟端）。
    红线：remark 只作候选优先级，不硬过滤；唯一候选即使 remark 空也返回。
    候选集内优先返回 remark 含 'P13H529' 的订单(本策略)；无且唯一候选时返回之；
    多候选且均非本策略(含 remark 空) 时返回 None(歧义, 宁可跳过不撤他人单)。"""
    if not orders:
        return None
    c6 = _norm_code(code)
    cand = []
    for o in reversed(orders):
        inst = getattr(o, "m_strInstrumentID", "") or ""
        if _norm_code(inst) != c6:
            continue
        op = getattr(o, "m_strOptName", "") or ""
        if direction == "buy" and "买" not in op and "buy" not in op.lower():
            continue
        if direction == "sell" and "卖" not in op and "sell" not in op.lower():
            continue
        cand.append(o)
    if not cand:
        return None
    for o in cand:
        remark = str(getattr(o, "m_strRemark", "") or "")
        if remark.find("P13H529") >= 0:
            return o
    if len(cand) == 1:
        return cand[0]
    return None


def _cancel_order_by(C, code, order_id):
    """按订单号撤单（生产惯用法：passorder(24,1101,acct,code,5,order_id,0,remark,2,"",C)）。"""
    try:
        passorder(24, 1101, ACCOUNT_ID, code, 5, order_id, 0, REMARK + "撤单", 2, "", C)
        _log("[P13 cancel] 撤单 %s order_id=%s" % (code, order_id), C)
        return True
    except Exception as e:
        _log("[P13 cancel error] %s order_id=%s: %s" % (code, order_id, str(e)), C)
        return False


def _cancel_pending_orders(C):
    """撤销所有待成交订单（换仓前清理）。"""
    global _pending_orders
    orders = _query_orders()
    for code, info in list(_pending_orders.items()):
        if orders:
            order = _find_order(orders, code, info["type"])
            order_id = getattr(order, "m_nOrderID", None) if order else None
            if order_id:
                _cancel_order_by(C, code, order_id)
    _pending_orders = {}


# ============ 挂单巡检 / 回滚 ============
def _rollback_pending(C, code, info):
    """重试耗尽/跨日残留：回滚估算记账。
    ①回滚前先反查并撤销 QMT 端活单（防次日意外成交）；
    ②卖出回滚后若仍跌停/停牌，进暂缓队列（解封后自动补卖）。"""
    global _cash, _holdings, _today_orders, _suspended_sells
    try:
        orders = _query_orders()
        if orders:
            order = _find_order(orders, code, info["type"])
            order_id = getattr(order, "m_nOrderID", None) if order else None
            if order_id:
                _cancel_order_by(C, code, order_id)
        orig = info.get("original_amount", info.get("amount", 0))
        if info["type"] == "buy":
            _holdings.pop(code, None)
            _cash += orig * info["price"]
            _log("[P13 pending] %s 买入放弃(重试%d次)，回滚估算 现金=%.0f"
                 % (code, info.get("retries", 0), _cash), C)
        else:
            remain = info.get("amount", 0)
            if remain > 0:
                if code not in _holdings:
                    _holdings[code] = {
                        "volume": int(remain),
                        "cost": info.get("entry_price", 0) or info["price"],
                        "entry_date": info.get("entry_date", "") or datetime.now().strftime("%Y%m%d"),
                        "peak_close": info.get("entry_price", 0) or info["price"],
                        "hold_days": 0,
                    }
                _cash -= orig * info["price"]
                try:
                    if _is_limit_down(C, code) or _is_suspended(C, code):
                        if code not in _suspended_sells:
                            _suspended_sells.append(code)
                            _log("[P13 pending] %s 卖出放弃但跌停/停牌，进暂缓队列" % code, C)
                except Exception:
                    pass
            _log("[P13 pending] %s 卖出放弃(重试%d次)，恢复持仓 现金=%.0f"
                 % (code, info.get("retries", 0), _cash), C)
        if code in _today_orders:
            _today_orders[code]["rolled_back"] = True
    except Exception as e:
        _log("[P13 rollback error] %s: %s" % (code, str(e)), C)


def _retry_pending(C, code, info):
    """撤单重下：撤旧单 + 重新市价委托，重置超时与冷却。"""
    global _pending_orders
    try:
        order_id = None
        orders = _query_orders()
        if orders:
            order = _find_order(orders, code, info["type"])
            order_id = getattr(order, "m_nOrderID", None) if order else None
        if order_id is None:
            _log("[P13 pending] %s 未反查到可撤订单，跳过重下（防重复下单，等收盘对账校准）" % code, C)
            return
        _cancel_order_by(C, code, order_id)
        if info["type"] == "buy":
            passorder(23, 1101, ACCOUNT_ID, code, 5, -1, info["amount"], REMARK, 2, "", C)
        else:
            passorder(24, 1101, ACCOUNT_ID, code, 5, -1, info["amount"], REMARK, 2, "", C)
        info["retries"] = info.get("retries", 0) + 1
        info["time"] = _get_market_time(C)
        _pending_orders[code] = info
        _log("[P13 pending] %s %s重下 数量=%d retry=%d" % (code, info["type"], info["amount"], info["retries"]), C)
    except Exception as e:
        _log("[P13 retry error] %s: %s" % (code, str(e)), C)


def _check_pending_orders(C):
    """挂单巡检（每bar调用，同分钟节流）：超时未成交 → 撤单重下，重试封顶后回滚。"""
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
        _log("[P13 pending] 订单反查不可用，巡检跳过（防重复下单）", C)
        return
    for code, info in list(_pending_orders.items()):
        try:
            elapsed_min = (now - info["time"]).total_seconds() / 60.0
        except Exception:
            elapsed_min = 0
        try:
            same_day = info["time"].strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d")
        except Exception:
            same_day = False
        if not same_day:
            _rollback_pending(C, code, info)
            _pending_orders.pop(code, None)
            _log("[P13 pending] %s 跨日残留，强制回滚并移出" % code, C)
            continue
        if elapsed_min < PENDING_TIMEOUT_MIN:
            continue
        order = _find_order(orders, code, info["type"])
        if order is not None:
            vol_traded = getattr(order, "m_nVolumeTraded", 0) or 0
            if vol_traded >= info["amount"]:
                _pending_orders.pop(code, None)
                _log("[P13 pending] %s 已全部成交 %d股，移出跟踪" % (code, vol_traded), C)
                continue
            if vol_traded > 0:
                info["amount"] = info["amount"] - vol_traded
                info["already_traded"] = info.get("already_traded", 0) + vol_traded
                _pending_orders[code] = info
                _log("[P13 pending] %s 部分成交 %d股，剩余 %d股" % (code, vol_traded, info["amount"]), C)
        else:
            _log("[P13 pending] %s 未找到 %s 委托，跳过重下（等收盘对账校准）" % (code, info["type"]), C)
            continue
        if elapsed_min < RETRY_COOLDOWN_MIN:
            continue
        if info.get("retries", 0) >= PENDING_MAX_RETRIES:
            _rollback_pending(C, code, info)
            _pending_orders.pop(code, None)
            continue
        _retry_pending(C, code, info)


# ============ 交易执行 ============
def _holdings_value(C):
    total = 0.0
    for code, info in _holdings.items():
        price = _get_price(C, code, "close")
        if price and price > 0:
            total += info.get("volume", 0) * price
    return total


def _calc_nav(C):
    total = _cash + _holdings_value(C)
    return total / CAPITAL_BASE if CAPITAL_BASE > 0 else 1.0


def _execute_buy(C, code):
    """买入：等权分配，估算记账（收盘对账校准）。"""
    global _pending_orders, _cash, _today_orders
    try:
        if code in _pending_orders:
            return
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            return
        n_held = len(_holdings)
        n_to_buy = N_HOLD - n_held
        if n_to_buy <= 0:
            return
        port_value = _cash + _holdings_value(C)
        per_stock = min(MAX_SINGLE_PCT * port_value, _cash / n_to_buy)
        amount = int(per_stock / price / 100) * 100
        if amount <= 0:
            return
        if amount * price > _cash:
            amount = int(_cash / price / 100) * 100
            if amount <= 0:
                return
        # 涨停不追
        if _is_limit_up(C, code):
            _log("[P13 buy skip] %s 涨停，跳过买入" % code, C)
            return
        passorder(23, 1101, ACCOUNT_ID, code, 5, -1, amount, REMARK, 2, "", C)
        _pending_orders[code] = {
            "type": "buy",
            "amount": amount,
            "original_amount": amount,
            "price": price,
            "time": _get_market_time(C),
            "retries": 0,
        }
        _today_orders[code] = {
            "dir": "buy", "amount": amount, "price": price,
            "entry_date": _get_market_time(C).strftime("%Y%m%d"),
        }
        _holdings[code] = {
            "volume": amount,
            "cost": price,
            "entry_date": _get_market_time(C).strftime("%Y%m%d"),
            "peak_close": price,
            "hold_days": 0,
        }
        _cash -= amount * price
        _log_trade("买入", code, price, amount, "huang529_signal")
        _log("[P13 buy] %s 数量=%d 价格=%.2f 现金=%.0f" % (code, amount, price, _cash), C)
    except Exception as e:
        _log("[P13 buy error] %s: %s" % (code, str(e)), C)


def _execute_sell(C, code, reason):
    """卖出：回笼资金，估算记账（收盘对账校准）。"""
    global _pending_orders, _cash, _today_orders
    try:
        if code in _pending_orders:
            return
        info = _holdings.get(code)
        if info is None:
            return
        amount = int(info.get("volume", 0))
        if amount <= 0:
            return
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            price = info.get("cost", 0) or 0
        entry_price = info.get("cost", price)
        entry_date = info.get("entry_date", _get_market_time(C).strftime("%Y%m%d"))
        passorder(24, 1101, ACCOUNT_ID, code, 5, -1, amount, REMARK, 2, "", C)
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
        _cash += amount * price
        _log_trade("卖出", code, price, amount, reason)
        _log("[P13 sell] %s 数量=%d 价格=%.2f 原因=%s 现金=%.0f" % (code, amount, price, reason, _cash), C)
        _holdings.pop(code, None)
    except Exception as e:
        _log("[P13 sell error] %s: %s" % (code, str(e)), C)


# ============ 对账（账户 position 唯一真相 + 三态兜底） ============
def _reconcile(C):
    """收盘对账：以账户 position 唯一真相校准专属资金池。
    买入: 撤销估算扣减，按实际成交金额/数量回写持仓。
    卖出: 撤销估算回笼，按实际成交金额回笼；部分成交恢复剩余持仓。"""
    global _cash, _holdings, _today_orders, _pending_orders, _last_reconcile_date
    try:
        if not _today_orders:
            return
        try:
            deals = get_trade_detail_data(ACCOUNT_ID, "STOCK", "deal") or []
        except Exception:
            deals = []
        traded = {}
        for d in deals:
            code = _norm_code(getattr(d, "m_strSecurityCode", "") or getattr(d, "m_strInstrumentID", ""))
            direction = str(getattr(d, "m_strOptName", "") or "")
            remark = str(getattr(d, "m_strRemark", "") or "")
            vol = float(getattr(d, "m_nVolume", 0) or 0)
            price = float(getattr(d, "m_nPrice", 0) or 0)
            if not code or vol <= 0 or price <= 0:
                continue
            if remark.find("P13H529") < 0:
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
                    _cash += od["amount"] * od["price"]
                if tr and tr["buy_vol"] > 0:
                    _cash -= tr["buy_amt"]
                    if code not in _holdings:
                        _holdings[code] = {}
                    _holdings[code]["volume"] = int(tr["buy_vol"])
                    _holdings[code]["cost"] = tr["buy_amt"] / tr["buy_vol"]
                    _holdings[code]["peak_close"] = _holdings[code]["cost"]
                    _holdings[code]["entry_date"] = od.get("entry_date", "")
                    _log("[P13 reconcile] %s 买入校准 成交=%.0f股 均价=%.3f 现金=%.0f"
                         % (code, tr["buy_vol"], _holdings[code]["cost"], _cash), C)
                elif not rolled:
                    pos = _get_acct_position(code)
                    if pos is not None and pos is not _ACCT_QUERY_FAIL:
                        acct_vol = float(getattr(pos, "m_nVolume", 0) or 0)
                        if acct_vol > 0:
                            _cash -= od["amount"] * od["price"]
                            _holdings[code] = {
                                "volume": int(min(acct_vol, od["amount"])),
                                "cost": od.get("price", 0) or 0,
                                "peak_close": od.get("price", 0) or 0,
                                "entry_date": od.get("entry_date", ""),
                                "hold_days": 0,
                            }
                            _log("[P13 reconcile] %s 买入成交(账户持仓兜底,min封顶) 持仓=%.0f股(账户%.0f)"
                                 % (code, min(acct_vol, od["amount"]), acct_vol), C)
                            continue
                    if pos is _ACCT_QUERY_FAIL:
                        _log("[P13 reconcile] %s 买入对账兜底查询失败，保守保留持仓" % code, C)
                        continue
                    _holdings.pop(code, None)
                    _log("[P13 reconcile] %s 买入未成交（账户无货）撤销虚拟持仓" % code, C)
            elif od["dir"] == "sell":
                if not rolled:
                    _cash -= od["amount"] * od["price"]
                if tr and tr["sell_vol"] > 0:
                    _cash += tr["sell_amt"]
                    remain = od["amount"] - tr["sell_vol"]
                    if remain > 0:
                        _holdings[code] = {
                            "volume": int(remain),
                            "cost": od.get("entry_price", 0),
                            "peak_close": od.get("entry_price", 0),
                            "entry_date": od.get("entry_date", ""),
                            "hold_days": 0,
                        }
                        _log("[P13 reconcile] %s 卖出部分成交 剩余=%.0f股" % (code, remain), C)
                    else:
                        _holdings.pop(code, None)
                        _log("[P13 reconcile] %s 卖出全部成交，持仓清零" % code, C)
                elif not rolled:
                    pos = _get_acct_position(code)
                    acct_vol = 0
                    if pos is not None and pos is not _ACCT_QUERY_FAIL:
                        acct_vol = float(getattr(pos, "m_nVolume", 0) or 0)
                    if pos is None:
                        _cash += od["amount"] * od["price"]
                        _holdings.pop(code, None)
                        _log("[P13 reconcile] %s 卖出成交(账户无货兜底) 现金=%.0f" % (code, _cash), C)
                        continue
                    if pos is _ACCT_QUERY_FAIL:
                        _log("[P13 reconcile] %s 卖出对账兜底查询失败，保守恢复持仓" % code, C)
                    _holdings[code] = {
                        "volume": int(od["amount"]),
                        "cost": od.get("entry_price", 0),
                        "peak_close": od.get("entry_price", 0),
                        "entry_date": od.get("entry_date", ""),
                        "hold_days": 0,
                    }
                    _log("[P13 reconcile] %s 卖出未成交（账户仍有货）恢复持仓" % code, C)
        _today_orders = {}
        _pending_orders = {}
        _last_reconcile_date = _get_market_time(C).strftime("%Y-%m-%d")
        _log("[P13 reconcile] 对账完成 持仓=%d 现金=%.0f" % (len(_holdings), _cash), C)
    except Exception as e:
        _log("[P13 reconcile error] %s" % str(e), C)


def _export_daily_csv(C, date_str):
    """每日成交/持仓导出 CSV 到 D:/QMT_POOL（QMT 端当日复核用）。"""
    try:
        src = TRADE_LOG_FILE
        dst = os.path.join(DATA_DIR, "p13_huang529_trades_%s.csv" % date_str)
        if os.path.exists(src):
            import shutil
            shutil.copyfile(src, dst)
        pos_path = os.path.join(DATA_DIR, "p13_huang529_holdings_%s.csv" % date_str)
        with open(pos_path, "w", encoding="utf-8") as f:
            f.write("ts_code,volume,cost,entry_date,peak_close,hold_days\n")
            for code, info in _holdings.items():
                f.write("%s,%d,%.3f,%s,%.3f,%d\n" % (
                    code, info.get("volume", 0), info.get("cost", 0),
                    info.get("entry_date", ""), info.get("peak_close", 0),
                    info.get("hold_days", 0)))
        _log("[P13 export] 成交/持仓 CSV 已导出 %s" % date_str, C)
    except Exception as e:
        _log("[P13 export error] %s" % str(e), C)


# ============ 每日决策（09:35 窗口，T-1 信号） ============
def _decision(C, now):
    """先卖后买：卖出评估（止损/trail/到期，T+1 保护）→ 读信号补槽买入。"""
    global _cash, _holdings, _last_decision_date
    today_str = now.strftime("%Y%m%d")
    if _last_decision_date == today_str:
        return
    _last_decision_date = today_str

    # 1) 卖出评估（收盘口径：价格用当前价，peak_close 先更新）
    sells = []
    for code, info in list(_holdings.items()):
        if code in _pending_orders:
            continue
        price = _get_price(C, code, "close")
        if price is None or price <= 0:
            continue
        cost = info.get("cost", 0) or 0
        if cost <= 0:
            continue
        pnl = (price - cost) / cost
        # 更新持有期最高收盘
        if price > info.get("peak_close", cost):
            info["peak_close"] = price
        # T+1 保护：当日买入不卖
        try:
            if info.get("entry_date", "") == today_str:
                continue
        except Exception:
            pass
        reason = None
        if pnl <= STOP_LOSS:
            reason = "stop_loss %.1f%%" % (pnl * 100)
        if reason is None and TRAILING_STOP is not None:
            peak = info.get("peak_close", cost)
            if peak > 0 and (price - peak) / peak <= -TRAILING_STOP:
                reason = "trailing_stop %.1f%%" % ((price - peak) / peak * 100)
        if reason is None:
            info["hold_days"] = info.get("hold_days", 0) + 1
            if info["hold_days"] >= MAX_HOLDING_DAYS:
                reason = "holding_expired %d天" % info["hold_days"]
        if reason is not None:
            sells.append((code, reason))

    # 2) 执行卖出（跌停/停牌进暂缓队列）
    for code, reason in sells:
        if _is_limit_down(C, code):
            if code not in _suspended_sells:
                _suspended_sells.append(code)
                _log("[P13 sell suspended] %s 跌停，暂缓卖出" % code, C)
            continue
        if _is_suspended(C, code):
            if code not in _suspended_sells:
                _suspended_sells.append(code)
                _log("[P13 sell suspended] %s 停牌，暂缓卖出" % code, C)
            continue
        if code in _suspended_sells:
            _suspended_sells.remove(code)
        _execute_sell(C, code, reason)

    # 3) 暂缓队列解封补卖
    for code in list(_suspended_sells):
        if code in _pending_orders or code not in _holdings:
            continue
        if not _is_limit_down(C, code) and not _is_suspended(C, code):
            _suspended_sells.remove(code)
            _execute_sell(C, code, "suspended_release")

    # 4) 读信号补槽（fail-open：缺失/损坏 -> 不买）
    if len(_holdings) < N_HOLD:
        signals = _load_signal_csv()
        if signals:
            held_codes = set(_holdings.keys())
            new_buy = [c for c, _, _ in signals if c not in held_codes][:N_HOLD - len(_holdings)]
            if new_buy:
                n_to_buy = N_HOLD - len(_holdings)
                port_value = _cash + _holdings_value(C)
                per_stock = min(MAX_SINGLE_PCT * port_value, _cash / n_to_buy)
                for code in new_buy:
                    if len(_holdings) >= N_HOLD:
                        break
                    if code in _pending_orders:
                        continue
                    _execute_buy(C, code)
        _save_holdings()
    _log("[P13 decision] %s 卖出%d 持仓%d 现金=%.0f" % (
        today_str, len(sells), len(_holdings), _cash), C)


# ============ QMT 生命周期 ============
def _close_task(C, now):
    """收盘任务：对账 + 导出 + 保存状态。init 末尾与 handlebar 联动防重复。"""
    global _last_close_task_date
    today_str = now.strftime("%Y-%m-%d")
    if _last_close_task_date == today_str:
        return
    _last_close_task_date = today_str
    try:
        if _today_orders:
            _reconcile(C)
    except Exception as e:
        _log("[P13 close error] %s" % str(e), C)
    _export_daily_csv(C, now.strftime("%Y%m%d"))
    _save_holdings()


def init(C):
    global _inited, _cash
    _load_config()
    _load_holdings()
    print("[P13] =============================================")
    print("[P13] 黄氏529主升浪 QMT策略 初始化... build=%s" % BUILD_TAG)
    print("[P13] =============================================")
    print("[P13] 账号=%s 本金=%.0f N_HOLD=%d stop_loss=%.2f trail=%s max_hold=%d天" % (
        ACCOUNT_ID, CAPITAL_BASE, N_HOLD, STOP_LOSS, TRAILING_STOP, MAX_HOLDING_DAYS))
    print("[P13] 信号源=%s/%s 账本=%s" % (SIGNAL_DIR, SIGNAL_PREFIX, HOLDINGS_FILE))
    print("[P13] remark=%s（防串账户）" % REMARK)
    _inited = True
    # 启动时若已过收盘，执行一次收盘任务（防 15:00 后 handlebar 不再触发的空窗）
    try:
        now = _get_market_time(C)
        if now.hour >= 15:
            _close_task(C, now)
    except Exception:
        pass


def after_init(C):
    pass


def handlebar(C):
    global _last_hb_min
    if not _inited:
        return
    now = _get_market_time(C)
    today_str = now.strftime("%Y%m%d")
    current_hour = now.hour
    current_minute = now.minute

    # 心跳日志（每5分钟，确认 handlebar 在跑）
    log_min = current_hour * 100 + current_minute
    if log_min != _last_hb_min and log_min % 5 == 0:
        _last_hb_min = log_min
        _log("[P13 hb] 心跳 持仓=%d 现金=%.0f nav=%.3f" % (len(_holdings), _cash, _calc_nav(C)), C)

    # 交易时段（连续竞价）内：挂单巡检
    _h, _m = current_hour, current_minute
    is_trading_time = ((_h == 9 and _m >= 30) or (_h == 10) or (_h == 11 and _m <= 30)
                       or (_h == 13) or (_h == 14 and _m <= 56))
    if is_trading_time:
        _check_pending_orders(C)

    # 09:35 决策窗口：先卖后买（当日只执行一次）
    if _h == 9 and 35 <= _m <= 45 and _last_decision_date != today_str:
        try:
            _decision(C, now)
        except Exception as e:
            _log("[P13 decision error] %s" % str(e), C)

    # 收盘任务：14:50 后 + 15:00
    if (_h == 14 and _m >= 50) or (_h == 15 and _m == 0):
        _close_task(C, now)


def exit(C):
    try:
        now = _get_market_time(C)
        if _today_orders:
            _reconcile(C)
    except Exception:
        pass
    _save_holdings()
    _log("[P13] 策略退出，持仓已保存")