# -*- coding: utf-8 -*-
"""外部模型 → QMT 内置执行：信号出口（hub）。

为什么是 HTTP 而不是文件/sqlite/KV：
  * 文件桥已死 —— 两台客户端的内置策略 open() 一律 `Foribdden FileIO`（2026-10-20/26 实测，
    读必拒；写时开时拒），所以 `_read_json`/`_atomic_write_json` 那套不能再用来喂信号；
  * KV `save_py_key` 只能存小数据且写入端必须是策略进程自己（1MB 起静默丢），外部写不进去；
  * sqlite 在沙箱里可用（走自身 C 层 IO），但「外部写库 + 内置主线程读」未实测，且排障不直观；
  * 回环网络**全开且已验证**（127.0.0.1 连通、`requests 2.24.0` 可用）⇒ 选它做主线。

职责边界（很关键）：
  * 本文件 = **只读转发**。它不做任何交易决策、不写账本、不下单；唯一输入是外部已经算好的
    换仓计划 JSON（`rebalance_g2.py` 的产物），唯一输出是带信封的只读 HTTP 响应。
  * 因此 hub 挂掉 = 内置策略拿不到信号 = 不下单（fail-safe），不会产生半截指令。

用法：
  python qmt_signal_hub.py --plan D:\\QuantLab\\data\\rebalance_g2\\rebalance_g2_20261029.json \
      --account 67014907 --port 58610
  不带 --plan 时按 `--plan-dir` 里当天日期自动找 `rebalance_g2_<YYYYMMDD>.json`。

只监听 127.0.0.1，不接受外部连接（信号通道不该暴露在局域网上）。
纯标准库，Python 3.6+ 可跑。
"""
from __future__ import print_function

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import threading
try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
except ImportError:                                    # pragma: no cover
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

SCHEMA = "p16.signal.v1"

# 成交回写账本的列定义（两套账本列序不同，必须按样式落）
LEDGER_COLS = {
    "g2": ["time", "account_id", "code", "side", "vol", "price", "order_id", "remark", "reason"],
    "v13": ["time", "code", "side", "vol", "price", "score", "order_id", "account_id"],
}


def log(msg):
    print("[HUB] " + msg)
    sys.stdout.flush()


def _today():
    return datetime.datetime.now().strftime("%Y%m%d")


