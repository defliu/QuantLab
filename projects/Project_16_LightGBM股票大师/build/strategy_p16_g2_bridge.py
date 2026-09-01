# coding=gbk
"""
g2 大QMT 文件桥 · 内置执行器（Python 3.6.8 单文件源码）

外部信号层把买卖指令写到文件桥，本策略在大QMT内轮询读取指令 → 委托 → 反查 → 超时撤单重试 → 状态回写。

数据流：
    外部(选股/风控, Python3.10)                内置执行器(大QMT, Python3.6)
            | 写 cmd/orders_<date>.json  -->  读指令(seq幂等)
            | 写 cmd/cancel_<date>.json  -->  撤单处理
            | <-- 写 state/fills_<date>.json    委托/成交/失败回写
            | <-- 写 state/positions_<date>.json 持仓快照
            | <-- 写 state/asset_<date>.json    资产快照
            | <-- 写 state/heart_<date>.json    心跳+已处理seq
"""
import json
import os
import time
import traceback

# ============================================================
# 常量
# ============================================================
BUILD_TAG = "20260901-142420"

BRIDGE_DIR = "D:/QMT_POOL/g2_bridge"
CMD_DIR = os.path.join(BRIDGE_DIR, "cmd")
STATE_DIR = os.path.join(BRIDGE_DIR, "state")

ACCOUNT_ID = "70180771"

# passorder 常量（对齐ATR战备模板：prType=5最新价，第8参=remark对账标识，第9参=2，第11参=C）
OP_BUY = 23
OP_SELL = 24
ORDER_TYPE_VOL = 1101
PRTYPE_LATEST = 5
ORDER_REMARK = "P16G2桥"

# pending 超时与重试（买卖统一：300秒/3次，防「30秒即放弃永不补单」）
PENDING_TIMEOUT = 300
MAX_RETRY = 3

# 订单状态码（对齐 QMT撤单重委托方案调研.md 标准表）：
# 活跃={48未报,49待报,50已报,51已报待撤,52部成待撤,55部成}；终态={53部撤,54已撤,56全成,57废单}
CANCEL_STATUS = (53, 54)
CONFIRM_DEAD_STATUS = (53, 54, 57)   # 撤单死透=部撤/已撤/废单（55部成是活跃态不能算死）
ACTIVE_SKIP_STATUS = (53, 54, 57)    # active_only 时跳过终态（55部成是活跃态不能排除；调研 4.2 原写 54,55,57 有 bug）

# 内置实时风控参数（自 qmt_monitor.py 迁移，纯行情驱动）
STOP_LOSS_PCT = 0.07
TAKE_PROFIT_PCT = 0.15
TRAILING_PCT = 0.08

# 反查参数
LOOKUP_RETRIES = 6
LOOKUP_INTERVAL = 0.25

# 持仓/资产刷新间隔(秒)
POSITION_REFRESH_INTERVAL = 10

# ============================================================
# 全局状态
# ============================================================
_g_pending = {}
_g_last_cmd_seq = 0
_g_last_position_refresh = 0.0
_g_last_asset_refresh = 0.0
_g_today = ""
_g_today_order_count = 0
_g_today_fill_count = 0
_g_today_abandon_count = 0
_g_today_risk_count = 0
_g_positions = {}
_g_risk_sold = set()
_g_diag_printed = False


# ============================================================
# 工具函数
# ============================================================
def _atomic_write_json(path, data):
    """临时文件 + rename，避免内置桥读到半个 JSON。
    2026-09-01 教训：外部进程（监控轮询 Get-Content）短暂持有目标文件时 os.replace 会抛
    PermissionError(WinError 5) → 必须重试（最多 5 次×0.2s），且调用方需 try/except 兜底防崩溃。"""
    tmp = path + ".tmp"
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return
        except OSError as e:
            if attempt < 4:
                time.sleep(0.2)
                continue
            raise e


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _today_str():
    return time.strftime("%Y%m%d")


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _to_qmt_code(code):
    """QMT passorder 下单代码：600522.SH（数字在前）原样返回。
    勿翻转为 SH.600522 —— QMT 解析 SH.600522 为 600522SH 判「代码不合法」废单（T-20260831-002，
    QMT 主日志实锤: orderCode:600522SH 不合法!；ATR/Project_10 均以 600522.SH 实盘成交）。"""
    parts = code.split(".")
    if len(parts) != 2:
        return None
    num, ex = parts[0], parts[1]
    if ex == "SH" and (num.startswith("6") or num.startswith("688")):
        return "%s.SH" % num
    if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
        return "%s.SZ" % num
    return None


def _to_bridge_code(code):
    """QMT代码(SH.600522) 转回桥协议代码(600522.SH)，供 fills 回写外部对账。"""
    if "." in code:
        ex, num = code.split(".", 1)
        if ex == "SH" and num.startswith("6"):
            return "%s.SH" % num
        if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
            return "%s.SZ" % num
    return code


def _norm_code(code):
    """代码归一化：统一返回裸数字段用于跨格式比较。
    'SH.600522' / '600522.SH' / '600522' 均 → '600522'。QMT m_strInstrumentID 返回裸码，
    而指令为 SH.600522 / 桥协议 600522.SH，直接 o_code != code 比较必失配（T-20260831-001）。"""
    c = str(code or "").strip()
    if "." in c:
        return c.split(".")[0]
    return c


def _valid_bridge_code(code):
    """桥协议代码是否可转为QMT代码。"""
    parts = code.split(".")
    if len(parts) != 2:
        return False
    num, ex = parts[0], parts[1]
    if ex == "SH" and (num.startswith("6") or num.startswith("688")):
        return True
    if ex == "SZ" and (num.startswith("0") or num.startswith("3")):
        return True
    return False


def _fills_path(date):
    return os.path.join(STATE_DIR, "fills_%s.json" % date)


def _positions_path(date):
    return os.path.join(STATE_DIR, "positions_%s.json" % date)


def _asset_path(date):
    return os.path.join(STATE_DIR, "asset_%s.json" % date)


def _heart_path(date):
    return os.path.join(STATE_DIR, "heart_%s.json" % date)


def _orders_cmd_path(date):
    return os.path.join(CMD_DIR, "orders_%s.json" % date)


def _cancel_cmd_path(date):
    return os.path.join(CMD_DIR, "cancel_%s.json" % date)


def _orders_archive_path(date, seq):
    return os.path.join(CMD_DIR, "orders_%s_done_%s.json" % (date, seq))


def _cancel_archive_path(date, seq):
    return os.path.join(CMD_DIR, "cancel_%s_done_%s.json" % (date, seq))


def _positions_cfg_path(date):
    return os.path.join(CMD_DIR, "positions_cfg_%s.json" % date)


def _peak_path():
    return os.path.join(STATE_DIR, "peak.json")


