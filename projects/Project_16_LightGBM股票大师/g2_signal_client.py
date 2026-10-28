# -*- coding: utf-8 -*-
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
from __future__ import print_function

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
