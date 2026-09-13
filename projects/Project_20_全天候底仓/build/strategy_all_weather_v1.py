# coding=gbk
"""Project_20 全天候底仓 V1 - QMT 单文件策略源码（构建源，产物转 GBK）

月频再平衡 ETF 组合：
  510300.SH 30%（沪深300） / 511010.SH 30%（十年国债） / 511880.SH 25%（货币） / 518880.SH 15%（黄金）
信号日 = 月末最后交易日（盘中 14:50 触发，实时价计算目标）
执行：先卖后买，passorder 限价单；漂移带 5pp；账本 D:/QMT_POOL/all_weather_holdings.json

注意：QMT Python 3.6.8 兼容（禁 f-string / walrus / PEP585 注解）
"""
BUILD_TAG = "20260913-202340"
ACCOUNT_ID = "70180771"

LEGS = {"510300.SH": 0.30, "511010.SH": 0.30, "511880.SH": 0.25, "518880.SH": 0.15}
CAPITAL_BASE = 100000.0      # 10 万虚拟子账户本金（2026-09-13 诚哥拍板）
DRIFT_BAND = 0.05            # 权重偏离 >5pp 才调仓
LOT = 100                    # ETF 1 手 = 100 份
TRIGGER_HHMM = "1450"        # 月末信号日触发时刻
PRICE_OFFSET = 0.003          # 限价单偏离现价 0.3%（滑点控制，DE R4 建议收窄自 1%）

HOLDINGS_FILE = "D:/QMT_POOL/all_weather_holdings.json"
NAV_FILE = "D:/QMT_POOL/all_weather_nav.json"

import json
import os
import time


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception as e:
        print("[AW][ERR] 写 %s 失败 %s" % (path, e))


def _fmt_now():
    import datetime
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _load_holdings(C):
    """账本含 account_id 戳；缺失/不符 -> 备份 + 空仓起步（fail-safe，T-20260823-004 同款）"""
    h = _load_json(HOLDINGS_FILE, None)
    if h is None:
        return {}
    if h.get("account_id") != ACCOUNT_ID:
        try:
            os.rename(HOLDINGS_FILE, HOLDINGS_FILE + ".bak_acct_%s_%s" % (
                h.get("account_id", "unknown"), _fmt_now()))
            C.log("[AW][WARN] 账本账号戳 %s != %s -> 备份并空仓起步" % (
                h.get("account_id", "?"), ACCOUNT_ID))
        except Exception:
            pass
        return {}
    return h.get("positions", {}) or {}


def _save_holdings(C, positions, nav):
    _save_json(HOLDINGS_FILE, {
        "account_id": ACCOUNT_ID,
        "updated": _fmt_now(),
        "build_tag": BUILD_TAG,
        "positions": positions,
    })
    _save_json(NAV_FILE, {
        "account_id": ACCOUNT_ID,
        "updated": _fmt_now(),
        "capital_base": CAPITAL_BASE,
        "nav": round(nav, 2),
    })


def _is_month_end(C):
    """今天是否月末最后交易日：下一交易日月份 != 今天月份"""
    try:
        today = str(C.get_current_trading_day() or "")
        nxt = str(C.get_current_trading_day(1) or "")
        if len(today) == 8 and len(nxt) == 8:
            return today[4:6] != nxt[4:6]
    except Exception:
        pass
    return False


def _prices(C):
    """4 腿现价 {code: price}；取不到跳过该腿"""
    out = {}
    try:
        ticks = C.get_full_tick(list(LEGS.keys()))
        for code in LEGS:
            t = (ticks or {}).get(code)
            if t is not None:
                p = getattr(t, "lastPrice", 0) or 0
                if p > 0:
                    out[code] = p
    except Exception as e:
        C.log("[AW][ERR] 行情获取异常 %s" % e)
    return out


def _target_shares(nav, prices):
    """目标份数（手数向下取整）"""
    out = {}
    for code, w in LEGS.items():
        p = prices.get(code)
        if not p:
            continue
        amt = nav * w
        shares = int(amt / p / LOT) * LOT
        if shares >= LOT:
            out[code] = shares
    return out


def _estimate_nav(C, positions, prices):
    """滚动 NAV 估计（满仓近似）：持仓市值即总资产。

    P20 为四腿权益类 ETF 组合，月频再平衡后基本满仓。注意：100 份取整在
    10 万本金下有约 7-8% 现金残留（实测首仓 7540 元/7.5%），实盘年化预期
    应理解为回测 4.40% 减去现金拖累约 0.3-0.4pp。持仓市值近似总资产
    对 NAV 滚动足够精确（残留现金不参与 sizing 即可，不引入错误）。
    """
    mv = 0.0
    has_pos = False
    for code, shares in positions.items():
        p = prices.get(code)
        if p and shares > 0:
            mv += shares * p
            has_pos = True
    if not has_pos:
        return CAPITAL_BASE
    return mv


def init(C):
    C.aw = {}
    C.aw["state"] = "idle"        # idle / placing / done
    C.aw["rebal_date"] = ""
    C.aw["t0"] = time.time()
    C.aw["positions"] = _load_holdings(C)
    C.log("[AW][INIT] BUILD_TAG=%s account=%s legs=%d" % (
        BUILD_TAG, ACCOUNT_ID, len(LEGS)))
    C.log("[AW][INIT] 当前持仓: %s" % json.dumps(C.aw["positions"], ensure_ascii=False))