def _archive_cmd(path, date, seq, prefix):
    """归档已处理的cmd文件。目标已存在则先删除。"""
    if prefix == "orders":
        dest = _orders_archive_path(date, seq)
    else:
        dest = _cancel_archive_path(date, seq)
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except Exception:
            pass
    try:
        os.rename(path, dest)
    except Exception:
        pass


# ============================================================
# fills 回写
# ============================================================
def _read_fills(date):
    return _read_json(_fills_path(date)) or {"account_id": ACCOUNT_ID, "date": date, "updated_at": "", "fills": []}


def _write_fills(date, fills_data):
    fills_data["account_id"] = ACCOUNT_ID
    fills_data["date"] = date
    fills_data["updated_at"] = _now_str()
    _atomic_write_json(_fills_path(date), fills_data)


def _add_or_update_fill(date, fill_info):
    """向fills中追加或更新一条记录。同一strategy_order_id只保留最新状态。"""
    data = _read_fills(date)
    existing = None
    for i, f in enumerate(data.get("fills", [])):
        if f.get("strategy_order_id") == fill_info.get("strategy_order_id"):
            existing = i
            break
    if existing is not None:
        data["fills"][existing] = fill_info
    else:
        if "fills" not in data:
            data["fills"] = []
        data["fills"].append(fill_info)
    _write_fills(date, data)


# ============================================================
# 下单
# ============================================================
def _do_passorder(C, action, code, vol, price, prtype=PRTYPE_LATEST):
    """调用passorder全局函数。11参形式（对齐ATR战备模板）：
    (op, 1101, account, code, prType, 第6参=价格, 第7参=股数, remark, quickTrade=2, '', C)。
    prtype: 5=最新价(默认,忽略price按市价) / 11=指定价(用price做限价,可造不可成交单测撤单)。"""
    if action == "BUY":
        op = OP_BUY
    else:
        op = OP_SELL
    try:
        ret = passorder(op, ORDER_TYPE_VOL, ACCOUNT_ID, code, prtype, price, vol, ORDER_REMARK, 2, "", C)
        return ret
    except Exception as e:
        print("[P16G2][PASSORDER-ERR] %s %s: %s" % (action, code, e))
        return None


# ============================================================
# 反查订单
# ============================================================
def _extract_order_id(matched):
    """从QMT订单对象提取撤单用委托标识。字段名随QMT版本变化，多字段兜底。
    参考 qmt_wrapper 用 m_nOrderID（真实 QMT 平台委托号，撤单已验证可用）→ 优先取；
    D:\\QMT交易端模拟 模拟端无 m_nOrderID（DIAG 实锤），只有 m_strOrderRef（passorder ref）
    与 m_strOrderSysID（sys号）——但实测**模拟端 passorder(24) 撤单两个都不认**（被当价格解析、
    报「下单数量为0」），模拟端撤单只能走 QMT 界面手动；取到仅用于 pending 记录/诊断。"""
    if matched is None:
        return ""
    for attr in ("m_nOrderID", "m_strOrderID", "m_strOrderRef", "m_strOrderSysID", "m_strSysid", "m_nOrderRef", "m_strUserOrderId"):
        try:
            v = getattr(matched, attr, "")
            if v:
                return str(v)
        except Exception:
            continue
    return ""


def _extract_deal_volume(matched, fallback_vol=0):
    """从QMT订单对象提取已成交量。多字段兜底；全部取不到但 status=56(已成) 时按目标量视为全成。"""
    if matched is None:
        return 0
    for attr in ("m_nDealVolume", "m_nTradedVolume", "m_nVolumeTraded", "m_nCumDealVolume", "m_nDealVol", "m_nTradeVolume", "m_nVolTraded"):
        try:
            v = getattr(matched, attr, 0) or 0
            if int(v) > 0:
                return int(v)
        except Exception:
            continue
    try:
        st = int(getattr(matched, "m_nOrderStatus", 0) or 0)
        if st == 56:
            return int(fallback_vol)
    except Exception:
        pass
    return 0


def _lookup_order(C, code, vol, action, sid="", active_only=True):
    """短轮询反查订单（对齐ATR战备模板）。
    全局函数优先、小写'stock'、m_strOrderID优先、量容差相对化(0.10)。
    active_only=True: 跳过终态(54,55,57)，用于下单反查/重报反查。
    active_only=False: 不过滤终态，用于跟踪/撤单确认/超时确认。
    候选排序: sid(userOrderId)精确匹配 > remark含ORDER_REMARK > 时间最新（软优先不硬过滤）。
    """
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return None, None
    dir_cn = "买入" if action == "BUY" else "卖出"
    for _ in range(LOOKUP_RETRIES):
        time.sleep(LOOKUP_INTERVAL)
        try:
            orders = f_get(ACCOUNT_ID, "stock", "order")
            if not orders:
                continue
            candidates = []
            for o in orders:
                try:
                    o_code = str(getattr(o, "m_strInstrumentID", "") or "")
                    o_vol = int(getattr(o, "m_nOrderVolume", 0) or 0)
                    o_opt = str(getattr(o, "m_strOptName", "") or "")
                    o_status = int(getattr(o, "m_nOrderStatus", 0) or 0)
                except Exception:
                    continue
                if _norm_code(o_code) != _norm_code(code):
                    continue
                if dir_cn not in o_opt:
                    continue
                if active_only and o_status in ACTIVE_SKIP_STATUS:
                    continue
                if vol > 0 and o_vol > 0 and abs(o_vol - vol) > float(max(o_vol, vol)) * 0.10:
                    continue
                candidates.append(o)
            if candidates:
                def _key(o):
                    uid = str(getattr(o, "m_strUserOrderId", "") or "")
                    remark = str(getattr(o, "m_strRemark", "") or "")
                    return (
                        1 if sid and uid == sid else 0,
                        1 if ORDER_REMARK in remark else 0,
                        str(getattr(o, "m_strInsertTime", "") or ""),
                    )
                candidates.sort(key=_key, reverse=True)
                matched = candidates[0]
                order_id = _extract_order_id(matched)
                # 一次性诊断：打印匹配订单的 ID/状态类字段，确认真实委托号字段名
                global _g_diag_printed
                if not _g_diag_printed:
                    _g_diag_printed = True
                    try:
                        attrs = [a for a in dir(matched)
                                 if ("order" in a.lower()) or ("sys" in a.lower()) or ("id" in a.lower())
                                 or ("deal" in a.lower()) or ("vol" in a.lower()) or ("trade" in a.lower())
                                 or ("price" in a.lower()) or ("amount" in a.lower())]
                        diag = {a: str(getattr(matched, a, ""))[:40] for a in attrs}
                        print("[P16G2][DIAG-ORDER] 匹配订单字段=%s" % diag)
                    except Exception:
                        pass
                return order_id, matched
        except Exception:
            continue
    return None, None


