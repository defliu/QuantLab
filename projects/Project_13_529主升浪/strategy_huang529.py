# coding=utf-8
"""黄氏529主升浪精选 —— QMT 单文件策略（本地预计算信号，QMT 只读 CSV）

选股: 黄氏529版公式信号（本地 D:/Python311 预计算，PIT 安全）按 ATR% 升序 top16,
      每日 T-1 收盘后写入 D:/QMT_POOL/529_signal_top16_YYYYMMDD.csv。
      策略 T 日 09:35 读最新信号 CSV（日期 < 今日），剔已持有，按 rank 补槽至 12 只，
      每只 min(total_asset/12, cash/当日新买数)；涨停不追、T+1、整数手。
卖出: 止损 -12%（收盘口径）/ 移动止损 trail15（默认关，T1 判定未启用）/ 到期 ≥60 交易日。
fail-open: 当日信号 CSV 缺失/损坏 -> 当日不买入（现金闲置），日志高亮 WARN，
           持仓管理（卖出）不受影响照常执行。
风控: 卖出优先级高于买入；先卖后买；跌停/停牌卖出进暂缓队列，解封自动补卖。
账本: D:/QMT_POOL/config/p13_huang529_holdings.json（code/entry_date/cost/peak_close/hold_days/volume）。
路径约定: 根目录 D:/QMT_POOL 只放信号 CSV；配置/账本/NAV/交易日志/策略日志/导出
      一律在 D:/QMT_POOL/config（DATA_DIR）。_DEFAULT_CONFIG、yaml、资金分配表
      config_file 三处必须一致，否则 yaml 读不到时状态会掉进根目录。
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
# 撤单确认态：cancel() 返回成功仅代表撤单指令已送达柜台，不等于已撤。
# 必须反查委托终态确认旧单死透后才允许重下新单，否则旧单仍挂在场内会叠出
# 第二份活单（买入顶破单票仓位上限、卖出可能超卖）。口径对齐 Project_16 实证。
CANCEL_CONFIRM_MAX_POLL = 3
CANCEL_RESEND_MAX = 2

# 构建版本标记：build.py 每次构建时自动替换为时间戳（YYYYmmdd-HHMMSS）
BUILD_TAG = "dev"

# ============ 全局状态 ============
_cash = CAPITAL_BASE
_holdings = {}              # code -> {volume, cost, entry_date, peak_close, hold_days, orphan}
_pending_orders = {}        # code -> {type, order_id, amount, original_amount, price, time, retries}
# pending 撤单三态字段契约（三者语义互不等价，运维排查必须区分）：
#   cancel_requested   —— 「已进入撤单跟踪流程」的闸门标志。True 表示这张单已被
#                        扣在确认态循环里，禁止走任何重下/回滚分支（_check_pending_orders
#                        见到它就直接转 _confirm_cancel）。注意：它**不代表撤单指令已发出**
#                        —— 查不到原单、撤单接口失败时同样置 True（因为仍需跟踪），
#                        指令是否真的送达柜台要看 cancel_sent。
#   cancel_sent        —— 「最近一次 cancel() 调用是否送达柜台」。True=已发，等终态；
#                        False=不可撤/接口异常，指令压根没发出去。纯可观测字段，
#                        绝不参与是否重下/回滚的判定（终态未确认就一律不重下）。
#   cancel_unconfirmed —— 「隔离态」：终态无法证明（查不到原单、轮询封顶、撤单未发出）。
#                        收盘对账与运维据此人工核查；新 attempt 成功后必须清除，
#                        否则陈旧隔离标志会误导次日排查。
_CANCEL_FIELDS = ("cancel_requested", "cancel_action", "cancel_poll",
                  "cancel_resend", "cancel_unconfirmed", "cancel_sent")
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
        "holdings_file": "D:/QMT_POOL/config/p13_huang529_holdings.json",
        "nav_file": "D:/QMT_POOL/config/p13_huang529_nav.json",
        "trade_log_file": "D:/QMT_POOL/config/p13_huang529_trade_log.csv",
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


# 委托标识的命名空间定义：(命名空间, 属性别名列表)
#   oid   —— 客户端委托号（passorder 异步返回值来源、deal 的 m_nOrderID）
#   sysid —— 柜台合同号（cancel() 首选标识）
# 两套编号是不同序列，禁止跨命名空间裸字符串比较。
_ORDER_ID_NS = (
    ("oid", ("m_nOrderID", "m_strOrderID", "m_nOrderId")),
    ("sysid", ("m_strOrderSysID", "m_strSysid")),
)


def _order_ref(v):
    """委托标识规范化：int/str 混用统一成字符串，空值统一成空串。

    为什么不能直接拿 passorder 返回值当单号用：passorder() 是**异步**接口，
    QMT 客户端侧正常返回就是 0/None（不是委托号），真实单号只能靠
    get_trade_detail_data 反查。所以这里把 0/None/"" 统一成空串，
    让「拿不到精确单号」退化到排序口径，而不是拿 0 去匹配订单。
    """
    if v is None:
        return ""
    try:
        s = str(v).strip()
    except Exception:
        return ""
    if s in ("", "0", "-1", "None", "null", "Null"):
        return ""
    return s


def _order_id_keys(order):
    """按**命名空间**取出委托的全部标识：(ns, value) 列表。

    QMT 委托有两个互不同属的标识体系：
      · oid   —— 客户端委托号 m_nOrderID（passorder 异步接口的返回值来源）
      · sysid —— 柜台合同号 m_strOrderSysID（cancel() 首选标识）
    两套编号是不同序列，值可能长得一样但含义不同，绝不能跨命名空间比字符串。
    早期实现只取一个键（oid 优先），会出现「委托只回填了 sysid、而 deal 两个
    字段都有」时按 oid 取键比不上的漏匹配，撤单瞬间成交反而认不到。
    """
    keys = []
    for _ns, _attrs in _ORDER_ID_NS:
        for _a in _attrs:
            v = _order_ref(getattr(order, _a, None))
            if v and ( _ns, v) not in keys:
                keys.append((_ns, v))
    return keys


def _order_id_key(order):
    """取委托对象的精确单号键（m_nOrderID 优先，柜台合同号备用）。"""
    keys = _order_id_keys(order)
    for ns, v in keys:
        if ns == "oid":
            return v
    return keys[0][1] if keys else ""


def _order_refs_match(a_keys, ns, value):
    """按命名空间判断某一标识是否命中（禁止跨命名空间裸字符串比较）。"""
    if not value:
        return False
    for k_ns, k_v in a_keys:
        if k_ns == ns and k_v == value:
            return True
    return False


def _order_seq(order):
    """委托时序键（插入时间字符串）；取不到返回空串。"""
    for attr in ("m_strInsertTime", "m_strInsertDateTime", "m_strOrderTime"):
        v = str(getattr(order, attr, "") or "").strip()
        if v:
            return v
    return ""


def _newest_order(cand):
    """从候选集里挑「最新一条」。

    cand 由 _find_order 反向遍历构造，天然「后进先出」（最新 attempt 在前）。
    这里再用插入时间做显式比较：同代码同方向存在多单（撤单重下会堆积多份
    历史 attempt）时，当前 attempt 必然是最后一条，只有按时间取最新才能
    稳定命中本次委托，不会把已撤的上一轮 attempt 当成当前单去校准成交量
    ——那是 attempt 口径串味的根源。
    """
    best = None
    best_seq = ""
    for o in cand:
        s = _order_seq(o)
        if best is None or (s and s > best_seq):
            best = o
            best_seq = s
    return best


def _find_order(orders, code, direction, oid="", sysid=""):
    """在委托列表中定位「本次 attempt」的委托对象，三层匹配。

    为什么要分层而不是一把启发式：同代码同方向出现多份委托（撤单重下、
    或与同账户其他策略撞票）时，模糊匹配会把 A attempt 的成交量算到
    B attempt 上，剩余量凭空蒸发、账本回滚凭空造钱。

    L1 精确单号：pending 持有本 attempt 单号时，唯一事实源，命中即返回；
                 拿不到单号（passorder 异步返回 0/None）时自然跳过。
    L2 remark + 最新：同代码同方向多单时取插入时间最新的一条——本 attempt
                 必然最新，语义等价于精确匹配，且不依赖 passorder 返回值。
    L3 旧 pending 兼容：无单号、remark 不可用（模拟端可能丢 remark）时，
                 唯一候选才认；多候选返回 None（歧义，宁可隔离不撤他人单）。

    红线：remark 只作优先级信号，不作硬过滤；唯一候选即使 remark 空也返回。
    """
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
    # L1 精确单号优先（按命名空间比较：oid 只比 oid，sysid 只比 sysid）
    want_oid = _order_ref(oid)
    want_sys = _order_ref(sysid)
    if want_oid:
        for o in cand:
            if _order_refs_match(_order_id_keys(o), "oid", want_oid):
                return o
    if want_sys:
        for o in cand:
            if _order_refs_match(_order_id_keys(o), "sysid", want_sys):
                return o
    # L2 本策略 remark 命中，取最新一条
    hit = [o for o in cand
           if str(getattr(o, "m_strRemark", "") or "").find("P13H529") >= 0]
    if hit:
        return _newest_order(hit)
    # L3 兼容：唯一候选才认
    if len(cand) == 1:
        return cand[0]
    return None


def _order_sysid(order):
    """取柜台合同号 m_strOrderSysID（cancel() 首选标识，m_strSysid 为兼容别名）。"""
    return str(getattr(order, "m_strOrderSysID", "")
               or getattr(order, "m_strSysid", "") or "")


def _find_pending_order(orders, code, info):
    """按 pending 定位本次 attempt 的委托对象（带精确单号优先）。

    所有撤单/校准/重下路径都必须走这个统一入口，禁止直接调 _find_order：
    漏传单号就会退化成「代码+方向」模糊匹配，同代码同方向多单时会把
    历史 attempt 的成交量算到当前 attempt 上。
    命中后顺带回填 sysid（cancel() 首选标识），供后续撤单使用。
    """
    if not orders:
        return None
    oid = info.get("attempt_order_id", "") or ""
    sysid = info.get("attempt_sysid", "") or ""
    o = _find_order(orders, code, info["type"], oid, sysid)
    if o is not None:
        s = _order_sysid(o)
        if s and s != sysid:
            info["attempt_sysid"] = s
    return o


# QMT 委托状态码（口径来自 Project_16 于 2026-09-01~09-03 模拟端实证）
_ORDER_ST_FILLED = 56          # 全成
_ORDER_ST_CANCELED = (53, 54)  # 已撤 / 部撤
_ORDER_ST_REJECTED = 57        # 废单


def _order_status(order):
    """取 m_nOrderStatus；字段缺失或不可转 int 时返回 None（未知≠活跃，保守当活跃处理）。"""
    try:
        v = getattr(order, "m_nOrderStatus", None)
    except Exception:
        return None
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        return None


def _order_traded(order):
    """取累计成交量 m_nVolumeTraded；取不到按 0 处理（0 只会导致少认成交，不会虚增）。"""
    try:
        return int(float(getattr(order, "m_nVolumeTraded", 0) or 0))
    except Exception:
        return 0


def _order_is_active(order):
    """委托是否仍在场内（未进终态）。

    终态：56 全成 / 53,54 已撤 / 57 废单。
    55 部成与 2/48/49/50 未报申报等均为活跃态——红线：55 绝不可当终态，
    否则会把仍在成交的真单当已撤收尾，造成真单孤儿。
    状态码取不到(None)时保守返回 True：宁可不重下等收盘对账，也不叠单。
    """
    if order is None:
        return False
    st = _order_status(order)
    if st is None:
        return True
    if st == _ORDER_ST_FILLED or st in _ORDER_ST_CANCELED or st == _ORDER_ST_REJECTED:
        return False
    return True


def _orig_amount(info):
    """委托原始股数（缺失时兜底当前剩余量）。"""
    try:
        return int(info.get("original_amount", info.get("amount", 0)) or 0)
    except Exception:
        return 0


def _deal_traded_for_order(code, order):
    """按委托单号聚合当日成交明细量（deal），用于校准委托快照滞后。

    口径要点：
      · deal 是柜台唯一真相；m_nVolumeTraded 只是快照，可能落后于真实成交。
      · 必须按**委托单号**聚合，不能按 code+方向——同票同方向一天内可能有多轮
        attempt，按代码聚合会把历史 attempt 的成交算到当前 attempt 上。
      · 取不到单号（QMT 未回填）时返回 0，调用方按"无 deal 证据"处理。
    """
    if not code or order is None:
        return 0
    o_keys = _order_id_keys(order)
    if not o_keys:
        return 0
    try:
        deals = get_trade_detail_data(ACCOUNT_ID, "STOCK", "deal") or []
    except Exception:
        return 0
    vol = 0.0
    for d in deals:
        dcode = _norm_code(getattr(d, "m_strSecurityCode", "")
                           or getattr(d, "m_strInstrumentID", ""))
        if dcode != _norm_code(code):
            continue
        # 命名空间感知匹配：只在同命名空间求交集。委托与 deal 至少共享一个
        # 同名字段（委托有 oid+sysid / deal 只有 oid，或双方都只有 sysid）才认定
        # 同一笔；oid 与 sysid 数值碰巧相同不互认，避免跨序列误聚成交量。
        d_keys = _order_id_keys(d)
        if not d_keys:
            continue
        if not any(_order_refs_match(o_keys, ns, v) for ns, v in d_keys):
            continue
        try:
            v = float(getattr(d, "m_nVolume", 0) or 0)
        except Exception:
            continue
        if v > 0:
            vol += v
    return int(vol)


def _calibrate_filled(info, order, code=""):
    """用 QMT 反查校准 pending 的成交量（唯一口径，覆盖写回，绝不做增量减法）。

    为什么必须拆 attempt：m_nVolumeTraded 是「本次委托」的累计成交量，
    既不是本 bar 增量，也不含历史尝试的成交。原实现把它当增量减
    （amount -= traded），两次轮询都读到 400 就会把 1000 减成 600 再减成 200，
    剩余量凭空蒸发；重下后新旧委托的成交量还会混算。

    拆成两个正交口径后互不干扰：
      attempt_amount 本次委托下的量（每次 _place_order 后快照）
      attempt_traded 本次委托已成交（QMT 累计成交，覆盖写回）
      filled_before   历次尝试累计已成交（撤单重下时结转）
      filled_amount = filled_before + attempt_traded  → 账本口径「总成交」
      amount = attempt_amount - attempt_traded        → 本次委托「剩余可重下量」

    deal 校准：委托快照 m_nVolumeTraded 可能滞后于柜台成交明细（撤单瞬间尤其
    容易读到偏小值）。少认成交会让回滚多退现金、重下超出剩余量，因此用 deal
    按单号聚合的成交量**只上调 attempt_traded，绝不下调**——快照回退到更小值时
    宁可保守按已有值持有，等 deal 侧或后续快照追平。

    filled_amount 是账本回滚的唯一依据（_do_rollback_ledger 只认它），
    所以任何要回滚/重下的路径都必须先调本函数刷新，禁止就地自行推算。
    """
    attempt_amount = int(info.get("attempt_amount", 0) or 0)
    if attempt_amount <= 0:
        # 旧 pending（上线前落盘）没有 attempt_amount：首次校准时用当前剩余量兜底，
        # 并必须持久化——否则第二次校准会把「已扣过的剩余量」当成新一轮委托量，
        # 同一笔 400 成交被扣两次，剩余量凭空蒸发。
        attempt_amount = int(info.get("amount", 0) or 0)
        if attempt_amount <= 0:
            return int(info.get("filled_amount", 0) or 0)
        info["attempt_amount"] = attempt_amount
        info.setdefault("attempt_traded", 0)
        info.setdefault("filled_before", 0)
    traded = _order_traded(order)
    if traded < 0:
        traded = 0
    prev = int(info.get("attempt_traded", 0) or 0)
    if traded < prev:
        # 快照回退：委托反查偶发读到更小的累计成交（如换批次快照），按大值持有
        traded = prev
    if traded > attempt_amount:
        # QMT 累计成交理论上不会超过委托量，异常值按满额处理（保守：不虚增现金）
        traded = attempt_amount
    if code:
        deal_vol = _deal_traded_for_order(code, order)
        if deal_vol > traded:
            if deal_vol > attempt_amount:
                deal_vol = attempt_amount
            _log("[P13 pending] %s 委托快照成交=%d 落后于柜台deal=%d，按deal上调"
                 % (code, traded, deal_vol))
            traded = deal_vol
    filled_before = int(info.get("filled_before", 0) or 0)
    info["attempt_traded"] = traded
    info["filled_amount"] = filled_before + traded
    info["amount"] = attempt_amount - traded
    return info["filled_amount"]


def _begin_attempt(info, amount):
    """重下前结转口径：把本次 attempt 的成交累进 filled_before，开启新一轮。

    只在确认旧单已进终态（不会继续成交）后调用——否则 filled_before 会被
    重复累加同一笔成交，导致 filled_amount 虚高、回滚凭空造钱。
    """
    traded = int(info.get("attempt_traded", 0) or 0)
    if traded < 0:
        traded = 0
    info["filled_before"] = int(info.get("filled_before", 0) or 0) + traded
    info["attempt_amount"] = int(amount)
    info["attempt_traded"] = 0
    # 新 attempt 必须清空上一轮的单号，否则 _find_order 会用旧单号命中
    # 已撤的历史委托，把旧 attempt 的成交量算到新 attempt 上。
    info["attempt_order_id"] = ""
    info["attempt_sysid"] = ""
    # 同时清空上一轮撤单状态：cancel_sent=True 是「旧单已发撤单」的事实，
    # 新 attempt 根本没发过撤单，留着会让运维误以为新单也撤过；
    # cancel_unconfirmed 同理是旧 attempt 的隔离结论，不能继承到新单。
    for _f in _CANCEL_FIELDS:
        info.pop(_f, None)


def _cancel_order_by(C, code, order_id, sysid=""):
    """官方撤单 cancel()。

    禁止用 passorder(24,1101,...) 撤单：opType 24 是股票卖出，QMT 按卖出解析，
    会导致撤单静默失效甚至误下单（Project_16 于 2026-09-01~09-03 实证推翻）。
    cancel(orderId, accountId, accountType, C) 是官方撤单唯一入口。
    顺序：can_cancel_order 预检 -> cancel() 发指令 -> 由调用方反查终态确认
    （返回成功仅代表指令送达柜台，不等于已撤）。
    撤单标识优先用 sysid（柜台合同号），缺失才退回 order_id。
    """
    oid = sysid or order_id
    if not oid:
        _log("[P13 cancel] %s 无有效委托号，跳过撤单" % code, C)
        return False
    try:
        if not can_cancel_order(oid, ACCOUNT_ID, "stock"):
            _log("[P13 cancel] %s %s 当前不可撤（可能已成交/已撤/已终结）" % (code, oid), C)
            return False
    except Exception:
        # 预检接口不可用时不阻断：仍尝试 cancel，靠后续反查终态兜底
        pass
    try:
        r = cancel(oid, ACCOUNT_ID, "stock", C)
        _log("[P13 cancel] 撤单指令已发 %s oid=%s 返回=%s（仅代表送达，待反查终态）"
             % (code, oid, r), C)
        return True
    except Exception as e:
        _log("[P13 cancel error] %s oid=%s: %s" % (code, oid, str(e)), C)
        return False


def _cancel_pending_orders(C):
    """撤销所有待成交订单（换仓前清理）。

    红线：cancel() 返回成功仅代表撤单指令送达柜台，旧单可能仍在场内。
    因此这里只发撤单并置 cancel_action="drop" 进入确认态，绝不清空
    _pending_orders——清空会让后续路径对同一票重新下单，叠出第二份活单。
    真正移除由 _confirm_cancel 终态确认后完成。

    可观测性：cancel_sent 记录撤单指令**是否真的发出**。
      True  = 指令已送达，等终态确认（正常）
      False = 不可撤/接口异常，指令压根没发（cancel_unconfirmed=True 隔离）
    两者都绝不在此处清 pending，区别只在于日志与收盘对账的排查线索。
    """
    orders = _query_orders()
    for code, info in list(_pending_orders.items()):
        order = _find_pending_order(orders, code, info) if orders else None
        if order is None:
            _log("[P13 cancel] %s 未反查到委托，置隔离态等收盘对账（不重下不记账）" % code, C)
            info["cancel_action"] = "drop"
            info["cancel_requested"] = True
            info["cancel_unconfirmed"] = True
            info["cancel_sent"] = False
            _pending_orders[code] = info
            continue
        sent = _cancel_order_by(C, code, getattr(order, "m_nOrderID", None),
                                _order_sysid(order))
        if not sent:
            _log("[P13 cancel] %s 撤单指令未发出（不可撤或接口异常），置隔离态"
                 "等收盘对账（不重下不记账）" % code, C)
            info["cancel_unconfirmed"] = True
        else:
            info.pop("cancel_unconfirmed", None)
        info["cancel_action"] = "drop"
        info["cancel_requested"] = True
        info["cancel_sent"] = bool(sent)
        info["cancel_poll"] = 0
        info["cancel_resend"] = 0
        _pending_orders[code] = info


# ============ 挂单巡检 / 回滚 ============
def _do_rollback_ledger(C, code, info):
    """真正回滚虚拟账本（估算记账的逆操作）。

    只能在确认 QMT 端旧单已进终态（已撤/废单）后调用——若旧单仍活跃或
    已成交，此时回滚会让账本与账户两头对不上（钱退了但货被吃掉）。

    红线：必须区分「已成交」与「未成交」两段。
      买入：只有未成交段该退钱；已成交段是真持仓，必须留在账本里。
            若按原始量全额退现等于凭空造钱（1000 股委托成交 400 股后撤单，
            全额退 1000 股的钱却丢掉那 400 股持仓）→ 现金虚增、仓位漏记。
      卖出：只有已成交段算真实回笼；未成交段该扣回，恢复对应持仓。
    """
    global _cash, _holdings, _today_orders, _suspended_sells
    try:
        orig = _orig_amount(info)
        filled = int(info.get("filled_amount", 0) or 0)
        if filled < 0:
            filled = 0
        if filled > orig:
            filled = orig
        # 未成交段直接取「本次委托剩余量」：调用方已确认旧单进终态，
        # amount 即为最终剩余，不再用 orig - filled 反推（跨 attempt 反推会漂移）。
        remain = int(info.get("amount", 0) or 0)
        if remain < 0:
            remain = 0
        if filled + remain > orig:
            remain = orig - filled
        if remain < 0:
            remain = 0
        if info["type"] == "buy":
            if filled > 0:
                # 已成交段是真持仓：保留并按估算价落账（真实成交价由收盘对账校准）
                _holdings[code] = {
                    "volume": int(filled),
                    "cost": info.get("price", 0) or 0,
                    "entry_date": info.get("entry_date", "")
                    or datetime.now().strftime("%Y%m%d"),
                    "peak_close": info.get("price", 0) or 0,
                    "hold_days": 0,
                }
                _log("[P13 pending] %s 买入部分成交后放弃，保留已成交 %d股(真实价待对账)"
                     % (code, filled), C)
            else:
                _holdings.pop(code, None)
            # 只退未成交段的估算占用
            _cash += remain * info["price"]
            _log("[P13 pending] %s 买入放弃(重试%d次)，退未成交 %d股 现金=%.0f"
                 % (code, info.get("retries", 0), remain, _cash), C)
        else:
            if remain > 0:
                if code not in _holdings:
                    _holdings[code] = {
                        "volume": int(remain),
                        "cost": info.get("entry_price", 0) or info["price"],
                        "entry_date": info.get("entry_date", "") or datetime.now().strftime("%Y%m%d"),
                        "peak_close": info.get("entry_price", 0) or info["price"],
                        "hold_days": 0,
                    }
            # 只扣回已成交段（真实回笼），未成交段的钱本来就没动
            _cash -= filled * info["price"]
            try:
                if _is_limit_down(C, code) or _is_suspended(C, code):
                    if code not in _suspended_sells:
                        _suspended_sells.append(code)
                        _log("[P13 pending] %s 卖出放弃但跌停/停牌，进暂缓队列" % code, C)
            except Exception:
                pass
            _log("[P13 pending] %s 卖出放弃(重试%d次)，已成交%d股 恢复持仓%d股 现金=%.0f"
                 % (code, info.get("retries", 0), filled, remain, _cash), C)
        if code in _today_orders:
            _today_orders[code]["rolled_back"] = True
            # 记录回滚时的成交段，供收盘对账只校准「估算价 vs 真实成交价」的差额。
            # 缺了它，对账会把整笔真实成交额再冲一次现金（部分成交回滚后重复扣钱）。
            _today_orders[code]["rollback_filled"] = int(filled)
            _today_orders[code]["rollback_price"] = info.get("price", 0) or 0
    except Exception as e:
        _log("[P13 rollback error] %s: %s" % (code, str(e)), C)


def _rollback_pending(C, code, info):
    """重试耗尽/跨日残留：回滚估算记账。

    唯一入口，**没有 defer 旁路**。原 defer=True 分支允许调用方在未确认
    QMT 终态时直接回滚账本，等于绕过「钱退了但货还在成交」这条安全红线；
    现实中不需要这种捷径——查不到单就隔离等收盘对账，比凭猜测改账本安全。

    三分支：
      查不到原单  -> 无法证明已撤，置隔离态等收盘对账（不记账）
      旧单仍活跃  -> 发撤单 + cancel_action="rollback"，等终态再回滚
      旧单已终态  -> 56 全成绝不回滚（成交即事实，交给对账校准价与量）；
                     53/54/57 才按最新成交量校准后回滚账本
    ②卖出回滚后若仍跌停/停牌，进暂缓队列（解封后自动补卖）。
    """
    global _pending_orders
    orders = _query_orders()
    order = _find_pending_order(orders, code, info) if orders else None
    if order is None:
        _log("[P13 pending] %s 查不到原委托，无法证明已撤，置隔离态等收盘对账"
             "（不重下不记账）" % code, C)
        info["cancel_action"] = "rollback"
        info["cancel_requested"] = True
        info["cancel_unconfirmed"] = True
        info["cancel_sent"] = False
        _pending_orders[code] = info
        return False
    if _order_is_active(order):
        sent = _cancel_order_by(C, code, getattr(order, "m_nOrderID", None),
                                _order_sysid(order))
        info["cancel_action"] = "rollback"
        info["cancel_requested"] = True
        info["cancel_sent"] = bool(sent)
        if not sent:
            # 撤单指令压根没发出去：旧单仍在场内成交，绝不能回滚账本。
            # 仍进确认态，让 _confirm_cancel 后续重发撤单 + 轮询终态兜底。
            info["cancel_unconfirmed"] = True
            _log("[P13 pending] %s 撤单指令未发出，回滚暂缓（等终态确认，绝不记账）"
                 % code, C)
        else:
            info.pop("cancel_unconfirmed", None)
        info["cancel_poll"] = 0
        info["cancel_resend"] = 0
        _pending_orders[code] = info
        return False
    # ---- 旧单已终态 ----
    st = _order_status(order)
    if st == _ORDER_ST_FILLED:
        # 红线：全成是既成事实，此时回滚=凭空造钱（钱退回来而股票已真持有）
        _log("[P13 pending] %s 原单已全成(status=56)，不回滚，交收盘对账校准"
             % code, C)
        info["cancel_unconfirmed"] = False
        _pending_orders.pop(code, None)
        return True
    # 已撤/部撤/废单：先按 QMT 累计成交校准口径，再回滚
    _calibrate_filled(info, order, code)
    _do_rollback_ledger(C, code, info)
    return True


def _bind_attempt_ref(info, oid):
    """把本次 attempt 的精确委托号绑到 pending 上（供 _find_order 走 L1 精确匹配）。

    oid 来自 passorder 返回值。QMT 客户端侧该值常为 0/None（异步接口），
    此时 _order_ref 归一成空串，_find_order 自然退化到 L2「最新一条」口径——
    两种情况下都能唯一定位本 attempt，不会串到历史 attempt。
    """
    ref = _order_ref(oid)
    if ref:
        info["attempt_order_id"] = ref
        info["attempt_sysid"] = ""
    return ref


def _place_order(C, code, info):
    """按 info 下市价委托（重下唯一入口）。返回 passorder 返回值供 attempt 绑定。"""
    if info["type"] == "buy":
        r = passorder(23, 1101, ACCOUNT_ID, code, 5, -1, info["amount"], REMARK, 2, "", C)
    else:
        r = passorder(24, 1101, ACCOUNT_ID, code, 5, -1, info["amount"], REMARK, 2, "", C)
    return r


def _retry_pending(C, code, info):
    """撤单重下的**第一阶段**：只发撤单并进入确认态，绝不在此重下。

    cancel() 返回成功仅代表撤单指令送达柜台，旧单仍可能挂在场内；
    此刻 passorder 会叠出第二份活单——买入顶破单票仓位上限、卖出可能超卖。
    重下由 _confirm_cancel 终态确认后执行（cancel_action="retry"）。
    查不到原单 / 撤单接口失败：置隔离态，等收盘对账校准，不重下。
    """
    global _pending_orders
    try:
        order_id = None
        sysid = ""
        orders = _query_orders()
        if orders:
            order = _find_pending_order(orders, code, info)
            if order is not None:
                order_id = getattr(order, "m_nOrderID", None)
                sysid = _order_sysid(order)
        if not order_id and not sysid:
            _log("[P13 pending] %s 未反查到可撤订单，置隔离态（不重下，等收盘对账校准）" % code, C)
            info["cancel_action"] = "retry"
            info["cancel_requested"] = True
            info["cancel_unconfirmed"] = True
            info["cancel_sent"] = False
            _pending_orders[code] = info
            return False
        if not _cancel_order_by(C, code, order_id, sysid):
            _log("[P13 pending] %s 撤单指令未发出（不可撤或接口异常），隔离不重下，防叠单" % code, C)
            info["cancel_action"] = "retry"
            info["cancel_requested"] = True
            info["cancel_unconfirmed"] = True
            info["cancel_sent"] = False
            _pending_orders[code] = info
            return False
        info["cancel_action"] = "retry"
        info["cancel_requested"] = True
        info["cancel_sent"] = True
        info.pop("cancel_unconfirmed", None)
        info["cancel_poll"] = 0
        info["cancel_resend"] = 0
        _pending_orders[code] = info
        _log("[P13 pending] %s 撤单已发，进入确认态（终态确认后才重下）" % code, C)
        return True
    except Exception as e:
        _log("[P13 retry error] %s: %s" % (code, str(e)), C)
        return False


def _confirm_cancel(C, code, info, orders):
    """撤单确认态轮询：反查原委托终态，决定重下 / 回滚 / 移除。

    判定口径（Project_16 实证）：
      · 查不到原单      -> 无法证明已撤，保守隔离到收盘对账（不重下不记账）
      · 仍活跃（含 55 部成）-> 不重下；可重发撤单（封顶 CANCEL_RESEND_MAX），
                              超过封顶仍不确认则隔离，等收盘对账
      · 56 全成         -> 成交就是成交，绝不重下也绝不回滚，交给收盘对账校准
      · 53/54 已撤/部撤、57 废单 -> 旧单死透，才执行 retry（重下剩余量）
                              或 rollback（回滚账本）
    """
    global _pending_orders
    try:
        info["cancel_poll"] = info.get("cancel_poll", 0) + 1
        polls = info["cancel_poll"]
        action = info.get("cancel_action", "drop")
        order = _find_pending_order(orders, code, info) if orders else None

        if order is None:
            if polls >= CANCEL_CONFIRM_MAX_POLL:
                _log("[P13 cancel] %s 连续%d次查不到原委托，终态无法确认，"
                     "隔离到收盘对账（不重下不记账）" % (code, polls), C)
                info["cancel_unconfirmed"] = True
                _pending_orders[code] = info
                return False
            _pending_orders[code] = info
            return False

        st = _order_status(order)
        if _order_is_active(order):
            # 仍活跃：含 55 部成。部成量要先记账，否则重下会超量。
            if st == 55:
                before = info.get("amount")
                _calibrate_filled(info, order, code)
                if info["amount"] != before:
                    _log("[P13 cancel] %s 部成，本次委托剩余=%d股(总成交=%d)"
                         % (code, info["amount"], info["filled_amount"]), C)
            resend = info.get("cancel_resend", 0)
            if resend < CANCEL_RESEND_MAX:
                info["cancel_resend"] = resend + 1
                sent = _cancel_order_by(C, code, getattr(order, "m_nOrderID", None),
                                        _order_sysid(order))
                info["cancel_sent"] = bool(sent)
                if not sent:
                    # 重发失败：撤单指令没出去，旧单仍活跃，绝不重下
                    info["cancel_unconfirmed"] = True
                else:
                    # 重发成功：隔离原因已消除（指令确实送达了），清掉旧标志，
                    # 否则本轮已成功发撤单仍显示隔离，收盘排查会误判为「从没撤过」。
                    # 注意终态仍未确认，所以本轮依旧不重下。
                    info.pop("cancel_unconfirmed", None)
                _log("[P13 cancel] %s 旧单仍活跃(status=%s)，第%d次重发撤单%s，不重下"
                     % (code, st, resend + 1, "（未发出）" if not sent else ""), C)
            elif polls >= CANCEL_CONFIRM_MAX_POLL:
                _log("[P13 cancel] %s 撤单重发已达上限且仍未终态，隔离到收盘对账（不重下不记账）"
                     % code, C)
                info["cancel_unconfirmed"] = True
            _pending_orders[code] = info
            return False

        # ---- 以下为终态：旧单已死透 ----
        # 旧单不会再成交，此刻校准才是安全的（活跃期校准会与后续成交重复累加）
        _calibrate_filled(info, order, code)
        st = _order_status(order)
        if st == _ORDER_ST_FILLED:
            _log("[P13 cancel] %s 原单已全成(status=%s)，不重下不回滚，交收盘对账校准"
                 % (code, st), C)
            info["cancel_unconfirmed"] = False
            _pending_orders.pop(code, None)
            return True

        # 已撤 / 部撤 / 废单：amount 已是本次委托的真实剩余量
        if info["amount"] <= 0:
            _log("[P13 cancel] %s 原单已部撤但剩余为0（总成交=%d），移出跟踪"
                 % (code, info["filled_amount"]), C)
            _pending_orders.pop(code, None)
            return True

        if action == "retry":
            if info.get("retries", 0) >= PENDING_MAX_RETRIES:
                _log("[P13 pending] %s 撤单已确认但重试封顶，回滚账本" % code, C)
                _do_rollback_ledger(C, code, info)
                _pending_orders.pop(code, None)
                return True
            # 结转口径：旧单的成交累进 filled_before，新委托只承接剩余量。
            # _begin_attempt 已统一清空 attempt 单号与全部撤单状态字段
            # （cancel_sent/cancel_unconfirmed 等），此处不再逐个 pop。
            _begin_attempt(info, info["amount"])
            _bind_attempt_ref(info, _place_order(C, code, info))
            info["retries"] = info.get("retries", 0) + 1
            info["time"] = _get_market_time(C)
            _pending_orders[code] = info
            _log("[P13 pending] %s 撤单终态已确认，%s重下 数量=%d retry=%d"
                 % (code, info["type"], info["amount"], info["retries"]), C)
            return True
        if action == "rollback":
            _do_rollback_ledger(C, code, info)
            _pending_orders.pop(code, None)
            _log("[P13 pending] %s 撤单终态已确认，回滚账本并移出" % code, C)
            return True
        # drop：仅清理跟踪，不动账本
        _pending_orders.pop(code, None)
        _log("[P13 cancel] %s 撤单终态已确认(status=%s)，移出跟踪" % (code, st), C)
        return True
    except Exception as e:
        _log("[P13 confirm_cancel error] %s: %s" % (code, str(e)), C)
        return False


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
        # 撤单确认态优先：处于确认态的单禁止走任何重下/回滚分支，
        # 只能靠反查终态推进（_confirm_cancel 内部决定重下/回滚/隔离）。
        if info.get("cancel_requested"):
            _confirm_cancel(C, code, info, orders)
            continue
        try:
            elapsed_min = (now - info["time"]).total_seconds() / 60.0
        except Exception:
            elapsed_min = 0
        try:
            same_day = info["time"].strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d")
        except Exception:
            same_day = False
        if not same_day:
            # 跨日残留：QMT 模拟端隔日查不到单，_rollback_pending 会置隔离态；
            # 只有确认态被 _confirm_cancel 收尾后才移出，防止次日仍挂着活单。
            if _rollback_pending(C, code, info):
                _pending_orders.pop(code, None)
                _log("[P13 pending] %s 跨日残留，回滚完成并移出" % code, C)
            continue
        if elapsed_min < PENDING_TIMEOUT_MIN:
            continue
        order = _find_pending_order(orders, code, info) if orders else None
        if order is not None:
            # 用唯一口径校准（覆盖写回，不做 amount -= traded 的增量减法：
            # m_nVolumeTraded 是本次委托累计量，连查两次都读到 400 时增量法
            # 会把 1000 减成 600 再减成 200，剩余量凭空蒸发）
            before = info.get("amount")
            _calibrate_filled(info, order, code)
            if info["amount"] <= 0:
                _pending_orders.pop(code, None)
                _log("[P13 pending] %s 本次委托已全部成交(累计%d股)，移出跟踪"
                     % (code, info["filled_amount"]), C)
                continue
            if info["amount"] != before:
                _log("[P13 pending] %s 部分成交 累计%d股，剩余 %d股"
                     % (code, info["filled_amount"], info["amount"]), C)
        else:
            _log("[P13 pending] %s 未找到 %s 委托，跳过重下（等收盘对账校准）" % (code, info["type"]), C)
            continue
        if elapsed_min < RETRY_COOLDOWN_MIN:
            continue
        if info.get("retries", 0) >= PENDING_MAX_RETRIES:
            # 同理：回滚若进入确认态，不得立即 pop
            if _rollback_pending(C, code, info):
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
        r_buy = passorder(23, 1101, ACCOUNT_ID, code, 5, -1, amount, REMARK, 2, "", C)
        _pending_orders[code] = {
            "type": "buy",
            "amount": amount,
            "original_amount": amount,
            "attempt_amount": amount,
            "attempt_traded": 0,
            "filled_before": 0,
            "filled_amount": 0,
            "attempt_order_id": "",
            "attempt_sysid": "",
            "price": price,
            "time": _get_market_time(C),
            "retries": 0,
        }
        _bind_attempt_ref(_pending_orders[code], r_buy)
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
        r_sell = passorder(24, 1101, ACCOUNT_ID, code, 5, -1, amount, REMARK, 2, "", C)
        _pending_orders[code] = {
            "type": "sell",
            "amount": amount,
            "original_amount": amount,
            "attempt_amount": amount,
            "attempt_traded": 0,
            "filled_before": 0,
            "filled_amount": 0,
            "attempt_order_id": "",
            "attempt_sysid": "",
            "price": price,
            "time": _get_market_time(C),
            "retries": 0,
            "entry_price": entry_price,
            "entry_date": entry_date,
        }
        _bind_attempt_ref(_pending_orders[code], r_sell)
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
            # 部分成交回滚后，现金已按「未成交段退回 / 已成交段保留估算价」处理过。
            # 此时对账若再整笔按真实成交额冲一次，会重复扣/退同一笔钱。
            # 正确做法：只校准「已成交段」的估算价与真实成交价之差。
            if rolled and tr is not None:
                r_filled = int(od.get("rollback_filled", 0) or 0)
                r_price = od.get("rollback_price", 0) or 0
                if r_filled <= 0:
                    _log("[P13 reconcile] %s 已回滚且无成交，跳过（现金已在回滚时结清）" % code, C)
                    continue
                if od["dir"] == "buy":
                    diff = tr["buy_amt"] - r_filled * r_price
                    _cash -= diff
                    # 真实成交段优先于回滚快照：撤单反查的成交量可能滞后于柜台，
                    # 收盘 deal 才是唯一真相，持仓量/成本都按真实成交写。
                    real_vol = int(tr["buy_vol"])
                    if real_vol > 0:
                        real_cost = tr["buy_amt"] / real_vol
                    else:
                        real_vol = r_filled
                        real_cost = r_price
                    if code not in _holdings:
                        _holdings[code] = {}
                    _holdings[code]["volume"] = real_vol
                    _holdings[code]["cost"] = real_cost
                    _holdings[code]["peak_close"] = real_cost
                    _holdings[code]["entry_date"] = od.get("entry_date", "")
                    _log("[P13 reconcile] %s 买入部分成交回滚后校准 真实成交=%d股 均价=%.3f 价差%.2f 现金=%.0f"
                         % (code, real_vol, real_cost, diff, _cash), C)
                else:
                    diff = tr["sell_amt"] - r_filled * r_price
                    _cash += diff
                    remain = od["amount"] - int(tr["sell_vol"])
                    if remain > 0:
                        _holdings[code] = {
                            "volume": int(remain),
                            "cost": od.get("entry_price", 0),
                            "peak_close": od.get("entry_price", 0),
                            "entry_date": od.get("entry_date", ""),
                            "hold_days": 0,
                        }
                    else:
                        _holdings.pop(code, None)
                    _log("[P13 reconcile] %s 卖出部分成交回滚后校准价差 %.2f 现金=%.0f"
                         % (code, diff, _cash), C)
                continue
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
        # 隔离态挂单的显式出口：cancel_unconfirmed 表示「原委托终态无法证明、
        # 撤单未发出或轮询封顶」，这批单已经靠 deal + 账户持仓隐式校准完毕，
        # 即将随下方 _pending_orders 清空而丢失跟踪。清理前必须逐条打印出来，
        # 否则运维在日志里看不到「今天有几张单没能自证终态、靠对账兜的底」。
        if _pending_orders:
            for _pc, _pi in sorted(_pending_orders.items()):
                if _pi.get("cancel_unconfirmed"):
                    _log("[P13 reconcile] %s 隔离态挂单：终态未确认，"
                         "已按成交明细+账户持仓对账兜底后移出跟踪"
                         "（撤单已发=%s 撤单动作=%s 轮询=%s 重发=%s）"
                         % (_pc, _pi.get("cancel_sent"), _pi.get("cancel_action", "drop"),
                            _pi.get("cancel_poll", 0), _pi.get("cancel_resend", 0)), C)
                else:
                    _log("[P13 reconcile] %s 未终态挂单（非隔离态）："
                         "已按成交明细+账户持仓对账兜底后移出跟踪" % _pc, C)
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