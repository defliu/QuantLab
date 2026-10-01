# coding: utf-8
"""飞书推送统一出口（Project_16 全部自动化任务共用）。

为什么要有这个模块（2026-09-28 审计结论）
--------------------------------------------
仓库里原本有 4 份各自实现的推送代码：qmt_bridge_client / qmt_bridge_client_ens /
qmt_monitor / qmt_card，各写一份 subprocess.run，导致同一个"消息发不全"问题
要在 4 个地方分别修、且修完还会漂移。本模块把发送语义收敛到唯一实现：

1. CLI 路径自动探测：调度提示词里曾硬编码 lark/1.0.4/bin/lark-cli.exe，
   而插件实际版本是 1.0.5 —— 路径不存在时模型只能"记录失败"，
   用户收到的是"任务跑了但没消息"。这里改为按候选列表探测，取第一个存在的。
2. 长文本分片：Windows 命令行有 32767 字符上限，整段报告塞进 --text 会被
   静默截断。切分必须可逆（"".join(chunks) == text），否则丢字。
3. 严格成功判定：只看 returncode==0 会把业务失败当成功（旧 qmt_monitor 就是
   这么写的）。统一要求 returncode==0 且 stdout 含 "ok": true。
4. 重试与失败可见：单片失败重试；任一片最终失败整体返回 False，并写
   data/schedules/feishu_push_fail_<date>.json，供事后审计。

安全性：本模块只做"发消息"，不下单、不改仓、不改数据文件。
"""
import glob
import json
import os
import subprocess
import sys
import time

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================
# CLI 路径探测
# ============================================================
_LARK_CLI_CANDIDATES = [
    r"C:\Users\Administrator\bin\lark-cli.exe",
    r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe",
    r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\1.0.4\bin\lark-cli.exe",
]
_LARK_GLOB = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark\*\bin\lark-cli.exe"

_cli_cache = {}


def resolve_cli():
    """返回可用的 lark-cli 路径；找不到返回 ""。

    顺序：显式环境变量 LARK_CLI → 固定候选 → 通配 glob（覆盖未来版本升级）。
    缓存结果避免每条消息都做文件系统探测。
    """
    if "path" in _cli_cache:
        return _cli_cache["path"]
    path = ""
    env_cli = (os.environ.get("LARK_CLI") or "").strip()
    if env_cli and os.path.exists(env_cli):
        path = env_cli
    if not path:
        for c in _LARK_CLI_CANDIDATES:
            if os.path.exists(c):
                path = c
                break
    if not path:
        hits = sorted(glob.glob(_LARK_GLOB), reverse=True)
        if hits:
            path = hits[0]
    _cli_cache["path"] = path
    return path


def describe_cli():
    """自检用：返回 (路径, 是否存在, 候选来源说明)。"""
    env_cli = (os.environ.get("LARK_CLI") or "").strip()
    return resolve_cli(), bool(resolve_cli()), ("env:LARK_CLI" if env_cli else "probe")


# ============================================================
# 环境构造
# ============================================================
def build_env(as_ident="bot"):
    """构造 lark-cli 子进程环境。

    as_ident="user"：保留宿主注入的凭据。
    as_ident="bot" ：清理外部凭据与 Agent 上下文信号 + 关闭 strict-mode，
                     让 lark-cli 回退到 ~/.lark-cli/config.json 的 bot 凭据。
    按前缀整体清扫 OPENCLAW*/HERMES* —— 原逐项清单漏过 OPENCLAW_CLI，
    该变量在 Windows 计划任务 S4U 会话下为 "1"，曾导致全部推送
    返回 config/not_configured（T-20260923-004）。
    """
    env = dict(os.environ)
    if as_ident == "bot":
        env.pop("LARKSUITE_CLI_APP_ID", None)
        env.pop("LARKSUITE_CLI_USER_ACCESS_TOKEN", None)
        env["LARKSUITE_CLI_STRICT_MODE"] = "off"
        for _k in list(env.keys()):
            _u = _k.upper()
            if _u.startswith("OPENCLAW") or _u.startswith("HERMES") or _u == "LARK_CHANNEL":
                env.pop(_k, None)
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    return env


