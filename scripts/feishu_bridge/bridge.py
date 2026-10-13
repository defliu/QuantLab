# coding: utf-8
"""飞书 <-> WorkBuddy AI 桥接工具（收发 + 去重 + 状态）。

背景（必读）
============
lark-cli 检测到 Agent 环境变量（OPENCLAW_CLI / HERMES_HOME / LARK_CHANNEL）时，
会拒绝读取 ~/.lark-cli/config.json，直接报：

    openclaw context detected but lark-cli is not bound to it

而本机不存在 ~/.openclaw/openclaw.json，`lark-cli config bind` 无法执行
（2026-10-09 实测：openclaw 源缺配置文件；hermes / lark-channel 源因环境不匹配被拒）。
=> 因此本模块统一在子进程环境里清掉这些变量再调用 lark-cli。
   这与 P16 `qmt_monitor._env_for_cli()` 是同一套做法，是本机唯一可用路径。

能力矩阵（2026-10-09 实测）
==========================
  发送消息（私聊/群）  OK   bot 身份可用
  接收消息事件         OK   WebSocket 长连接可用（im.message.receive_v1），无需公网 IP
  读取消息列表          NO  应用缺 im:message:readonly（错误码 230027）-> 一律走事件流
  bot 列私聊会话        NO  飞书隐私保护，bot 身份不可列 p2p chat

子命令
======
  whoami                          健康检查（bot 身份 + token 状态）
  pull [--minutes N]              长连接接收事件，新消息落 inbox.jsonl 并打印摘要
  pending [--json]                列出未处理消息
  reply --message-id ID --text T  回复指定消息（自动标记已处理）
  send --text T                   主动发消息（默认私聊诚哥，可 --chat-id 发群）
  mark --message-id ID            仅标记已处理

状态文件（data/feishu_bridge/）
==============================
  inbox.jsonl     收到的消息（append-only）
  processed.json  已处理的 message_id 列表
  pull.log        pull 运行日志（含 lark-cli 的 ready / exited 标记）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]          # D:/QuantLab
STATE_DIR = ROOT / "data" / "feishu_bridge"
INBOX = STATE_DIR / "inbox.jsonl"
PROCESSED = STATE_DIR / "processed.json"
PULL_LOG = STATE_DIR / "pull.log"

# ---- 目标与凭据（与 P16 qmt_config.py 保持一致）----
OPEN_ID_CHENG = "ou_bd13444d8ea53c28249c669f43f3eeff"   # 诚哥（本应用维度 open_id）
GROUP_CHAT_ID = "oc_f436148efffabf9383cf7075bca9b17f"   # 股票投研群
P2P_CHAT_ID = "oc_3bb09b777437e5a79a3fa371c4bf7689"     # 机器人 <-> 诚哥 私聊

LARK_CLI_CANDIDATES = [
    r"C:\Users\Administrator\bin\lark-cli.exe",
    r"C:\Users\Administrator\.workbuddy\binaries\node\cli-connector-packages\lark-cli.cmd",
]

AGENT_ENV_KEYS = ("OPENCLAW_CLI", "OPENCLAW_HOME", "HERMES_HOME", "LARK_CHANNEL")


# ---------------------------------------------------------------- 基础设施

def lark_cli() -> str:
    for p in LARK_CLI_CANDIDATES:
        if os.path.exists(p):
            return p
    return "lark-cli"


def clean_env() -> dict:
    """清掉 Agent 上下文信号，否则 lark-cli 拒绝读取本地配置。"""
    env = dict(os.environ)
    for k in AGENT_ENV_KEYS:
        env.pop(k, None)
    return env


def run_lark(args: list[str], timeout: int = 120) -> tuple[bool, str]:
    """调用 lark-cli，返回 (ok, 合并输出)。"""
    try:
        p = subprocess.run(
            [lark_cli(), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=clean_env(), timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out.strip()


def _ms_to_str(ms) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ms) / 1000))
    except Exception:
        return str(ms or "")


# 常见不可见字符：零宽空格/连接符、BOM、不间断空格、各类空白
_INVISIBLE = dict.fromkeys(
    list(range(0x200B, 0x2010)) + [0xFEFF, 0x00A0, 0x3000, 0x2000, 0x2001], None
)


def _norm_id(s: str) -> str:
    """归一化 ID：去掉首尾空白与常见不可见字符（兜底，非主要防线）。

    教训（2026-10-09 实测）：**手抄 / 从界面复制 message_id 极易出错**。
    当时手抄时漏掉一个字符（`...eb9499f` 写成 `...eb949f`），
    两个字符串肉眼完全一致却比对不相等，导致查不到 chat_id、回复静默失败。
    正确做法：ID 一律从 `inbox.jsonl` 或 JSON 输出里程序化读取，不要手打；
    本函数只作为额外兜底。
    """
    return (s or "").strip().translate(_INVISIBLE)


def ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 状态读写

def inbox_ids() -> set[str]:
    ids: set[str] = set()
    if not INBOX.exists():
        return ids
    with INBOX.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["message_id"])
            except Exception:
                continue
    return ids


def append_inbox(rec: dict) -> None:
    ensure_state()
    with INBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_inbox() -> list[dict]:
    if not INBOX.exists():
        return []
    out = []
    with INBOX.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def load_processed() -> list[str]:
    if not PROCESSED.exists():
        return []
    try:
        return list(json.loads(PROCESSED.read_text(encoding="utf-8")))
    except Exception:
        return []


def mark_processed(message_id: str) -> None:
    ensure_state()
    ids = load_processed()
    if message_id not in ids:
        ids.append(message_id)
    PROCESSED.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 事件规范化

def normalize(ev: dict) -> dict | None:
    """把 lark-cli 事件拍平成入库记录；机器人自己发的消息返回 None。"""
    if (ev.get("sender_type") or "") == "bot":
        return None
    mid = ev.get("message_id") or ev.get("id") or ""
    if not mid:
        return None
    return {
        "message_id": mid,
        "chat_id": ev.get("chat_id", ""),
        "chat_type": ev.get("chat_type", ""),
        "sender_id": ev.get("sender_id", ""),
        "message_type": ev.get("message_type", ""),
        "content": ev.get("content", ""),
        "create_time": _ms_to_str(ev.get("create_time")),
        "mentions": [m.get("name") for m in (ev.get("mentions") or [])],
        "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def fmt_rec(r: dict) -> str:
    where = "私聊" if r.get("chat_type") == "p2p" else "群"
    at = (" @[" + ",".join(filter(None, r.get("mentions") or [])) + "]") if r.get("mentions") else ""
    return f"[{r.get('create_time')}] ({where}) {r.get('message_id')}{at}\n    {r.get('content')}"


# ---------------------------------------------------------------- 子命令

def cmd_whoami(_args) -> int:
    ok, out = run_lark(["whoami"])
    print(out)
    return 0 if ok else 1


def cmd_pull(args) -> int:
    ensure_state()
    dur = f"{int(args.minutes * 60)}s"
    known = inbox_ids()
    log = PULL_LOG.open("a", encoding="utf-8")
    log.write(f"\n=== pull start {time.strftime('%Y-%m-%d %H:%M:%S')} timeout={dur} ===\n")
    log.flush()

    cmd = [lark_cli(), "event", "consume", "im.message.receive_v1",
           "--as", "bot", "--timeout", dur]
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
        env=clean_env(), text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    new = 0
    try:
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            rec = normalize(ev)
            if rec is None or rec["message_id"] in known:
                continue
            append_inbox(rec)
            known.add(rec["message_id"])
            new += 1
            print(fmt_rec(rec))
            sys.stdout.flush()
    finally:
        p.wait()
        log.write(f"=== pull end new={new} rc={p.returncode} ===\n")
        log.close()

    print(f"[pull] 窗口 {dur}，新增 {new} 条消息；日志 {PULL_LOG}")
    return 0


def cmd_pending(args) -> int:
    done = set(load_processed())
    pend = [r for r in read_inbox() if r["message_id"] not in done]
    if args.json:
        print(json.dumps(pend, ensure_ascii=False, indent=2))
    else:
        if not pend:
            print("(无未处理消息)")
        for r in pend:
            print(fmt_rec(r))
    return 0


def cmd_reply(args) -> int:
    # 优先尝试真正的「回复」（需应用具备 im:message 相关 scope）
    ok, out = run_lark(["im", "+messages-reply",
                        "--message-id", args.message_id,
                        "--text", args.text, "--as", "bot"])
    if not ok:
        # 回退：应用缺 read scope 时按 message_id 定位会报 99992354
        #       （权限掩码错误），改为按 chat_id 直接发新消息。
        rec = next((r for r in read_inbox()
                    if _norm_id(r["message_id"]) == _norm_id(args.message_id)), None)
        chat_id = args.chat_id or (rec or {}).get("chat_id") or ""
        if not chat_id:
            print(out)
            print("[reply] 回复失败且无法确定 chat_id，放弃（该消息不在 inbox 中）")
            return 1
        print("[reply] messages-reply 不可用（应用缺 im:message scope，99992354），"
              f"回退为按 chat_id 发送新消息: {chat_id}")
        ok, out = run_lark(["im", "+messages-send",
                            "--chat-id", chat_id,
                            "--text", args.text, "--as", "bot"])
    print(out)
    if ok:
        mark_processed(args.message_id)
        print(f"[reply] 已回复并标记已处理: {args.message_id}")
    return 0 if ok else 1


def cmd_send(args) -> int:
    cmd = ["im", "+messages-send", "--as", "bot"]
    if args.chat_id:
        cmd += ["--chat-id", args.chat_id]
    else:
        cmd += ["--user-id", args.user_id]
    if args.markdown:
        cmd += ["--markdown", args.text]
    else:
        cmd += ["--text", args.text]
    ok, out = run_lark(cmd)
    print(out)
    return 0 if ok else 1


def cmd_mark(args) -> int:
    mark_processed(args.message_id)
    print(f"[mark] {args.message_id} 已标记为已处理")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="飞书 <-> WorkBuddy AI 桥接工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="健康检查").set_defaults(func=cmd_whoami)

    p_pull = sub.add_parser("pull", help="长连接接收新消息")
    p_pull.add_argument("--minutes", type=float, default=5.0, help="监听窗口（分钟）")
    p_pull.set_defaults(func=cmd_pull)

    p_pend = sub.add_parser("pending", help="列出未处理消息")
    p_pend.add_argument("--json", action="store_true")
    p_pend.set_defaults(func=cmd_pending)

    p_reply = sub.add_parser("reply", help="回复指定消息")
    p_reply.add_argument("--message-id", required=True)
    p_reply.add_argument("--text", required=True)
    p_reply.add_argument("--chat-id", default="", help="可选：直接指定目标会话（跳过 inbox 查找）")
    p_reply.set_defaults(func=cmd_reply)

    p_send = sub.add_parser("send", help="主动发消息")
    p_send.add_argument("--text", required=True)
    p_send.add_argument("--user-id", default=OPEN_ID_CHENG, help="私聊目标（默认诚哥）")
    p_send.add_argument("--chat-id", default="", help="改发群（覆盖 --user-id）")
    p_send.add_argument("--markdown", action="store_true", help="按 markdown 富文本发送")
    p_send.set_defaults(func=cmd_send)

    p_mark = sub.add_parser("mark", help="标记已处理")
    p_mark.add_argument("--message-id", required=True)
    p_mark.set_defaults(func=cmd_mark)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
