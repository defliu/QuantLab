# coding=utf-8
"""STRATEGY_P16_G2_EXEC —— 外部模型信号的 QMT 内执行壳（2026-10-29 立）。

定位：**本文件不做任何选股/评分**。组合由外部（Python 3.11 + D:/astock + LightGBM）
算好后经回环 HTTP 送来，这里只负责「校验 → 过闸 → 下单 → 打日志」。

为什么这样拆（实测依据，见 specs/外部信号→QMT执行通道方案_20261029.md）：
  * 内置侧拿不到干净特征源（get_finance 无属性 / get_financial_data 全 nan /
    FloatVolume 18/20 失真 / turnover 接口空）=> 信号内产不可用；
  * 内置 open() 一律 Foribdden FileIO => 文件桥不可用；回环网络全开 => 走 HTTP。

安全口径（每一条都不可省）：
  1. DRY_RUN 默认 True：只在日志里写明「本应下什么单」，不产生任何委托；
  2. 时段闸：仅 09:30–11:30 / 13:00–14:57 允许下单（收盘后一律拒绝）；
  3. 校验不过整单放弃：schema/日期/账号/过期/字段/重复腿/空订单，见 g2_signal_client；
  4. 幂等：remark = signal_id + "-" + 腿序号；当日委托 remark 已含 signal_id 即视为执行过，
     进程重启也不会重复下单（账户委托是真相源，不依赖内存）；
  5. 只执行计划里出现的代码：**绝不**清理「不在计划里的持仓」（那是别的策略的票）；
  6. 卖腿按账户可卖量夹取；买腿总额不得超过 CAPITAL_BASE（防计划损坏导致超量买入）；
  7. 任何异常只打印，不抛出——绝不能把异常冒到 QMT 引擎里去。

部署：`python build_p16_g2_exec.py` → `build/strategy_p16_g2_exec.py`（GBK）→
`qmt_autodeploy.py --product ... --model STRATEGY_P16_G2_EXEC --qmt-dir D:\QMT交易端模拟`。
**新模型名需先在客户端手工注册一次**（任意内容的同名策略即可）。
"""
BUILD_TAG = "00000000-000000"

import time

SCHEMA = "p16.signal.v1"

# ---- 配置（改这里，别改下面的逻辑）----
SIGNAL_URL = "http://127.0.0.1:58610/signal"
ACCOUNT_ID = "67014907"
CAPITAL_BASE = 100000.0          # 与 config/capital_allocation.yaml 的 g2_bridge 一致
DRY_RUN = True                   # ★ 首次部署必须保持 True，确认日志无误后再改 False 重新构建
# ★ 执行窗口：必须与回测口径一致。G2 的回测成交价取「开盘价」，实盘既有确认口径是
#   09:50 触发（原换仓时点）、09:58 未成则当日放弃——见 build/strategy_p16_g2_selfcontained.py
#   的 SIGNAL_HM / SIGNAL_DEADLINE_HM。执行壳照抄这个窗口，**不允许**在盘中任意时刻执行：
#   10:30 拿到的信号若照单执行，成交价就与回测口径脱钩了。
EXEC_HM_START = 950
EXEC_HM_END = 958
POLL_MIN_SEC = 5.0               # handlebar 内最小轮询间隔
HTTP_TIMEOUT = 2.0               # 拉信号超时（绝不允许拖住 handlebar）
MAX_SIGNAL_AGE_SEC = 600         # 信号超过 10 分钟即视为过期
PRICE_TOLERANCE = 1.02           # 买腿总额允许上浮 2%（价格漂移）

# passorder 常量（与 G2 自包含版一致：prType=5 最新价，第8参 remark，第9参 2，第11参 C）
OP_BUY = 23
OP_SELL = 24
ORDER_TYPE_VOL = 1101
PRTYPE_LATEST = 5