def apply_src_tag(text):
    """来源标记（LARK_SRC_TAG 未设置时为 no-op，不改动文本）。"""
    tag = (os.environ.get("LARK_SRC_TAG") or "").strip()
    if tag and not text.startswith(tag):
        return tag + " " + text
    return text


# ============================================================
# 分片
# ============================================================
def chunk_text(text, limit):
    """按行切分长文本，每片不超过 limit 字符。

    硬约束: "".join(chunk_text(text, limit)) == text
    推送文本必须与抽取器 stdout 逐字一致，多一个或少一个字符都会让"消息完整性"
    无法事后校验，所以切分必须可逆。
    实现要点:
    - 用 find 切出"行内容 + 紧跟其后的换行符"，换行归属于本行，拼接可完整还原;
    - 末行若本来没有换行不会被凭空补上（早期 split("\\n") + "\\n" 会多出尾换行）;
    - 单行超长（无换行的长串）按字符硬切，避免死循环;
    - 空白分片也不丢弃，否则同样破坏可逆性。
    """
    if not text or not text.strip():
        return [text]
    if limit <= 0:
        return [text]
    pieces, i, n = [], 0, len(text)
    while i < n:
        j = text.find("\n", i)
        if j < 0:
            pieces.append(text[i:])
            break
        pieces.append(text[i:j + 1])
        i = j + 1
    chunks, cur, size = [], [], 0
    for p in pieces:
        if len(p) > limit:
            if cur:
                chunks.append("".join(cur))
                cur, size = [], 0
            while len(p) > limit:
                chunks.append(p[:limit])
                p = p[limit:]
            if p:
                cur, size = [p], len(p)
            continue
        if cur and size + len(p) > limit:
            chunks.append("".join(cur))
            cur, size = [p], len(p)
        else:
            cur.append(p)
            size += len(p)
    if cur:
        chunks.append("".join(cur))
    return chunks


def resolve_chunk_limit(chunk_limit=None, default=3500):
    """分片阈值：显式参数 > env LARK_CHUNK_LIMIT > default；传 0 关闭分片。"""
    if chunk_limit is None:
        try:
            chunk_limit = int(os.environ.get("LARK_CHUNK_LIMIT") or default)
        except ValueError:
            chunk_limit = default
    return chunk_limit


# ============================================================
# 失败兜底（失败必须可被发现）
# ============================================================
def _proj_dir():
    return os.path.dirname(os.path.abspath(__file__))


def record_failure(kind, title, reason, extra=None):
    """推送最终失败时写本地文件，确保"任务跑了但用户没收到消息"可事后审计。"""
    try:
        d = os.path.join(_proj_dir(), "data", "schedules")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "feishu_push_fail_%s.json" % time.strftime("%Y%m%d"))
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
        if not isinstance(data, list):
            data = []
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
               "title": title, "reason": reason}
        if extra:
            rec["extra"] = extra
        data.append(rec)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("[推送] 失败已记入本地兜底: %s" % path)
    except Exception as e:
        print("[推送] !! 兜底文件写入失败: %r" % (e,))


# ============================================================
# 文本发送
# ============================================================
def _resolve_target(target=None, uid=None, chat_id=None):
    """解析推送目标并校验"实际值"非空，返回 (target, err)。

    为什么不只查参数名：
    ["--chat-id", ""] 里含 "--chat-id" 这个参数名，若只查名字就会通过，
    空 ID 被送进 lark-cli 才报错，真实原因（未配置目标）被 CLI 报文掩盖。
    调用方（qmt_monitor / qmt_card）恰好会构造出这种空值 target。
    """
    if target:
        # 调用方显式传了 target：仍要验证其值，否则空 ID 会被漏放行
        vals = {}
        tl = list(target)
        for i, t in enumerate(tl):
            if t in ("--chat-id", "--user-id") and i + 1 < len(tl):
                vals[t] = (tl[i + 1] or "").strip()
        for flag in ("--chat-id", "--user-id"):
            if vals.get(flag):
                return [flag, vals[flag]], ""
        return [], "target 中 chat_id/user_id 均为空"

    uid = uid or os.environ.get("FEISHU_OPEN_ID", "")
    chat_id = chat_id if chat_id is not None else os.environ.get("LARK_PUSH_CHAT_ID", "")
    if chat_id:
        return ["--chat-id", chat_id], ""
    if uid:
        return ["--user-id", uid], ""
    return [], "no target (neither chat_id nor user_id)"