def _cancel_order_by_id(C, code, order_id, sysid=""):
    """官方撤单 cancel()（对齐 QMT撤单重委托方案调研.md：passorder opType 无撤单值（24=卖出），
    cancel(orderId, accountId, accountType, C) 是官方撤单唯一入口；先 can_cancel_order 预检）。
    顺序：can_cancel_order 预检 → cancel() 发指令（返回成功仅=指令送达柜台）→ 确认态轮询终态。
    order_id 优先用 sysid（m_strOrderSysID 柜台合同号，调研实战文多用），否则用 order_id。"""
    oid = sysid or order_id
    if not oid:
        return None
    try:
        if not can_cancel_order(oid, ACCOUNT_ID, "stock"):
            print("[P16G2][CANCEL-NOT-CANCELABLE] %s %s 当前不可撤" % (code, oid))
            return None
    except Exception:
        pass
    try:
        r = cancel(oid, ACCOUNT_ID, "stock", C)
        print("[P16G2][CANCEL] code=%s order_id=%s 返回:%s" % (code, oid, r))
        return r
    except Exception as e:
        print("[P16G2][CANCEL-ERR] code=%s order_id=%s: %s" % (code, oid, e))
        return None


# ============================================================
# 执行单笔指令
# ============================================================
def _execute_order(C, order, date):
    """处理单条买卖指令，记入pending。"""
    global _g_today_order_count
    action = order.get("action", "")
    code = order.get("code", "")
    vol = int(order.get("vol", 0) or 0)
    price = float(order.get("price", 0) or 0)
    strategy_order_id = order.get("strategy_order_id", "")

    # 无效指令检查
    if vol <= 0 or price <= 0:
        print("[P16G2][REJECTED] %s %s vol=%d price=%.3f reason=无效参数" % (action, code, vol, price))
        _add_or_update_fill(date, {
            "strategy_order_id": strategy_order_id,
            "code": code,
            "action": action,
            "vol": vol,
            "price": price,
            "status": "REJECTED",
            "sysid": "",
            "reason": "vol<=0 or price<=0",
            "ts": _now_str(),
        })
        return

    # 代码格式转换
    qmt_code = _to_qmt_code(code)
    if qmt_code is None:
        print("[P16G2][REJECTED] %s %s reason=代码格式无法转换" % (action, code))
        _add_or_update_fill(date, {
            "strategy_order_id": strategy_order_id,
            "code": code,
            "action": action,
            "vol": vol,
            "price": price,
            "status": "REJECTED",
            "sysid": "",
            "reason": "code format invalid: %s" % code,
            "ts": _now_str(),
        })
        return

    # 幂等：已在pending中则跳过
    if strategy_order_id in _g_pending:
        print("[P16G2][SKIP] %s %s 已在pending中" % (action, strategy_order_id))
        return

    # 下单（prtype 可覆盖为 11 指定价，用于测试不可成交单）
    prtype = int(order.get("prtype", PRTYPE_LATEST) or PRTYPE_LATEST)
    ret = _do_passorder(C, action, qmt_code, vol, price, prtype)
    print("[P16G2][ORDER] %s %s %s %d股 @%.3f prtype=%d ret=%s" % (action, strategy_order_id, qmt_code, vol, price, prtype, ret))
    _g_today_order_count += 1

    # 反查确认（同tick内撤单需要 sysid=柜台合同号；m_strOrderSysID 优先，与 _check_pending_orders 一致）
    oid, matched = _lookup_order(C, qmt_code, vol, action, sid=strategy_order_id)
    sysid = ""
    if matched:
        sysid = str(getattr(matched, "m_strSysid", "") or getattr(matched, "m_strOrderSysID", "") or "")

    # 写入pending（vol=目标总量, cur_vol=当前活跃单量, filled_so_far=历史已成交累计）
    _g_pending[strategy_order_id] = {
        "code": qmt_code,
        "bridge_code": code,
        "action": action,
        "vol": vol,
        "cur_vol": vol,
        "filled_so_far": 0,
        "price": price,
        "time": time.time(),
        "retry": 0,
        "order_id": oid or "",
        "sysid": sysid,
        "status": "PENDING",
    }


# ============================================================
# 撤单处理
# ============================================================
def _process_cancels(C, cancel_data, date):
    """处理撤单指令：先查deal——已全部成交则撤太迟按FILLED收尾；否则撤单后CANCELED。"""
    if not cancel_data:
        return
    cancels = cancel_data.get("cancels", [])
    for c in cancels:
        sid = c.get("strategy_order_id", "")
        info = _g_pending.get(sid)
        if info is None:
            print("[P16G2][CANCEL-SKIP] %s 不在pending中" % sid)
            continue
        code = info["code"]
        action = info["action"]
        vol = info.get("vol", 0)
        cur_vol = info.get("cur_vol", vol)
        filled_so_far = info.get("filled_so_far", 0)
        oid = info.get("order_id", "")
        deal_vol = 0
        # 无论 order_id 是否已知，先反查订单当前状态（2026-09-01 竞态：模拟盘下单 0.1s 即成交，
        # 撤单指令到达时订单可能已成交 → 必须按 FILLED 收尾，绝不写假 CANCELED）
        _oid, m = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if m is not None:
            oid = _extract_order_id(m) or oid
            deal_vol = _extract_deal_volume(m, fallback_vol=cur_vol)
            st_now = int(getattr(m, "m_nOrderStatus", 0) or 0)
            # 补录 sysid（柜台合同号，cancel() 需要；_execute_order 若未捕获这里兜底）
            _sid_now = str(getattr(m, "m_strSysid", "") or getattr(m, "m_strOrderSysID", "") or info.get("sysid", ""))
            if _sid_now:
                info["sysid"] = _sid_now
            if deal_vol >= cur_vol or st_now == 56:
                total_deal = filled_so_far + deal_vol
                _add_or_update_fill(date, {
                    "strategy_order_id": sid,
                    "code": info.get("bridge_code", code),
                    "action": action,
                    "vol": total_deal,
                    "price": info.get("price", 0),
                    "status": "FILLED",
                    "sysid": info.get("sysid", ""),
                    "reason": info.get("risk_reason", "filled before cancel"),
                    "ts": _now_str(),
                })
                _g_pending.pop(sid, None)
                print("[P16G2][CANCEL-TOO-LATE] %s 已全部成交 deal=%d" % (sid, total_deal))
                continue
        if oid:
            # 官方撤单 cancel()（调研 QMT撤单重委托方案.md），进入 cancel_requested 确认态
            _cancel_order_by_id(C, code, oid, info.get("sysid", ""))
            info["order_id"] = oid
            info["cancel_requested"] = True
            info["time"] = time.time()
            print("[P16G2][CANCEL-SENT] %s %s 撤单已发 order_id=%s，等待确认" % (sid, code, oid))
            continue
        # 拿不到真实 order_id：绝不假写 CANCELED（否则 QMT 真单变孤儿），保留 pending 延后复查
        print("[P16G2][CANCEL-NOID] %s %s 无法定位order_id，保留pending复查" % (sid, code))
        continue


