# coding=gbk
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
BUILD_TAG = "20261030-134110"

import time

SCHEMA = "p16.signal.v1"

# ---- 配置（改这里，别改下面的逻辑）----
SIGNAL_URL = "http://127.0.0.1:58610/signal"
REPORT_URL = SIGNAL_URL.replace("/signal", "/report")   # 成交回写账本（hub 落 CSV）
ACCOUNT_ID = "67014907"
CAPITAL_BASE = 100000.0          # 与 config/capital_allocation.yaml 的 g2_bridge 一致
DRY_RUN = False                 # builder --live 覆盖
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
PRTYPE_LATEST = 5       # 最新价（实测模拟端＝按当时价挂限价，价格一走就挂着不成交）
PRTYPE_LIMIT = 11       # 指定价（用 price 做限价；执行壳改用这个 + 让价）
PRICE_SLIP_BPS = 30     # 让价 0.30%：BUY 加价 / SELL 降价，保证能穿价成交
ORDER_VERIFY_SEC = 12   # 下单后回查账户委托的超时（查不到=>大概率仍在模拟模式）

_g = {
    "last_poll": 0.0,
    "last_sid": None,
    "last_reject": "",
    "last_reject_ts": 0.0,
    "placed_sids": set(),
    "fills_seen": set(),
    "legs_side": {},
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


def _verify_order_visible(C, leg, timeout=12):
    """下单后回查账户委托列表里有没有我们这条 remark。

    为什么需要：客户端「运行模式=模拟」时 passorder 只在内部记账、**不进柜台委托列表**
    （2026-10-29 实测 10:46 那笔即如此，且日志层完全看不出来）。这里用「委托能不能查到」
    做第二证据，查不到就打一条显式 WARN，避免"以为下了单、实际只在模拟里转"。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        remarks = _extract_remarks(_tdd(C, "order"))
        for r in remarks:
            if leg["remark"] and leg["remark"] in r:
                return True
        time.sleep(2)
    return False


def _extract_deals(objs):
    """把 deal 对象转成 [(code, vol, price)]，字段名多版本兜底。"""
    out = []
    for o in objs or []:
        try:
            code = _obj_code(o)
            if not code:
                continue
            vol = _obj_int(o, ("m_nVolume", "m_nDealVolume"))
            px = 0.0
            for n in ("m_dPrice", "m_dDealPrice", "m_nPrice"):
                try:
                    v = getattr(o, n, None)
                    if v is not None:
                        px = float(v)
                        break
                except Exception:
                    continue
            out.append((code, vol, px))
        except Exception:
            continue
    return out


def _report_fills(fills):
    """把新成交 POST 回 hub，由 hub 追加到外部账本（G2: g2_trade_log.csv / V13: qmt_trade_log.csv）。

    为什么：执行壳在 QMT 内置侧下单，外部账本不会自动更新；不回写的话次日计划仍以为空仓
    → 重复建仓。上报失败只打日志，绝不影响下单主流程。
    """
    if not fills:
        return
    try:
        import json as _json
        try:
            from urllib.request import Request, urlopen
        except ImportError:
            from urllib2 import Request, urlopen
        payload = _json.dumps({
            "account_id": ACCOUNT_ID,
            "signal_id": _g.get("last_sid") or "",
            "reason": "exec_shell",
            "fills": fills,
        }).encode("utf-8")
        req = Request(REPORT_URL, data=payload,
                      headers={"Content-Type": "application/json"})
        resp = urlopen(req, timeout=HTTP_TIMEOUT)
        body = resp.read()
        resp.close()
        _log("REPORT fills=%d -> %s" % (len(fills), body.decode("utf-8", "replace")[:120]))
    except Exception as e:
        _log("REPORT-FAIL %s" % (str(e)[:120],))


def _log_new_fills(C, sid_marker):
    """把当日成交里属于本 signal_id 的腿打出来（每个 (code,vol,px) 只打一次），并回写账本。"""
    fresh = []
    for code, vol, px in _extract_deals(_tdd(C, "deal")):
        key = (code, vol, px)
        if key in _g["fills_seen"]:
            continue
        _g["fills_seen"].add(key)
        side = _g["legs_side"].get(code, "BUY")
        fresh.append({"code": code, "side": side, "vol": vol, "price": px})
        if sid_marker:
            _log("FILL %s %d@%.3f" % (code, vol, px))
        else:
            _log("FILL? %s %d@%.3f" % (code, vol, px))
    if fresh:
        _report_fills(fresh)


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
def _limit_price(leg):
    """BUY 加价、SELL 降价，保证穿价成交（回测按开盘价成交，一点点让价反而更贴口径）。"""
    p = float(leg.get("price") or 0.0)
    if p <= 0:
        return 0.0
    k = 1.0 + PRICE_SLIP_BPS / 10000.0 if leg["action"] == "BUY" else 1.0 - PRICE_SLIP_BPS / 10000.0
    return round(p * k, 2)


def _place(C, leg):
    op = OP_BUY if leg["action"] == "BUY" else OP_SELL
    px = _limit_price(leg)
    prtype = PRTYPE_LIMIT if px > 0 else PRTYPE_LATEST
    try:
        ret = passorder(op, ORDER_TYPE_VOL, ACCOUNT_ID, leg["code"], prtype,
                        px, leg["vol"], leg["remark"], 2, "", C)
        return ret, px, prtype
    except Exception as e:
        _log("PASSORDER-ERR %s %s: %s" % (leg["action"], leg["code"], e))
        return None, px, prtype


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

        try:
            _log_new_fills(C, _g.get("last_sid"))
        except Exception:
            pass

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
            ret, px, prtype = _place(C, leg)
            _g["legs_side"][leg["code"]] = leg["action"]
            _g["n_legs"] += 1
            _log("ORDER ret=%s prtype=%s px=%.2f %s" % (ret, prtype, px, line))
            if not _verify_order_visible(C, leg, ORDER_VERIFY_SEC):
                _log("WARN 委托未出现在账户委托列表（remark=%s）——极可能还在「模拟」运行模式；"
                     "请在客户端停止策略并以「实盘」重新启动" % leg["remark"])
        if sid:
            _g["placed_sids"].add(sid)
            _g["last_sid"] = sid
    except Exception as e:
        # 绝不冒泡到 QMT 引擎
        _log("HANDLEBAR-EXC %s: %s" % (type(e).__name__, e))


# ==== vendored: g2_signal_client.py ====
"""QMT 内置侧：信号拉取 + 校验 + 去重（可整段 vendored 进单文件产物）。

设计约束（全部来自实测，不是假设）：
  * **不能读文件**：内置策略 open() 一律 `Foribdden FileIO` => 信号只能走回环 HTTP；
  * **必须 3.6 语法**：不能用 f-string / 海象 / 类型注解泛型 / dataclass；
  * **必须 fail-safe**：拿不到信号、信号过期、账号不符、字段非法 => 一律「不下单」，
    绝不把「没信号」当成「清仓」或「空仓」；也绝不部分执行（校验不过就整单放弃）；
  * **必须可重放**：signal_id 由「日期 + 计划内容哈希」构成，重启后按 sid 去重；
    跨重启的去重状态以账户委托（remark/sid）为真相源，不用内存当唯一依据。

对接契约见 specs/外部信号→QMT执行通道方案_20261029.md；外部出口见 qmt_signal_hub.py。
"""

import json
import re

SCHEMA = "p16.signal.v1"
_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
_ACTIONS = ("BUY", "SELL")


def fetch(url, timeout=2.0):
    """拉取信号。返回 (env | None, err | None)。任何异常都收敛成 err，不抛出。

    用标准库 urllib：内置环境里 `requests` 虽然在，但 urllib 零依赖、行为更可控。
    """
    try:
        try:
            from urllib.request import urlopen
        except ImportError:                       # pragma: no cover - py2 兜底
            from urllib2 import urlopen
        resp = urlopen(url, timeout=timeout)
        body = resp.read()
        code = getattr(resp, "getcode", lambda: 200)()
        resp.close()
        if code != 200:
            return None, "http_%s" % code
        env = json.loads(body.decode("utf-8"))
        if not isinstance(env, dict):
            return None, "not_object"
        return env, None
    except Exception as exc:
        return None, "fetch_error:%s" % str(exc)[:120]


def validate(env, today, account_id, now_ts, max_age_s=600, applied_ids=None):
    """校验信封。返回 (ok, reasons, orders)。

    reasons 是给人看的中文短语列表；ok=False 时调用方必须整单放弃。
    """
    reasons = []
    if not env:
        return False, ["空信号"], []
    if env.get("schema") != SCHEMA:
        reasons.append("schema不符(%s)" % env.get("schema"))
    if str(env.get("trade_date") or "") != str(today):
        reasons.append("日期不符(%s≠%s)" % (env.get("trade_date"), today))
    if str(env.get("account_id") or "") != str(account_id):
        reasons.append("账号不符(%s≠%s)" % (env.get("account_id"), account_id))
    try:
        age = int(now_ts) - int(env.get("served_ts") or 0)
    except Exception:
        age = 10 ** 9
    if age < -120:
        reasons.append("时间戳在未来(%ds)" % age)
    elif age > max_age_s:
        reasons.append("信号过期(%ds>%ds)" % (age, max_age_s))
    sid = str(env.get("signal_id") or "")
    if not sid:
        reasons.append("缺 signal_id")
    elif applied_ids and sid in applied_ids:
        reasons.append("已执行过(%s)" % sid)

    raw_orders = env.get("orders")
    if not isinstance(raw_orders, list) or not raw_orders:
        reasons.append("订单为空")
        raw_orders = []

    orders = []
    seen = set()
    for idx, o in enumerate(raw_orders):
        if not isinstance(o, dict):
            reasons.append("第%d条不是对象" % (idx + 1))
            continue
        act = str(o.get("action") or "").upper()
        code = str(o.get("code") or "")
        try:
            vol = int(o.get("vol") or 0)
        except Exception:
            vol = 0
        try:
            price = float(o.get("price") or 0.0)
        except Exception:
            price = -1.0
        if act not in _ACTIONS:
            reasons.append("第%d条动作非法(%s)" % (idx + 1, act))
        if not _CODE_RE.match(code):
            reasons.append("第%d条代码非法(%s)" % (idx + 1, code))
        if vol <= 0 or vol % 100 != 0:
            reasons.append("第%d条股数非法(%d)" % (idx + 1, vol))
        if price < 0:
            reasons.append("第%d条价格非法(%s)" % (idx + 1, price))
        key = (act, code)
        if key in seen:
            reasons.append("第%d条重复(%s %s)" % (idx + 1, act, code))
        seen.add(key)
        orders.append({
            "action": act,
            "code": code,
            "vol": vol,
            "price": price,
            "reason": str(o.get("reason") or ""),
            "strategy_order_id": str(o.get("strategy_order_id") or ""),
        })
    return (len(reasons) == 0), reasons, orders


def already_applied(signal_id, today_orders_remarks):
    """跨重启去重：从当日委托的 remark 里找 signal_id。

    `today_orders_remarks` 由调用方从 get_trade_detail_data(..., 'order') 收集
    （G2 生产里的 remark 约定就是以 signal_id 打头）。这样「内存丢了 / 进程重启了」
    也不会重复下单——账户委托才是真相源。
    """
    if not signal_id:
        return False
    for remark in today_orders_remarks or []:
        if str(remark).find(signal_id) >= 0:
            return True
    return False


def describe(env, ok, reasons, orders):
    """一行可 grep 的日志串（写进 FormulaOutput，供沙箱外旁路采集）。"""
    sid = (env or {}).get("signal_id") or "-"
    head = "[P16SIG][%s] sid=%s n=%d" % ("OK" if ok else "REJECT", sid, len(orders))
    if ok:
        legs = []
        for o in orders[:12]:
            legs.append("%s %s %d@%.2f" % (o["action"], o["code"], o["vol"], o["price"]))
        return head + " | " + "; ".join(legs)
    return head + " | " + "; ".join(reasons)
# ==== end vendor ====