def send_text(text, target=None, as_ident="bot", uid=None, chat_id=None,
              chunk_limit=None, retries=1, title="文本消息"):
    """发送文本消息，返回 True/False（必须严格判定，调用方据此决定任务成败）。

    成功判定：returncode == 0 且 stdout 含 '"ok": true'。
    仅看退出码不够 —— lark-cli 在业务失败时可能仍以 0 退出。
    """
    cli = resolve_cli()
    if not cli:
        print("[推送] !! 未找到 lark-cli，跳过（不是静默丢弃：调用方会收到 False）")
        record_failure("text", title, "lark-cli not found")
        return False
    target, terr = _resolve_target(target, uid, chat_id)
    if terr:
        record_failure("text", title, terr)
        print("[推送] !! 未配置推送目标，跳过：%s" % terr)
        return False

    text = apply_src_tag(text)
    limit = resolve_chunk_limit(chunk_limit)
    # 片头【第 i/n 部分】与正文共用预算，否则实际发给 CLI 的文本会超出 limit
    head_budget = 24 if limit > 120 else 0
    body_limit = limit - head_budget
    parts = chunk_text(text, body_limit) if limit and len(text) > limit else [text]
    env = build_env(as_ident)

    def _once(t):
        return subprocess.run(
            [cli, "im", "+messages-send"] + list(target) +
            ["--msg-type", "text", "--text", t, "--as", as_ident],
            capture_output=True, text=True, encoding="utf-8", timeout=15, env=env)

    def _send(t):
        detail = ""
        for k in range(max(1, retries + 1)):
            try:
                r = _once(t)
                detail = (r.stdout or r.stderr).strip()[:160]
                if r.returncode == 0 and '"ok": true' in r.stdout:
                    return True, detail
                if r.returncode != 0:
                    detail = "rc=%d %s" % (r.returncode, detail)
            except Exception as e:
                detail = "异常: %s" % e
            if k < max(0, retries):
                time.sleep(1.5)
        return False, detail

    if len(parts) == 1:
        ok, detail = _send(parts[0])
        print("[推送] 文本%s(%d字): %s" % ("成功" if ok else "失败", len(text), detail))
        if not ok:
            record_failure("text", title, detail, {"chars": len(text)})
        return ok

    total = len(parts)
    results = []
    for i, part in enumerate(parts, 1):
        ok, detail = _send("【第 %d/%d 部分】\n%s" % (i, total, part))
        results.append(ok)
        print("[推送] 第%d/%d片%s: %s" % (i, total, "成功" if ok else "失败", detail))
    all_ok = all(results)
    if not all_ok:
        bad = ",".join(str(i + 1) for i, x in enumerate(results) if not x)
        print("[推送] !! 分片投递不完整，失败片: %s（共%d片），消息可能残缺" % (bad, total))
        record_failure("text", title, "partial chunk failure",
                       {"failed_chunks": bad, "total_chunks": total, "chars": len(text)})
    return all_ok


# ============================================================
# 卡片发送
# ============================================================
CARD_CONTENT_LIMIT = 28000  # 飞书 interactive content 实际上限，留余量


def send_card(card, target=None, as_ident="bot", uid=None, chat_id=None,
              retries=1, title="卡片消息"):
    """发送 Card 2.0 交互卡片，返回 True/False。

    与文本出口的关键差异：卡片是一个不可分割的 JSON 载荷，**不能分片**
    （切成两半的 JSON 飞书会直接拒收）。所以这里的"完整性"保护是：
    超限时降级为纯文本分片发送，而不是截断 payload 或静默丢弃。
    """
    cli = resolve_cli()
    if not cli:
        print("[推送] !! 未找到 lark-cli，跳过卡片")
        record_failure("card", title, "lark-cli not found")
        return False
    target, terr = _resolve_target(target, uid, chat_id)
    if terr:
        record_failure("card", title, terr)
        print("[推送] !! 未配置推送目标，跳过卡片：%s" % terr)
        return False

    payload = json.dumps(card, ensure_ascii=False)
    if len(payload) > CARD_CONTENT_LIMIT:
        # 降级：把卡片里的 markdown 文本抽出来走文本分片通道，保证内容不丢
        flat = _flatten_card_text(card)
        print("[推送] !! 卡片 payload %d 字符超上限，降级为文本分片（内容不截断）"
              % len(payload))
        return send_text(flat, target=target, as_ident=as_ident, retries=retries,
                         title=title + "(降级文本)")

    env = build_env(as_ident)
    detail = ""
    for k in range(max(1, retries + 1)):
        try:
            r = subprocess.run(
                [cli, "im", "+messages-send"] + list(target) +
                ["--msg-type", "interactive", "--content", payload, "--as", as_ident],
                capture_output=True, text=True, encoding="utf-8", timeout=20, env=env)
            detail = (r.stdout or r.stderr).strip()[:160]
            if r.returncode == 0 and '"ok": true' in r.stdout:
                print("[推送] 卡片成功(%d字符): %s" % (len(payload), detail))
                return True
            if r.returncode != 0:
                detail = "rc=%d %s" % (r.returncode, detail)
        except Exception as e:
            detail = "异常: %s" % e
        if k < max(0, retries):
            time.sleep(1.5)
    print("[推送] !! 卡片失败: %s" % detail)
    record_failure("card", title, detail, {"payload_chars": len(payload)})
    return False