_g = {
    "last_poll": 0.0,
    "last_sid": None,
    "last_reject": "",
    "last_reject_ts": 0.0,
    "placed_sids": set(),
    "n_legs": 0,
}


# ============================================================
# 基础工具
# ============================================================
def _log(msg):
    print("[P16SIG][EXEC] " + str(msg))


def _qmt_now(C):
    """取 QMT 行情时间。返回 (date_str, hhmm_int, epoch_ts)。

    绝不用 datetime.now()：设备 CMOS 可能错乱（AGENTS 实盘执行红线）。取不到时降级为
    本机时间并打标，宁可少交易也不误交易。
    """
    t = None
    try:
        t = C.get_current_time()
    except Exception:
        t = None
    if t is None:
        try:
            t = time.localtime()
            src = "LOCAL"
        except Exception:
            return "", -1, int(time.time())
    else:
        src = "QMT"
    if hasattr(t, "tm_year"):                   # time.struct_time（QMT 实测就返回这个）
        d = "%04d%02d%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
        hm = t.tm_hour * 100 + t.tm_min
        try:
            ts = int(time.mktime(t))
        except Exception:
            ts = int(time.time())
        return d, hm, ts
    if hasattr(t, "year"):                      # datetime / date
        d = "%04d%02d%02d" % (t.year, t.month, t.day)
        hm = t.hour * 100 + t.minute
        try:
            ts = int(t.timestamp())
        except Exception:
            ts = int(time.time())
        return d, hm, ts
    # ★ 2026-10-29 修：C.get_current_time() 不保证是 'YYYYMMDDHHMMSS' 纯数字串
    #   （实测拿到过含 'product_' 的对象 repr，直接 int(s[8:12]) 抛 ValueError，
    #   结果 handlebar 每轮都异常、永远不下单）。只接受「前 12 位全是数字」的串，
    #   其余一律退回本机时间并把原始值打一次，便于事后定位。
    s = str(t)
    if len(s) >= 12 and s[:12].isdigit():
        return s[:8], int(s[8:12]), int(time.time())
    if s.isdigit() and len(s) >= 14:            # 可能是秒级时间戳
        try:
            lt = time.localtime(int(s[:10]))
            return time.strftime("%Y%m%d", lt), lt.tm_hour * 100 + lt.tm_min, int(time.time())
        except Exception:
            pass
    if not _g.get("bad_time_logged"):
        _g["bad_time_logged"] = True
        _log("WARN C.get_current_time() 返回不可解析值 %r，退回本机时间" % (s[:80],))
    lt = time.localtime()
    return time.strftime("%Y%m%d", lt), lt.tm_hour * 100 + lt.tm_min, int(time.time())


def in_session(hhmm):
    """连续竞价时段：09:30–11:30 / 13:00–14:57。收盘后一律不下单。"""
    if hhmm < 0:
        return False
    return (930 <= hhmm <= 1130) or (1300 <= hhmm <= 1457)


def in_exec_window(hhmm):
    """执行窗口 09:50–09:58（与回测口径对齐，见 EXEC_HM_START 注释）。"""
    if hhmm < 0:
        return False
    return EXEC_HM_START <= hhmm <= EXEC_HM_END


def _extract_positions(objs):
    """把 QMT position 对象列表转成 {code: {'vol':x,'can_use':y}}，字段名多版本兜底。"""
    out = {}
    for o in objs or []:
        try:
            code = _obj_code(o)
            if not code:
                continue
            vol = _obj_int(o, ("m_nVolume", "m_nVol", "m_nCurrentVolume"))
            can = _obj_int(o, ("m_nCanUseVolume", "m_nCanUseVol", "m_nAvailableVolume"))
            if can <= 0:
                can = vol
            out[code] = {"vol": vol, "can_use": can}
        except Exception:
            continue
    return out


def _obj_code(o):
    try:
        ins = getattr(o, "m_strInstrumentID", "") or getattr(o, "m_strCode", "")
        ex = getattr(o, "m_strExchangeID", "") or getattr(o, "m_strExchange", "")
        if ins and ex:
            return "%s.%s" % (ins, ex.upper())
        return str(ins or "")
    except Exception:
        return ""