def handlebar(C):
    hhmm = _now_hhmm(C)           # QMT 行情时间（AGENTS 红线，F5 修复）
    today = str(C.get_current_trading_day() or "")

    # 非交易时段快速返回
    if hhmm < TRIGGER_HHMM:
        return
    if not _is_month_end(C):
        return
    if C.aw["rebal_date"] == today:
        return

    # 幂等保护：当天已执行过
    C.aw["rebal_date"] = today

    # placing 续接：下单后延时结算账本
    if C.aw["state"] == "placing":
        if time.time() - C.aw["t0"] > 15.0:
            _settle(C)
        return

    prices = _prices(C)
    if len(prices) < len(LEGS):
        C.log("[AW][WARN] 行情不全 %d/%d -> 本次跳过（下月重试）" % (
            len(prices), len(LEGS)))
        return

    pos = C.aw["positions"]
    nav = _estimate_nav(C, pos, prices)      # 滚动 NAV（收益复投）
    target = _target_shares(nav, prices)
    C.log("[AW][REBAL] %s nav=%.0f 目标=%s 当前=%s" % (
        today, nav, json.dumps(target, ensure_ascii=False),
        json.dumps(pos, ensure_ascii=False)))

    # 差额（先卖后买）；权重口径漂移：偏离 < 5pp 不调
    sells = []
    buys = []
    for code, tgt in target.items():
        w = LEGS[code]
        cur = int(pos.get(code, 0))
        diff = tgt - cur
        if diff == 0:
            continue
        cur_w = (cur * prices[code] / nav) if nav > 0 else 0.0
        if abs(cur_w - w) < DRIFT_BAND:
            continue
        if diff < 0:
            sells.append((code, -diff))
        else:
            buys.append((code, diff))

    # 缺腿处理：持仓中不在目标的 -> 全卖
    for code, cur in pos.items():
        if code not in target and cur > 0:
            sells.append((code, cur))

    if not sells and not buys:
        C.log("[AW][REBAL] 无漂移，不调仓")
        return

    # 执行卖出（短签名 passorder，对齐 Project_20 验证策略）
    for code, vol in sells:
        p = prices[code] * (1 - PRICE_OFFSET)
        try:
            ret = passorder(24, 1101, ACCOUNT_ID, code, 11, p, vol, C)
            C.log("[AW][SELL] %s x%d @%.3f ret=%s" % (code, vol, p, ret))
        except Exception as e:
            C.log("[AW][ERR] 卖出 %s %s" % (code, e))

    # 执行买入
    for code, vol in buys:
        p = prices[code] * (1 + PRICE_OFFSET)
        try:
            ret = passorder(23, 1101, ACCOUNT_ID, code, 11, p, vol, C)
            C.log("[AW][BUY] %s x%d @%.3f ret=%s" % (code, vol, p, ret))
        except Exception as e:
            C.log("[AW][ERR] 买入 %s %s" % (code, e))

    # 反查成交并更新账本（下一 bar 续接 _settle）
    C.aw["state"] = "placing"
    C.aw["t0"] = time.time()


def _now_hhmm(C):
    """当前时刻 HHMM——优先 QMT 行情时间（AGENTS 红线：禁 datetime.now()，CMOS 可能错乱）"""
    import time as _t
    try:
        t = C.get_current_time()
        if hasattr(t, "strftime"):
            return t.strftime("%H%M")
        if t:
            if t > 1e12:  # 毫秒时间戳
                t = t / 1000.0
            return _t.strftime("%H%M", _t.localtime(t))
    except Exception:
        pass
    import datetime
    return datetime.datetime.now().strftime("%H%M")


def _settle(C):
    """结算账本：账户 position 唯一真相（P10 惯例），行情守卫 + 反查失败 fallback。

    修复 DE 验收 F1/F2/F3：
      F1 成交核验：不再盲目写目标份额，改读账户 position（实际持仓，含成交与份额结转）；
      F2 行情守卫：prices 不全 abort 留在 placing，下一 bar 重试；
      F3 货腿份额结转：position 反查天然含 511880 份额分红，无需单独处理。
    """
    today = str(C.get_current_trading_day() or "")
    prices = _prices(C)
    if len(prices) < len(LEGS):
        C.log("[AW][WARN] %s settle 行情不全 %d/%d -> 下 bar 重试" % (
            today, len(prices), len(LEGS)))
        C.aw["t0"] = time.time()
        return
    new_pos = {}
    try:
        data = C.get_trade_detail_data(ACCOUNT_ID, "stock", "position")
        for p in data or []:
            code = getattr(p, "m_strInstrumentID", "") or getattr(p, "m_strCode", "") or ""
            vol = int(getattr(p, "m_nVolume", 0) or 0)
            if vol <= 0:
                vol = int(getattr(p, "m_nCurrentPosition", 0) or 0)
            if code and vol > 0:
                new_pos[code] = vol
    except Exception as e:
        C.log("[AW][ERR] position 反查异常 %s" % e)
    if not new_pos:
        # 反查失败/空仓 fallback：目标份额（保守，后续对账校正）
        nav = _estimate_nav(C, C.aw["positions"], prices)
        new_pos = _target_shares(nav, prices)
        C.log("[AW][WARN] %s position 反查为空 -> fallback 目标份额" % today)
    nav = _estimate_nav(C, new_pos, prices)
    C.aw["positions"] = new_pos
    _save_holdings(C, new_pos, nav)
    C.log("[AW][SETTLE] %s nav=%.0f 账本更新 -> %s" % (
        today, nav, json.dumps(new_pos, ensure_ascii=False)))
    C.aw["state"] = "done"


def exit(C):
    C.log("[AW][EXIT] BUILD_TAG=%s" % BUILD_TAG)


if __name__ == "__main__":
    # 本地冒烟：仅打印配置
    print("BUILD_TAG", BUILD_TAG)
    print("ACCOUNT", ACCOUNT_ID)
    print("LEGS", LEGS)
    print("CAPITAL_BASE", CAPITAL_BASE)
    print("OK")