def _flatten_card_text(card):
    """把卡片里的 markdown / plain_text 文本按顺序抽出，拼成可分片的纯文本。"""
    out = []

    def _walk(node):
        if isinstance(node, dict):
            tag = node.get("tag")
            content = node.get("content")
            if tag in ("markdown", "plain_text", "lark_md") and isinstance(content, str):
                if content.strip():
                    out.append(content)
                return
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
        elif isinstance(node, str):
            if node.strip():
                out.append(node)

    _walk(card)
    return "\n".join(out)


# ============================================================
# CLI（供 PowerShell 包装脚本与调度提示词直接调用）
# ============================================================
def main():
    import argparse

    ap = argparse.ArgumentParser(description="Project_16 飞书统一推送出口")
    ap.add_argument("--text", default=None, help="直接给出文本")
    ap.add_argument("--file", default=None, help="从 UTF-8 文件读文本（长消息首选，避免命令行长度限制）")
    ap.add_argument("--card-file", default=None, help="从 UTF-8 JSON 文件读卡片并发送")
    ap.add_argument("--title", default="自动化推送", help="失败兜底记录的标题")
    ap.add_argument("--as", dest="as_ident", default="bot", choices=["bot", "user"])
    ap.add_argument("--chat-id", default=None, help="群发目标；缺省用 env LARK_PUSH_CHAT_ID")
    ap.add_argument("--user-id", default=None, help="私聊目标；缺省用 env FEISHU_OPEN_ID")
    ap.add_argument("--chunk-limit", type=int, default=None, help="分片阈值，0 关闭分片")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--check", action="store_true", help="只自检 CLI 路径，不发送")
    args = ap.parse_args()

    if args.check:
        path, ok, src = describe_cli()
        print("[自检] lark-cli: %s (来源=%s) 存在=%s" % (path or "(未找到)", src, ok))
        return 0 if ok else 4

    if not (args.text or args.file or args.card_file):
        print("[推送] !! 未指定 --text / --file / --card-file")
        return 3

    target = []
    chat_id = args.chat_id if args.chat_id is not None else (os.environ.get("LARK_PUSH_CHAT_ID") or "")
    uid = args.user_id if args.user_id is not None else (os.environ.get("FEISHU_OPEN_ID") or "")
    if chat_id:
        target = ["--chat-id", chat_id]
    elif uid:
        target = ["--user-id", uid]

    try:
        if args.card_file:
            with open(args.card_file, encoding="utf-8") as f:
                card = json.load(f)
            ok = send_card(card, target=target, as_ident=args.as_ident,
                           retries=args.retries, title=args.title)
        else:
            if args.file:
                with open(args.file, encoding="utf-8") as f:
                    body = f.read()
            else:
                body = args.text
            if not body.strip():
                print("[推送] !! 正文为空，拒绝发送空消息")
                record_failure("text", args.title, "empty body")
                return 3
            ok = send_text(body, target=target, as_ident=args.as_ident,
                           chunk_limit=args.chunk_limit, retries=args.retries,
                           title=args.title)
    except Exception as e:
        print("[推送] !! 异常: %r" % (e,))
        record_failure("text", args.title, "exception: %r" % (e,))
        return 3
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
