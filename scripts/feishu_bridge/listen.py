# coding: utf-8
"""飞书消息常驻监听：连续消费 im.message.receive_v1，新消息追加到 inbox.jsonl。

为什么要常驻
============
WorkBuddy 定时任务的最小调度粒度是 1 小时，若只靠定时任务里的 `bridge.py pull`，
两次运行之间到达的消息会永久丢失。因此用本常驻进程保证「捕获零丢失」，
定时任务只负责「处理 + 回复」。

运行方式
========
  python scripts/feishu_bridge/listen.py
由计划任务 `QuantLab_FeishuListen_*` 在登录时拉起，并每 5 分钟自愈（见 README）。

设计要点
========
  - 循环调用 `lark-cli event consume ... --timeout <WINDOW>`：
    有界运行会忽略 stdin EOF，退出后本进程立即重启，实现准连续监听。
  - 去重：按 message_id 对比 inbox.jsonl 已有记录，绝不重复入库。
  - 失败退避：spawn 失败等 30s，正常退出等 5s（等 bus daemon 静默退出）。
  - 日志：data/feishu_bridge/listen.log（append-only）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge as B  # noqa: E402

WINDOW = "1500s"        # 单次监听窗口（25 分钟）
RESTART_WAIT = 5        # 正常退出后的重启等待
FAIL_WAIT = 30          # spawn 失败后的退避
EVENT_KEY = "im.message.receive_v1"


def main() -> int:
    B.ensure_state()
    log = (B.STATE_DIR / "listen.log").open("a", encoding="utf-8")

    def w(msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    w(f"listener start (window={WINDOW}, cli={B.lark_cli()})")
    known = B.inbox_ids()
    w(f"inbox 已有 {len(known)} 条历史记录")

    while True:
        cmd = [B.lark_cli(), "event", "consume", EVENT_KEY,
               "--as", "bot", "--timeout", WINDOW]
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                env=B.clean_env(), text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except Exception as e:
            w(f"!! 启动 consume 失败: {e!r}；{FAIL_WAIT}s 后重试")
            time.sleep(FAIL_WAIT)
            continue

        try:
            for line in p.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                rec = B.normalize(ev)
                if rec is None or rec["message_id"] in known:
                    continue
                B.append_inbox(rec)
                known.add(rec["message_id"])
                w(f"inbox+ {rec['message_id']} [{rec.get('chat_type')}] "
                  f"{(rec.get('content') or '')[:80]}")
        except Exception as e:
            w(f"!! 读取循环异常: {e!r}")
        finally:
            try:
                p.wait(timeout=30)
            except Exception:
                p.kill()

        w(f"consume 退出 rc={p.returncode}；{RESTART_WAIT}s 后重启")
        time.sleep(RESTART_WAIT)


if __name__ == "__main__":
    raise SystemExit(main())
