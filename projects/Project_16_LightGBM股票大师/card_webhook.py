# coding: utf-8
"""飞书卡片回调服务：监听 card.action.trigger，处理「加入关注」按钮回调并落库。

部署步骤：
  1. 本机启动：python card_webhook.py --port 9001
  2. 用 frp / ngrok / 云服务器反向代理，把公网地址映射到本机 9001 端口
  3. 飞书开放平台 → 应用 →「事件与回调」→ 添加「卡片回传交互(card.action.trigger)」，
     请求地址填公网 URL（形如 https://your-domain/card）
  4. 把该页面显示的 Encrypt Key 填入 qmt_config.LARK_ENCRYPT_KEY，重启本服务启用验签

安全：
  - LARK_ENCRYPT_KEY 为空时跳过验签（仅限本机调试）；公网部署必须配置，
    否则任何人都能伪造回调往关注列表塞标。
  - 回调落库文件：data/qmt_watch_cards.json（追加式，供盯盘合并与审计）。
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import qmt_config as C


# ---- 落库：加入关注的标的 ----

def load_watch_items():
    """读取关注落库列表（结构 {items:[{symbol,open_id,time}]}）。文件缺失/损坏返回空列表。"""
    if not os.path.exists(C.WATCH_CARD_FILE):
        return []
    try:
        with open(C.WATCH_CARD_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("items", []))
    except Exception as e:
        print(f"    !! 关注列表读取失败: {e!r}")
        return []


def save_watch_items(items):
    """整表写回关注落库文件（先备份旧文件，防写坏）。"""
    os.makedirs(os.path.dirname(C.WATCH_CARD_FILE), exist_ok=True)
    if os.path.exists(C.WATCH_CARD_FILE):
        try:
            os.replace(C.WATCH_CARD_FILE, C.WATCH_CARD_FILE + ".bak")
        except OSError:
            pass
    with open(C.WATCH_CARD_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)


def add_watch_item(symbol, open_id=""):
    """把标的加入关注列表（去重：同 symbol 覆盖时间与操作人）。返回 (已新增/已存在, items)。"""
    items = load_watch_items()
    existed = any(it.get("symbol") == symbol for it in items)
    items = [it for it in items if it.get("symbol") != symbol]
    items.append({
        "symbol": symbol,
        "open_id": open_id,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_watch_items(items)
    return (not existed), items


# ---- 飞书回调签名校验 ----

def _verify_signature(ts, nonce, signature, raw_body):
    """飞书卡片回传交互验签：
    string_to_sign = timestamp + nonce + encrypt_key + request_body
    signature = base64(hmac_sha256(string_to_sign, encrypt_key))
    未配置 LARK_ENCRYPT_KEY 时返回 True（开发模式跳过）。"""
    encrypt_key = getattr(C, "LARK_ENCRYPT_KEY", "") or ""
    if not encrypt_key:
        return True
    if not (ts and nonce and signature):
        return False
    string_to_sign = f"{ts}{nonce}{encrypt_key}{raw_body}"
    digest = hmac.new(encrypt_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    expect = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expect, signature)


# ---- 事件处理 ----

def handle_card_trigger(payload):
    """处理 card.action.trigger 回调。返回飞书要求的响应 dict。"""
    action = payload.get("action") or {}
    value = action.get("value") or {}
    act = value.get("action", "")
    symbol = value.get("symbol") or value.get("code") or ""
    open_id = payload.get("open_id") or ""
    operator = payload.get("operator") or {}
    op_name = operator.get("name") or open_id

    if act == "add_watch" and symbol:
        is_new, items = add_watch_item(symbol, open_id)
        n = len(items)
        status = "已加入盯盘" if is_new else "已在关注列表"
        print(f"    [回调] {op_name} -> {symbol} | {status} | 共 {n} 条")
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "toast": {"type": "success", "content": f"{symbol} {status}（共 {n} 只）"}
            },
        }

    print(f"    [回调] 未识别的动作: {act!r} symbol={symbol!r}")
    return {"code": 0, "msg": "success", "data": {}}


class Handler(BaseHTTPRequestHandler):
    """极简 HTTP 服务，仅处理 POST /card 回调。"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length)
        raw_text = raw.decode("utf-8", errors="replace")

        # 事件订阅握手（url_verification）：直接回显 challenge
        try:
            payload = json.loads(raw_text)
        except Exception:
            payload = {}
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            self._respond({"challenge": challenge} if challenge is not None else {})
            return

        # 验签
        ts = self.headers.get("X-Lark-Request-Timestamp", "") or ""
        nonce = self.headers.get("X-Lark-Request-Nonce", "") or ""
        sig = self.headers.get("X-Lark-Signature", "") or ""
        if not _verify_signature(ts, nonce, sig, raw_text):
            print("    !! 回调签名校验失败，拒绝请求")
            self._respond({"code": 1, "msg": "invalid signature"}, status=401)
            return

        if payload.get("type") == "card.action.trigger":
            self._respond(handle_card_trigger(payload))
        else:
            self._respond({"code": 0, "msg": "success", "data": {}})

    def _respond(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[webhook] {time.strftime('%H:%M:%S')} " + (fmt % args))


def main():
    ap = argparse.ArgumentParser(description="飞书卡片回调服务（card.action.trigger）")
    ap.add_argument("--host", default=getattr(C, "CALLBACK_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=getattr(C, "CALLBACK_PORT", 9001))
    args = ap.parse_args()

    verify_state = "已启用" if (getattr(C, "LARK_ENCRYPT_KEY", "") or "") else "跳过(开发模式，请配置 LARK_ENCRYPT_KEY)"
    print(f"[webhook] 监听 {args.host}:{args.port} | 验签: {verify_state}")
    print(f"[webhook] 关注落库: {C.WATCH_CARD_FILE}")
    print(f"[webhook] 请将飞书开放平台「卡片回传交互」请求地址指向本服务的 /card 路径")
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