def _obj_int(o, names):
    for n in names:
        try:
            v = getattr(o, n, None)
            if v is not None:
                return int(v)
        except Exception:
            continue
    return 0


def _extract_remarks(objs):
    out = []
    for o in objs or []:
        for n in ("m_strRemark", "m_strOrderRef", "m_strRemarkEx"):
            try:
                v = getattr(o, n, None)
                if v:
                    out.append(str(v))
                    break
            except Exception:
                continue
    return out


def _tdd(C, kind):
    """get_trade_detail_data 兜底调用（全局函数优先，其次 C 方法）。"""
    f = globals().get("get_trade_detail_data")
    try:
        if f is not None:
            return f(ACCOUNT_ID, "stock", kind)
        g = getattr(C, "get_trade_detail_data", None)
        if g is not None:
            return g(ACCOUNT_ID, "stock", kind)
    except Exception as e:
        _log("TD-ERR %s: %s" % (kind, e))
    return []


# ============================================================
# 决策（纯函数，离线可测）
# ============================================================
def plan_legs(env, today, hhmm, positions, applied_sids, dry_run):
    """把信号信封转成「本 tick 要下的腿」。

    返回 (legs, info)。legs 为空时 info 是人话原因（写日志用）。
    纯函数：不碰 C、不碰网络、不写全局状态 => 可在 CPython 3.11 里直接回归。
    """
    if not env:
        return [], "无信号"
    if env.get("schema") != SCHEMA:
        return [], "schema不符(%s)" % env.get("schema")
    if str(env.get("trade_date") or "") != str(today):
        return [], "日期不符(%s≠%s)" % (env.get("trade_date"), today)
    if str(env.get("account_id") or "") != ACCOUNT_ID:
        return [], "账号不符(%s)" % env.get("account_id")
    sid = str(env.get("signal_id") or "")
    if not sid:
        return [], "缺 signal_id"
    if applied_sids and sid in applied_sids:
        return [], "已执行(%s)" % sid
    if not in_exec_window(hhmm):
        return [], "非执行窗口(%s，要求%d-%d)" % (hhmm, EXEC_HM_START, EXEC_HM_END)

    raw = env.get("orders")
    if not isinstance(raw, list) or not raw:
        return [], "订单为空"          # 空订单 ≠ 清仓

    legs = []
    notes = []
    seen = set()
    buy_notional = 0.0
    for idx, o in enumerate(raw):
        act = str(o.get("action") or "").upper()
        code = str(o.get("code") or "")
        try:
            vol = int(o.get("vol") or 0)
        except Exception:
            vol = 0
        try:
            price = float(o.get("price") or 0.0)
        except Exception:
            price = 0.0
        if act not in ("BUY", "SELL") or not code or vol <= 0 or vol % 100 != 0:
            return [], "第%d条非法(%s %s %d)" % (idx + 1, act, code, vol)
        key = (act, code)
        if key in seen:
            return [], "第%d条重复(%s %s)" % (idx + 1, act, code)
        seen.add(key)
        if act == "SELL":
            pos = positions.get(code) or {}
            can = int(pos.get("can_use") or 0)
            if can <= 0:
                # 可能是本账号里另一策略（V1.3）刚挂出卖单占用了可卖量。**留痕跳过**，
                # 不静默缩量——第二天对账才看得出这是账户竞争而不是信号错误。
                notes.append("SKIP %s 无可用量(计划=%d)" % (code, vol))
                continue
            if vol > can:
                notes.append("TRIM %s %d->%d(可用=%d)" % (code, vol, (can // 100) * 100, can))
                vol = (can // 100) * 100
            if vol <= 0:
                continue
        else:
            buy_notional += price * vol
        legs.append({
            "action": act,
            "code": code,
            "vol": vol,
            "price": price,
            "remark": "%s-%d" % (sid, idx + 1),
            "reason": str(o.get("reason") or ""),
        })
    if buy_notional > CAPITAL_BASE * PRICE_TOLERANCE:
        return [], "买腿总额超限(%.0f>%.0f)" % (buy_notional, CAPITAL_BASE * PRICE_TOLERANCE)
    if not legs:
        return [], "无可执行腿（卖腿均无可卖量/其余为空）"
    if notes:
        return legs, "ok sid=%s n=%d | %s" % (sid, len(legs), "; ".join(notes))
    return legs, "ok sid=%s n=%d" % (sid, len(legs))


# ============================================================
# 下单
# ============================================================
def _place(C, leg):
    op = OP_BUY if leg["action"] == "BUY" else OP_SELL
    try:
        ret = passorder(op, ORDER_TYPE_VOL, ACCOUNT_ID, leg["code"], PRTYPE_LATEST,
                        leg["price"], leg["vol"], leg["remark"], 2, "", C)
        return ret
    except Exception as e:
        _log("PASSORDER-ERR %s %s: %s" % (leg["action"], leg["code"], e))
        return None


# ============================================================
# 生命周期
# ============================================================
def init(C):
    _log("INIT BUILD_TAG=%s account=%s dry_run=%s url=%s capital=%.0f"
         % (BUILD_TAG, ACCOUNT_ID, DRY_RUN, SIGNAL_URL, CAPITAL_BASE))
    if DRY_RUN:
        _log("MODE=DRY_RUN 只打日志不下单（确认后把 DRY_RUN 改 False 重新构建部署）")


def handlebar(C):
    try:
        now = time.time()
        if now - _g["last_poll"] < POLL_MIN_SEC:
            return
        _g["last_poll"] = now

        today, hhmm, ts = _qmt_now(C)
        if not in_session(hhmm) and not DRY_RUN:
            # 非交易时段静默（每 10 分钟最多说一次，避免刷屏）
            if now - _g.get("off_ts", 0) > 600:
                _g["off_ts"] = now
                _log("OFFSESSION hhmm=%s 不下单" % hhmm)
            return

        env, err = fetch(SIGNAL_URL, HTTP_TIMEOUT)
        if env is None:
            if err != _g["last_reject"] or now - _g["last_reject_ts"] > 60:
                _g["last_reject"] = err
                _g["last_reject_ts"] = now
                _log("REJECT fetch: %s" % err)
            return

        positions = _extract_positions(_tdd(C, "position"))
        remarks = _extract_remarks(_tdd(C, "order"))
        applied = set(_g["placed_sids"])
        sid = str(env.get("signal_id") or "")
        if sid and already_applied(sid, remarks):
            applied.add(sid)

        legs, info = plan_legs(env, today, hhmm, positions, applied, DRY_RUN)
        if not legs:
            if info != _g.get("last_info"):
                _g["last_info"] = info
                _log("SKIP %s" % info)
            return
        _g["last_info"] = info
        _log("PLAN %s legs=%d" % (info, len(legs)))

        for leg in legs:
            line = "%s %s %d@%.2f remark=%s reason=%s" % (
                leg["action"], leg["code"], leg["vol"], leg["price"],
                leg["remark"], leg["reason"])
            if DRY_RUN:
                _log("DRY " + line)
                continue
            ret = _place(C, leg)
            _g["n_legs"] += 1
            _log("ORDER ret=%s %s" % (ret, line))
        if sid:
            _g["placed_sids"].add(sid)
            _g["last_sid"] = sid
    except Exception as e:
        # 绝不冒泡到 QMT 引擎
        _log("HANDLEBAR-EXC %s: %s" % (type(e).__name__, e))


# ==== vendored: g2_signal_client.py ====
# （构建时由 build_p16_g2_exec.py 注入 fetch/validate/already_applied/describe）
# ==== end vendor ====