# ============================================================
# pending 状态机
# ============================================================
def _handle_rejected_retry(C, date, sid, info, now, deal_vol):
    """status=55 废单：累计已成交到filled_so_far，计retry重报剩余量；retry耗尽则ABANDON。"""
    global _g_today_abandon_count
    code = info["code"]
    action = info["action"]
    vol = info.get("vol", 0)
    filled_so_far = info.get("filled_so_far", 0) + deal_vol
    retry = info.get("retry", 0)
    info["filled_so_far"] = filled_so_far
    if retry >= MAX_RETRY:
        print("[P16G2][ABANDON] %s %s 废单retry=%d filled=%d" % (sid, code, retry, filled_so_far))
        _add_or_update_fill(date, {
            "strategy_order_id": sid,
            "code": info.get("bridge_code", code),
            "action": action,
            "vol": filled_so_far,
            "price": info.get("price", 0),
            "status": "ABANDONED",
            "sysid": info.get("sysid", ""),
            "reason": info.get("risk_reason", "rejected status=55, retry exhausted"),
            "ts": _now_str(),
        })
        _g_pending.pop(sid, None)
        _g_today_abandon_count += 1
        return
    remaining = vol - filled_so_far
    if remaining <= 0:
        _g_pending.pop(sid, None)
        return
    ret = _do_passorder(C, action, code, remaining, info.get("price", 0))
    print("[P16G2][REJECTED-RETRY-%d] %s %s %d股 ret=%s" % (retry + 1, sid, code, remaining, ret))
    oid3, m3 = _lookup_order(C, code, remaining, action, sid=sid)
    sysid3 = ""
    if m3:
        sysid3 = str(getattr(m3, "m_strSysid", "") or "")
    info["time"] = now
    info["retry"] = retry + 1
    info["cur_vol"] = remaining
    info["order_id"] = oid3 or ""
    info["sysid"] = sysid3


def _handle_timeout_retry(C, date, sid, info, now):
    """超时处理：撤旧单 → 确认死透才重报剩余量；撤单未确认则延后60s复查（绝不重复报）。
    关键：撤前若已全部成交(dv2>=cur_vol) → 按FILLED收尾绝不重报；死透才重报 remaining=vol-filled_so_far。"""
    global _g_today_fill_count, _g_today_abandon_count
    code = info["code"]
    action = info["action"]
    vol = info.get("vol", 0)
    cur_vol = info.get("cur_vol", vol)
    filled_so_far = info.get("filled_so_far", 0)
    retry = info.get("retry", 0)
    oid = info.get("order_id", "")
    if not oid:
        # order_id 未知：先反查补齐真实委托号再撤（防空ID撤不掉→旧单未死透→误重报双倍持仓）
        _oid0, _m0 = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if _m0 is not None:
            oid = _extract_order_id(_m0) or _oid0 or ""
    print("[P16G2][TIMEOUT] %s %s elapsed>%ds retry=%d cur_vol=%d filled=%d" % (
        sid, code, PENDING_TIMEOUT, retry, cur_vol, filled_so_far))
    if oid:
        _cancel_order_by_id(C, code, oid, info.get("sysid", ""))
    # 短轮询确认撤单死透（4次×0.5s）：dv2>=cur_vol → 撤前已全成交；状态码进入终态 → 死透
    dead = False
    filled_before_cancel = 0
    saw_order = False
    for _ in range(4):
        time.sleep(0.5)
        oid2, m2 = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if m2 is None:
            continue
        saw_order = True
        st2 = int(getattr(m2, "m_nOrderStatus", 0) or 0)
        dv2 = _extract_deal_volume(m2, fallback_vol=cur_vol)
        if dv2 >= cur_vol:
            filled_before_cancel = dv2
            break
        if st2 in CONFIRM_DEAD_STATUS:
            dead = True
            break
    if not saw_order:
        dead = True
    if filled_before_cancel > 0:
        total_deal = filled_so_far + filled_before_cancel
        print("[P16G2][FILLED] %s %s 撤前已全成交 deal=%d" % (sid, code, total_deal))
        _add_or_update_fill(date, {
            "strategy_order_id": sid,
            "code": info.get("bridge_code", code),
            "action": action,
            "vol": total_deal,
            "price": info.get("price", 0),
            "status": "FILLED",
            "sysid": info.get("sysid", ""),
            "reason": info.get("risk_reason", "filled before cancel"),
            "ts": _now_str(),
        })
        _g_pending.pop(sid, None)
        _g_today_fill_count += 1
        return
    if not dead:
        # 撤单未确认死透 → 延后60s复查，不重报不计数（防双倍持仓）
        print("[P16G2][TIMEOUT-UNCONFIRMED] %s 撤单未确认，延后60s复查" % sid)
        info["time"] = now - PENDING_TIMEOUT + 60
        return
    if retry >= MAX_RETRY:
        print("[P16G2][ABANDON] %s %s 超时retry=%d filled=%d" % (sid, code, retry, filled_so_far))
        _add_or_update_fill(date, {
            "strategy_order_id": sid,
            "code": info.get("bridge_code", code),
            "action": action,
            "vol": filled_so_far,
            "price": info.get("price", 0),
            "status": "ABANDONED",
            "sysid": info.get("sysid", ""),
            "reason": info.get("risk_reason", "timeout, retry exhausted"),
            "ts": _now_str(),
        })
        _g_pending.pop(sid, None)
        _g_today_abandon_count += 1
        return
    remaining = vol - filled_so_far
    if remaining <= 0:
        _g_pending.pop(sid, None)
        return
    ret = _do_passorder(C, action, code, remaining, info.get("price", 0))
    print("[P16G2][RETRY-%d] %s %s %d股 ret=%s" % (retry + 1, sid, code, remaining, ret))
    oid3, m3 = _lookup_order(C, code, remaining, action, sid=sid)
    sysid3 = ""
    if m3:
        sysid3 = str(getattr(m3, "m_strSysid", "") or "")
    info["time"] = now
    info["retry"] = retry + 1
    info["cur_vol"] = remaining
    info["order_id"] = oid3 or ""
    info["sysid"] = sysid3