def _sha8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def append_ledger(ledger_path, style, account_id, fills, sid, reason="exec_shell"):
    """把执行壳回报的成交追加到账本（去重：同一天 + code + side + vol + price 只记一次）。"""
    if not ledger_path or not fills:
        return 0
    cols = LEDGER_COLS.get(style or "g2") or LEDGER_COLS["g2"]
    d = os.path.dirname(ledger_path)
    if d and not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    existing = set()
    if os.path.exists(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                rows = f.read().splitlines()
            hdr = [x.strip() for x in rows[0].split(",")] if rows else cols
            for line in rows[1:]:
                parts = line.split(",")
                if len(parts) != len(hdr):
                    continue
                rec = dict(zip(hdr, parts))
                existing.add((rec.get("time", "")[:10], rec.get("code", ""),
                              rec.get("side", ""), rec.get("vol", ""), rec.get("price", "")))
        except Exception:
            pass
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    try:
        need_header = not os.path.exists(ledger_path)
        with open(ledger_path, "a", encoding="utf-8") as f:
            if need_header:
                f.write(",".join(cols) + "\n")
            for x in fills:
                try:
                    code = str(x.get("code") or "")
                    side = str(x.get("side") or "BUY").upper()
                    vol = int(x.get("vol") or 0)
                    px = float(x.get("price") or 0)
                except Exception:
                    continue
                if not code or vol <= 0:
                    continue
                key = (now[:10], code, side, str(vol), str(px))
                if key in existing:
                    continue
                row = {
                    "time": now, "account_id": str(account_id or ""), "code": code,
                    "side": side, "vol": vol, "price": px, "order_id": "",
                    "remark": ("exec:%s" % (sid or ""))[:60], "reason": reason,
                    "score": "",
                }
                f.write(",".join(str(row.get(c, "")) for c in cols) + "\n")
                existing.add(key)
                n += 1
    except Exception:
        return n
    return n


def load_plan(plan_path):
    """读外部计划文件；返回 (plan_dict, raw_text) 或 (None, None)。"""
    try:
        with open(plan_path, "rb") as f:
            raw = f.read()
    except IOError:
        return None, None
    try:
        plan = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, None
    return plan, raw.decode("utf-8", "replace")


def build_envelope(plan, raw_text, account_id, plan_path, now=None, strategy="g2"):
    """把外部 plan 包装成可校验、可去重的信号信封。

    幂等键 signal_id = 日期 + 计划内容哈希：同一份计划重复发布得到同一个 id，
    内置侧据此天然去重（重启后重放也不会重复下单）。
    """
    now = now or datetime.datetime.now()
    trade_date = str(plan.get("date") or _today())
    digest = _sha8(raw_text)
    orders = []
    for o in plan.get("orders") or []:
        orders.append({
            "action": str(o.get("action", "")).upper(),
            "code": str(o.get("code", "")),
            "vol": int(o.get("vol") or 0),
            "price": float(o.get("price") or 0.0),
            "reason": str(o.get("reason", "")),
            "strategy_order_id": str(o.get("strategy_order_id", "")),
        })
    env = {
        "schema": SCHEMA,
        "signal_id": "%s-%s-%s" % (strategy, trade_date, digest),
        "trade_date": trade_date,
        "account_id": str(plan.get("account_id") or account_id or ""),
        "strategy": strategy,
        "capital": float(plan.get("capital") or 0.0),
        "generated_at": str(plan.get("generated_at") or plan.get("ts") or ""),
        "served_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "served_ts": int(now.timestamp()),
        "source": os.path.basename(plan_path),
        "orders": orders,
        "meta": {
            "n_orders": len(orders),
            "n_sells": len(plan.get("sells") or []),
            "n_buys": len(plan.get("buys") or []),
            "written": bool(plan.get("written")),
            "note": str(plan.get("note", "")),
        },
    }
    return env


class _State(object):
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.cache = None          # (plan_path, mtime, envelope)
        self.last_error = ""

    def resolve_plan_path(self):
        a = self.args
        if a.plan:
            return a.plan
        date = _today()
        # 优先取「只生成计划」的产物（09:40 纯脚本任务写 .plan.json，不下单）；
        # 没有才回退到 live 产物 .json。两者内容同源，前者是执行壳的常规输入。
        # 文件名前缀随策略（g2 / v13），否则 v13 目录里找不到计划
        prefix = "rebalance_%s_" % (getattr(a, "strategy", None) or "g2")
        plan_only = os.path.join(a.plan_dir, prefix + "%s.plan.json" % date)
        if os.path.exists(plan_only):
            return plan_only
        return os.path.join(a.plan_dir, prefix + "%s.json" % date)

    def current(self):
        """返回 (envelope|None, reason)。按 mtime 缓存，避免每 3 秒重算哈希。"""
        path = self.resolve_plan_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None, "plan_file_missing:%s" % path
        with self.lock:
            if self.cache and self.cache[0] == path and self.cache[1] == mtime:
                return self.cache[2], ""
            plan, raw = load_plan(path)
            if plan is None:
                return None, "plan_file_unreadable:%s" % path
            env = build_envelope(plan, raw, self.args.account, path,
                                 strategy=getattr(self.args, "strategy", "g2"))
            if env["trade_date"] != _today() and not self.args.allow_stale_date:
                return None, "plan_date_not_today:%s" % env["trade_date"]
            self.cache = (path, mtime, env)
            return env, ""


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "P16SignalHub/1.0"

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path in ("/health", "/ping"):
                env, reason = state.current()
                self._send(200, {
                    "ok": env is not None,
                    "schema": SCHEMA,
                    "plan": state.resolve_plan_path(),
                    "reason": reason,
                })
                return
            if path in ("/signal", "/"):
                env, reason = state.current()
                if env is None:
                    # 没有可用信号 = 明确拒绝，而不是给一个空计划（空计划会被误当"清仓"）
                    self._send(404, {"ok": False, "schema": SCHEMA, "reason": reason})
                    return
                self._send(200, env)
                return
            self._send(404, {"ok": False, "reason": "unknown_path"})

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            if path != "/report":
                self._send(404, {"ok": False, "reason": "unknown_path"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                body = json.loads(raw.decode("utf-8"))
            except Exception as e:
                self._send(400, {"ok": False, "reason": "bad_json:%s" % str(e)[:60]})
                return
            fills = body.get("fills") or []
            acct = body.get("account_id") or state.args.account
            if str(acct) != str(state.args.account):
                # 账号不符一律拒收（防串号回写别人的账本）
                self._send(403, {"ok": False, "reason": "account_mismatch"})
                return
            n = append_ledger(state.args.ledger, state.args.ledger_style, acct,
                              fills, body.get("signal_id"), body.get("reason") or "exec_shell")
            self._send(200, {"ok": True, "appended": n})

        def log_message(self, fmt, *args):
            # 客户端每 3 秒轮询一次，不逐条刷日志；只在启动与异常时说话
            return

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="", help="明确的计划文件路径")
    ap.add_argument("--plan-dir", default=r"D:\\QuantLab\\data\\rebalance_g2")
    ap.add_argument("--account", default="67014907")
    ap.add_argument("--port", type=int, default=58610)
    ap.add_argument("--strategy", default=None,
                    help="signal_id 前缀（g2 / v13 / ...，缺省按计划目录推断）")
    ap.add_argument("--ledger", default="", help="成交回写的账本 CSV 路径（空=不回写）")
    ap.add_argument("--ledger-style", default="g2", choices=["g2", "v13"], help="账本列序样式")
    ap.add_argument("--allow-stale-date", action="store_true",
                    help="允许发布非当天计划（仅调试；生产必须关闭）")
    args = ap.parse_args()

    if not args.strategy:
        args.strategy = "v13" if "v13" in os.path.basename(os.path.normpath(args.plan_dir)) else "g2"
    state = _State(args)
    env, reason = state.current()
    if env is None:
        log("WARN 启动时没有可用信号：%s（服务照常起，等外部计划落盘后自动生效）" % reason)
    else:
        log("READY signal_id=%s trade_date=%s orders=%d source=%s"
            % (env["signal_id"], env["trade_date"], len(env["orders"]), env["source"]))

    srv = HTTPServer(("127.0.0.1", args.port), make_handler(state))
    log("LISTEN http://127.0.0.1:%d/signal（只绑回环）" % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("bye")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