def _handle_cancel_confirm(C, date, sid, info, now):
    """撤单已发后确认终态（2026-09-01 实锤定稿）。
    已成(status56/deal>=cur_vol)→FILLED；已撤(53/54)→CANCELED；废单(55/57)→REJECTED；
    仍活跃→撤单未生效，保留 pending + 90s 重发（绝不假写 CANCELED，fail-loud）；
    查不到订单→状态未知，写 UNCONFIRMED（外部以 positions 兜底，绝不写假 CANCELED）。"""
    global _g_today_fill_count
    code = info["code"]
    action = info["action"]
    cur_vol = info.get("cur_vol", info.get("vol", 0))
    filled_so_far = info.get("filled_so_far", 0)
    _oid, m = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
    if m is not None:
        st = int(getattr(m, "m_nOrderStatus", 0) or 0)
        dv = _extract_deal_volume(m, fallback_vol=cur_vol)
        # 补录 sysid（m_strOrderSysID 柜台合同号，官方 cancel() 撤单用）
        _sid_now = str(getattr(m, "m_strSysid", "") or getattr(m, "m_strOrderSysID", "") or info.get("sysid", ""))
        if _sid_now:
            info["sysid"] = _sid_now
        if dv >= cur_vol or st == 56:
            total_deal = filled_so_far + dv
            _add_or_update_fill(date, {
                "strategy_order_id": sid, "code": info.get("bridge_code", code), "action": action,
                "vol": total_deal, "price": info.get("price", 0), "status": "FILLED",
                "sysid": info.get("sysid", ""),
                "reason": info.get("risk_reason", "filled while canceling"),
                "ts": _now_str(),
            })
            _g_pending.pop(sid, None)
            _g_today_fill_count += 1
            print("[P16G2][CANCEL-CONFIRM-FILLED] %s %s deal=%d" % (sid, code, total_deal))
            return
        if st in (53, 54):
            _add_or_update_fill(date, {
                "strategy_order_id": sid, "code": info.get("bridge_code", code), "action": action,
                "vol": filled_so_far + dv, "price": info.get("price", 0), "status": "CANCELED",
                "sysid": info.get("sysid", ""),
                "reason": info.get("risk_reason", "cancel confirmed"),
                "ts": _now_str(),
            })
            _g_pending.pop(sid, None)
            print("[P16G2][CANCEL-CONFIRMED] %s %s status=%d" % (sid, code, st))
            return
        if st in (55, 57):
            _add_or_update_fill(date, {
                "strategy_order_id": sid, "code": info.get("bridge_code", code), "action": action,
                "vol": filled_so_far + dv, "price": info.get("price", 0), "status": "REJECTED",
                "sysid": info.get("sysid", ""),
                "reason": "cancel rejected status=%d" % st, "ts": _now_str(),
            })
            _g_pending.pop(sid, None)
            print("[P16G2][CANCEL-REJECTED] %s %s status=%d" % (sid, code, st))
            return
        # 仍活跃（2/48/49/50 等非终态）：撤单未生效！保留 pending（fail-loud），90s 后重发最多2次
        print("[P16G2][CANCEL-NOT-EFFECTIVE] %s %s status=%d 撤单未生效，订单仍活跃，保留pending复查" % (sid, code, st))
        if now - info.get("time", now) > 90 and info.get("cancel_retry", 0) < 2:
            info["cancel_retry"] = info.get("cancel_retry", 0) + 1
            info["time"] = now
            _cancel_order_by_id(C, code, _extract_order_id(m) or info.get("order_id", ""), info.get("sysid", ""))
            print("[P16G2][CANCEL-RETRY] %s %s 重发撤单 %d/2" % (sid, code, info["cancel_retry"]))
        return
    # m is None：查不到订单（可能已撤/已成交/列表过期）→ UNCONFIRMED，外部以 positions 兜底
    print("[P16G2][CANCEL-UNKNOWN] %s %s 撤单后订单查不到，状态未知，写UNCONFIRMED（外部以positions兜底）" % (sid, code))
    _add_or_update_fill(date, {
        "strategy_order_id": sid, "code": info.get("bridge_code", code), "action": action,
        "vol": filled_so_far, "price": info.get("price", 0), "status": "UNCONFIRMED",
        "sysid": info.get("sysid", ""),
        "reason": "cancel state unknown (may be canceled or filled), rely on positions",
        "ts": _now_str(),
    })
    _g_pending.pop(sid, None)


def _check_pending_orders(C, date):
    """检查所有pending订单状态。主判据=成交量(m_nDealVolume)，状态码仅辅助收尾。
    1a) filled_so_far+deal_vol >= vol → FILLED（成交量硬事实优先于一切状态码）
    1b) status=55 废单 → 计retry重报剩余量
    1c) status in (53,54) → CANCELED 收尾(vol=total_deal)
    1d) deal>0 → PARTIAL_FILLED 进度回写
    然后超时判断 → 撤单→确认死透→重报剩余量；未确认延后复查。"""
    global _g_today_fill_count, _g_today_abandon_count
    now = time.time()
    for sid in list(_g_pending.keys()):
        info = _g_pending[sid]
        action = info["action"]
        code = info["code"]
        vol = info.get("vol", 0)
        cur_vol = info.get("cur_vol", vol)
        filled_so_far = info.get("filled_so_far", 0)
        price = info.get("price", 0)

        # 撤单确认态：只确认终态（已成→FILLED / 已撤→CANCELED / 废单→REJECTED / 超时强制），不走重试/超时路径
        if info.get("cancel_requested"):
            _handle_cancel_confirm(C, date, sid, info, now)
            continue

        oid, matched = _lookup_order(C, code, cur_vol, action, sid=sid, active_only=False)
        if matched is None:
            elapsed = now - info.get("time", now)
            if elapsed > PENDING_TIMEOUT:
                _handle_timeout_retry(C, date, sid, info, now)
            else:
                print("[P16G2][PENDING-NO-ORDER] %s %s elapsed=%.0fs" % (sid, code, elapsed))
            continue

        deal_vol = _extract_deal_volume(matched, fallback_vol=cur_vol)
        status = int(getattr(matched, "m_nOrderStatus", 0) or 0)
        sysid = str(getattr(matched, "m_strSysid", "") or getattr(matched, "m_strOrderSysID", "") or info.get("sysid", ""))
        if sysid:
            info["sysid"] = sysid
        # 回填 order_id：首次反查（_execute_order 内）可能因缓存延迟未抓到，状态机内持续补齐，
        # 否则撤单/超时重报拿不到真实委托号（2026-09-01 P0：撤单空ID→真单变孤儿）
        if not info.get("order_id"):
            oid_bk = _extract_order_id(matched)
            if oid_bk:
                info["order_id"] = oid_bk
        total_deal = filled_so_far + deal_vol

        # 1a) 成交量硬事实优先：已成交 >= 目标量 → FILLED
        if total_deal >= vol:
            fill_price = float(getattr(matched, "m_dPrice", 0) or getattr(matched, "m_dAvgPrice", 0) or price)
            print("[P16G2][FILLED] %s %s deal=%d" % (sid, code, total_deal))
            _add_or_update_fill(date, {
                "strategy_order_id": sid,
                "code": info.get("bridge_code", code),
                "action": action,
                "vol": total_deal,
                "price": fill_price,
                "status": "FILLED",
                "sysid": sysid,
                "reason": info.get("risk_reason", "deal confirmed"),
                "ts": _now_str(),
            })
            _g_pending.pop(sid, None)
            _g_today_fill_count += 1
            continue
        # 1b) 废单 → 计retry重报剩余量
        if status == 55:
            print("[P16G2][REJECTED] %s %s status=55废单 deal=%d" % (sid, code, deal_vol))
            _handle_rejected_retry(C, date, sid, info, now, deal_vol)
            continue
        # 1c) 撤类终态(53,54) → CANCELED 收尾（vol=total_deal，防漏计最后成交）
        if status in CANCEL_STATUS:
            print("[P16G2][CANCELED-FROM-QMT] %s %s status=%d deal=%d" % (sid, code, status, total_deal))
            _add_or_update_fill(date, {
                "strategy_order_id": sid,
                "code": info.get("bridge_code", code),
                "action": action,
                "vol": total_deal,
                "price": price,
                "status": "CANCELED",
                "sysid": sysid,
                "reason": info.get("risk_reason", "canceled by QMT, status=%d" % status),
                "ts": _now_str(),
            })
            _g_pending.pop(sid, None)
            continue
        # 1d) 有成交未满 → PARTIAL_FILLED 进度回写（filled_so_far不变，deal_vol是累计值）
        if deal_vol > 0:
            fill_price = float(getattr(matched, "m_dPrice", 0) or getattr(matched, "m_dAvgPrice", 0) or price)
            print("[P16G2][PARTIAL] %s %s deal=%d/%d" % (sid, code, total_deal, vol))
            _add_or_update_fill(date, {
                "strategy_order_id": sid,
                "code": info.get("bridge_code", code),
                "action": action,
                "vol": total_deal,
                "price": fill_price,
                "status": "PARTIAL_FILLED",
                "sysid": sysid,
                "reason": info.get("risk_reason", "partial deal, waiting more"),
                "ts": _now_str(),
            })
            continue

        # 2) 超时判断（有活跃单但无成交）
        elapsed = now - info.get("time", now)
        if elapsed > PENDING_TIMEOUT:
            _handle_timeout_retry(C, date, sid, info, now)
            continue
        print("[P16G2][PENDING-STATUS] %s %s status=%d deal=%d" % (sid, code, status, deal_vol))


# ============================================================
# 内置实时风控（自 qmt_monitor.py 迁移：止损/止盈/追盈，纯行情驱动）
# ============================================================
def _write_peak():
    """持久化追盈历史最高价（跨日延续，重启不丢）。"""
    try:
        data = {"account_id": ACCOUNT_ID, "updated_at": _now_str(),
                "peaks": {c: round(float(v.get("peak", 0.0) or 0.0), 4) for c, v in _g_positions.items()}}
        _atomic_write_json(_peak_path(), data)
    except Exception:
        pass


def _check_risk_signals(C, date):
    """内置实时风控：账户持仓全量纳管（防孤儿）→ get_full_tick → 三规则判断 → 触发即卖出走pending。
    成本锚=外部成本表(positions_cfg)；峰值=state/peak.json 持久化。"""
    global _g_today_risk_count
    # 1) 账户持仓全量纳管（AGENTS 红线：孤儿持仓=止损永不触发，必须纳管）
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        positions = f_get(ACCOUNT_ID, "stock", "position")
        acct_pos = {}
        if positions:
            for p in positions:
                code = str(getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or "")
                if not code:
                    continue
                if "." not in code:
                    if code.startswith("6") or code.startswith("688"):
                        code = "%s.SH" % code
                    else:
                        code = "%s.SZ" % code
                vol = int(getattr(p, "m_nVolume", 0) or 0)
                can_use = int(getattr(p, "m_nCanUseVolume", 0) or 0)
                if vol > 0:
                    acct_pos[code] = {"vol": vol, "can_use": can_use}
        for code, info in acct_pos.items():
            if code in _g_positions:
                _g_positions[code]["vol"] = info["vol"]
                _g_positions[code]["can_use"] = info["can_use"]
            else:
                # 孤儿持仓：成本未知不触发（避免误杀），但纳管待外部成本表校准
                _g_positions[code] = {"cost": 0.0, "vol": info["vol"], "can_use": info["can_use"], "peak": 0.0}
        for code in list(_g_positions.keys()):
            if code not in acct_pos:
                _g_positions.pop(code, None)
                _g_risk_sold.discard(code)
    except Exception as e:
        print("[P16G2][RISK-POS-ERR] %s" % e)
        return
    if not _g_positions:
        return
    # 2) 逐持仓评估（三规则照搬 qmt_monitor.evaluate）
    codes = list(_g_positions.keys())
    try:
        ticks = C.get_full_tick(codes)
    except Exception:
        ticks = {}
    if not ticks:
        return
    now = time.time()
    for code, info in _g_positions.items():
        if code in _g_risk_sold:
            continue
        cost = info.get("cost", 0.0)
        if cost <= 0:
            continue
        tick = ticks.get(code)
        if not tick:
            continue
        last = float(tick.get("lastPrice", 0) or 0)
        if last <= 0:
            continue
        high = max(float(tick.get("high", last) or last), last, info.get("peak", cost))
        info["peak"] = high
        action = "HOLD"
        note = ""
        stop_line = cost * (1 - STOP_LOSS_PCT)
        tp_line = cost * (1 + TAKE_PROFIT_PCT)
        if last <= stop_line:
            action, note = "SELL_STOP", "现价%.2f 跌破止损位%.2f" % (last, stop_line)
        elif last >= tp_line:
            action, note = "SELL_TAKE_PROFIT", "现价%.2f 达止盈位%.2f" % (last, tp_line)
        elif high > cost and last <= high * (1 - TRAILING_PCT):
            action, note = "SELL_TRAILING", "从高点%.2f 回撤%.0f%% 触发追盈(线%.2f)" % (
                high, TRAILING_PCT * 100, high * (1 - TRAILING_PCT))
        if action == "HOLD":
            continue
        can_use = info.get("can_use", info.get("vol", 0))
        if can_use <= 0:
            continue
        # 触发 → 记防重复标记 → passorder 卖出 → 走 pending 状态机
        _g_risk_sold.add(code)
        _g_today_risk_count += 1
        sid = "P16_%s_RSK%04d" % (date, _g_today_risk_count)
        ret = _do_passorder(C, "SELL", code, can_use, last)
        print("[P16G2][RISK] %s %s %s 卖%d股 ret=%s | %s" % (action, sid, code, can_use, ret, note))
        _g_pending[sid] = {
            "code": code,
            "bridge_code": _to_bridge_code(code),
            "action": "SELL",
            "vol": can_use,
            "cur_vol": can_use,
            "filled_so_far": 0,
            "price": last,
            "time": now,
            "retry": 0,
            "order_id": "",
            "sysid": "",
            "status": "PENDING",
            "risk_reason": action,
        }
        # 立即回写一条 RISK 触发记录，外部可实时感知（后续 FILLED/CANCELED 覆盖同 sid）
        _add_or_update_fill(date, {
            "strategy_order_id": sid,
            "code": _to_bridge_code(code),
            "action": "SELL",
            "vol": can_use,
            "price": last,
            "status": "RISK_%s" % action,
            "sysid": "",
            "reason": note,
            "ts": _now_str(),
        })


# ============================================================
# 持仓/资产快照
# ============================================================
def _refresh_positions(C, date):
    """导出持仓快照（全局函数优先、小写'stock'；m_strInstrumentID纯数字按首位补交易所后缀）。"""
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        positions = f_get(ACCOUNT_ID, "stock", "position")
        if not positions:
            return
        result = []
        for p in positions:
            code = str(getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strSecurityCode", "") or "")
            if not code:
                continue
            if "." not in code:
                if code.startswith("6") or code.startswith("688"):
                    code = "%s.SH" % code
                else:
                    code = "%s.SZ" % code
            vol = int(getattr(p, "m_nVolume", 0) or 0)
            can_use = int(getattr(p, "m_nCanUseVolume", 0) or 0)
            avg_price = float(getattr(p, "m_dOpenPrice", 0) or 0)
            market_value = float(getattr(p, "m_dInstrumentValue", 0) or 0)
            result.append({
                "code": code,
                "volume": vol,
                "can_use_volume": can_use,
                "avg_price": avg_price,
                "market_value": market_value,
            })
        data = {
            "account_id": ACCOUNT_ID,
            "date": date,
            "updated_at": _now_str(),
            "positions": result,
        }
        _atomic_write_json(_positions_path(date), data)
    except Exception as e:
        print("[P16G2][POSITIONS-ERR] %s" % e)


def _refresh_asset(C, date):
    """导出资产快照（全局函数优先、小写'stock'）。"""
    f_get = globals().get("get_trade_detail_data")
    if f_get is None:
        f_get = getattr(C, "get_trade_detail_data", None)
    if f_get is None:
        return
    try:
        accts = f_get(ACCOUNT_ID, "stock", "account")
        if not accts:
            return
        a = accts[0] if isinstance(accts, list) else accts
        total_asset = float(getattr(a, "m_dBalance", 0) or 0)
        cash_available = float(getattr(a, "m_dAvailable", 0) or 0)
        market_value = float(getattr(a, "m_dInstrumentValue", 0) or 0)
        data = {
            "account_id": ACCOUNT_ID,
            "date": date,
            "updated_at": _now_str(),
            "total_asset": total_asset,
            "cash_available": cash_available,
            "market_value": market_value,
        }
        _atomic_write_json(_asset_path(date), data)
    except Exception as e:
        print("[P16G2][ASSET-ERR] %s" % e)


# ============================================================
# 心跳
# ============================================================
def _write_heartbeat(date, cmd_seq):
    """写心跳文件（含pending快照，供重启恢复孤儿单）。"""
    pendings = []
    for sid in _g_pending.keys():
        info = _g_pending[sid]
        pendings.append({
            "strategy_order_id": sid,
            "code": info.get("code", ""),
            "bridge_code": info.get("bridge_code", ""),
            "action": info.get("action", ""),
            "vol": info.get("vol", 0),
            "cur_vol": info.get("cur_vol", info.get("vol", 0)),
            "filled_so_far": info.get("filled_so_far", 0),
            "price": info.get("price", 0),
            "time": info.get("time", time.time()),
            "retry": info.get("retry", 0),
            "order_id": info.get("order_id", ""),
            "sysid": info.get("sysid", ""),
            "status": info.get("status", "PENDING"),
            "cancel_requested": info.get("cancel_requested", False),
        })
    data = {
        "account_id": ACCOUNT_ID,
        "date": date,
        "last_heartbeat": _now_str(),
        "build_tag": BUILD_TAG,
        "pending_count": len(_g_pending),
        "last_cmd_seq_processed": cmd_seq,
        "pending": pendings,
        "risk_sold": sorted(_g_risk_sold),
        "risk_count": _g_today_risk_count,
    }
    _atomic_write_json(_heart_path(date), data)
    _write_peak()


# ============================================================
# 读取指令文件
# ============================================================
def _archive_cmd_if_same(path, date, seq, prefix):
    """重读cmd文件，seq仍一致才归档。防归档掉外部新写入的更高seq。"""
    try:
        data = _read_json(path)
        if data is None:
            return
        cur_seq = int(data.get("seq", 0) or 0)
        if cur_seq != seq:
            print("[P16G2][ARCHIVE-SKIP] %s seq变化 %d->%d，不归档" % (prefix, seq, cur_seq))
            return
        _archive_cmd(path, date, seq, prefix)
    except Exception:
        pass


def _process_orders_cmd(C, date):
    """读取并处理orders指令文件。返回已处理的seq(用于归档)。
    先持久化seq到heart再执行（宁可丢单不可重复：防下单后写heart前崩溃→重启重复下单）。"""
    global _g_last_cmd_seq
    path = _orders_cmd_path(date)
    data = _read_json(path)
    if data is None:
        return 0

    # 账号防呆
    file_acct = str(data.get("account_id", "") or "")
    if file_acct != ACCOUNT_ID:
        print("[P16G2][ACCT-MISMATCH] orders文件account=%s != 期望%s，跳过" % (file_acct, ACCOUNT_ID))
        return 0

    seq = int(data.get("seq", 0) or 0)
    if seq <= _g_last_cmd_seq:
        return 0

    print("[P16G2][CMD] 读取orders seq=%d (上次=%d)" % (seq, _g_last_cmd_seq))
    _g_last_cmd_seq = seq
    _write_heartbeat(date, seq)
    orders = data.get("orders", [])
    for o in orders:
        try:
            _execute_order(C, o, date)
        except Exception:
            print("[P16G2][ORDER-ERR] %s" % traceback.format_exc())

    _archive_cmd_if_same(path, date, seq, "orders")
    return seq


def _process_cancel_cmd(C, date):
    """读取并处理cancel指令文件。返回已处理的seq(用于归档)。"""
    global _g_last_cmd_seq
    path = _cancel_cmd_path(date)
    data = _read_json(path)
    if data is None:
        return 0

    file_acct = str(data.get("account_id", "") or "")
    if file_acct != ACCOUNT_ID:
        print("[P16G2][ACCT-MISMATCH] cancel文件account=%s != 期望%s，跳过" % (file_acct, ACCOUNT_ID))
        return 0

    seq = int(data.get("seq", 0) or 0)
    if seq <= _g_last_cmd_seq:
        return 0

    print("[P16G2][CMD] 读取cancel seq=%d (上次=%d)" % (seq, _g_last_cmd_seq))
    _g_last_cmd_seq = seq
    _write_heartbeat(date, seq)
    cancels = data.get("cancels", [])
    cancel_data = {"cancels": cancels}
    try:
        _process_cancels(C, cancel_data, date)
    except Exception:
        print("[P16G2][CANCEL-ERR] %s" % traceback.format_exc())

    _archive_cmd_if_same(path, date, seq, "cancel")
    return seq


# ============================================================
# QMT 生命周期
# ============================================================
def init(C):
    """策略初始化。首部set_account确保主推推送；从心跳恢复seq/pending/risk_sold；读外部成本表与peak。"""
    global _g_last_cmd_seq, _g_today, _g_risk_sold
    try:
        C.set_account(ACCOUNT_ID)
    except Exception:
        pass
    _g_today = _today_str()
    # 读取上次已处理seq + 恢复pending孤儿单 + 恢复risk_sold
    heart = _read_json(_heart_path(_g_today))
    if heart:
        _g_last_cmd_seq = int(heart.get("last_cmd_seq_processed", 0) or 0)
        pendings = heart.get("pending", [])
        for p in pendings:
            try:
                sid = p.get("strategy_order_id", "")
                if not sid or sid in _g_pending:
                    continue
                _g_pending[sid] = p
            except Exception:
                pass
        if pendings:
            print("[P16G2][INIT] 从心跳恢复pending %d条" % len(pendings))
        rs = heart.get("risk_sold", [])
        if rs:
            _g_risk_sold = set(str(x) for x in rs)
            print("[P16G2][INIT] 恢复risk_sold %d只" % len(_g_risk_sold))
    # 读外部成本表（外部每日写 cmd/positions_cfg_<date>.json，FIFO 含费成本）
    cfg = _read_json(_positions_cfg_path(_g_today))
    if cfg and str(cfg.get("account_id", "") or "") == ACCOUNT_ID:
        n = 0
        for p in cfg.get("positions", []):
            try:
                bcode = str(p.get("code", "") or "")
                cost = float(p.get("cost", 0) or 0)
                vol = int(p.get("vol", 0) or 0)
                if not bcode or cost <= 0:
                    continue
                qcode = _to_qmt_code(bcode) or bcode
                _g_positions[qcode] = {"cost": cost, "vol": vol, "peak": cost}
                n += 1
            except Exception:
                pass
        if n:
            print("[P16G2][INIT] 外部成本表 %d 条" % n)
    # 恢复追盈历史最高价（state/peak.json，跨日延续）
    pk = _read_json(_peak_path())
    if pk and str(pk.get("account_id", "") or "") == ACCOUNT_ID:
        for code, peak in pk.get("peaks", {}).items():
            try:
                if code in _g_positions:
                    _g_positions[code]["peak"] = float(peak or 0.0)
            except Exception:
                pass
    print("[P16G2][BUILD] BUILD_TAG=%s account=%s bridge=%s" % (BUILD_TAG, ACCOUNT_ID, BRIDGE_DIR))
    print("[P16G2][INIT] date=%s last_seq=%d pending=%d positions=%d risk_sold=%d" % (
        _g_today, _g_last_cmd_seq, len(_g_pending), len(_g_positions), len(_g_risk_sold)))


def handlebar(C):
    """每分钟触发：读指令→下单→反查→pending→快照→心跳。"""
    global _g_last_cmd_seq, _g_today, _g_last_position_refresh, _g_last_asset_refresh

    # 日期切换重置
    today = _today_str()
    if today != _g_today:
        _g_today = today
        _g_last_cmd_seq = 0
        _g_last_position_refresh = 0.0
        _g_last_asset_refresh = 0.0
        _g_pending.clear()
        _g_risk_sold.clear()
        print("[P16G2][NEW-DAY] date=%s pending清空 risk_sold清空" % today)

    # 交易时段判断：09:30-11:30 或 13:00-15:00
    try:
        qtime = C.get_current_time()
    except Exception:
        qtime = time.strftime("%H:%M:%S")
    hour_min = qtime[:5]
    in_session = ("09:30" <= hour_min <= "11:30") or ("13:00" <= hour_min <= "15:00")

    if not in_session:
        # 2026-09-01 教训：此分支必须 try/except 兜底——心跳写失败（文件锁）若不捕获会崩溃整个策略
        try:
            _write_heartbeat(_g_today, _g_last_cmd_seq)
        except Exception:
            print("[P16G2][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())
        return

    # 1) 处理orders指令
    try:
        orders_seq = _process_orders_cmd(C, _g_today)
        if orders_seq > _g_last_cmd_seq:
            _g_last_cmd_seq = orders_seq
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-ORDERS] %s" % traceback.format_exc())

    # 2) 处理cancel指令
    try:
        cancel_seq = _process_cancel_cmd(C, _g_today)
        if cancel_seq > _g_last_cmd_seq:
            _g_last_cmd_seq = cancel_seq
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-CANCEL] %s" % traceback.format_exc())

    # 3) pending 状态机
    try:
        _check_pending_orders(C, _g_today)
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-PENDING] %s" % traceback.format_exc())

    # 3.5) 内置实时风控（止损/止盈/追盈，纯行情驱动）
    try:
        _check_risk_signals(C, _g_today)
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-RISK] %s" % traceback.format_exc())

    # 4) 持仓/资产快照（间隔>=10秒）
    now = time.time()
    if now - _g_last_position_refresh >= POSITION_REFRESH_INTERVAL:
        try:
            _refresh_positions(C, _g_today)
            _g_last_position_refresh = now
        except Exception:
            print("[P16G2][HANDLEBAR-ERR-POS] %s" % traceback.format_exc())
    if now - _g_last_asset_refresh >= POSITION_REFRESH_INTERVAL:
        try:
            _refresh_asset(C, _g_today)
            _g_last_asset_refresh = now
        except Exception:
            print("[P16G2][HANDLEBAR-ERR-ASSET] %s" % traceback.format_exc())

    # 5) 心跳
    try:
        _write_heartbeat(_g_today, _g_last_cmd_seq)
    except Exception:
        print("[P16G2][HANDLEBAR-ERR-HEART] %s" % traceback.format_exc())


def exit(C):
    """日终摘要。"""
    pending_count = len(_g_pending)
    print("[P16G2][EXIT] date=%s 委托=%d 成交=%d 放弃=%d 最终pending=%d" % (
        _g_today, _g_today_order_count, _g_today_fill_count,
        _g_today_abandon_count, pending_count))
    # 写心跳
    try:
        _write_heartbeat(_g_today, _g_last_cmd_seq)
    except Exception:
        pass
