# coding: utf-8
"""管道健康告警卡片：候选缺失 / 桥心跳停 / 任务失败 / 对账异常 时主动推送。

用法：
  python push_alert_card.py --title "G2 候选缺失" --body "20260902_g2_top2.csv 不存在，已中止换仓" --level error
  python push_alert_card.py --title "桥心跳异常" --body "last_heartbeat 3 分钟未刷新" --level warn --no-send
"""
import argparse
import json
import os
import sys
import time

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import qmt_card as QC

LEVEL = {
    "error": {"template": "red", "color": "red", "tag": "告警", "icon": "error_filled"},
    "warn": {"template": "orange", "color": "orange", "tag": "提醒", "icon": "warning"},
    "info": {"template": "blue", "color": "blue", "tag": "通知", "icon": "notification_colorful"},
}


def build_alert_card(title, body, level="error"):
    meta = LEVEL.get(level, LEVEL["error"])
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title or "管道告警"},
            "subtitle": {"tag": "plain_text", "content": "%s · Project_16" % ts},
            "template": meta["template"],
            "icon": {"tag": "standard_icon", "token": meta["icon"]},
            "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": meta["tag"]}, "color": meta["color"]}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": [
                {"tag": "interactive_container", "width": "fill", "has_border": True,
                 "border_color": "%s-100" % meta["color"], "background_style": "%s-50" % meta["color"],
                 "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
                 "elements": [{"tag": "markdown", "content": "<font color='%s'>%s</font>" % (meta["color"], body or "—")}]},
                {"tag": "markdown", "content": "<font color='grey'>%s · 需关注处理</font>" % ts, "text_size": "notation"},
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser(description="管道健康告警卡片推送")
    ap.add_argument("--title", default="管道告警")
    ap.add_argument("--body", default="")
    ap.add_argument("--level", choices=["error", "warn", "info"], default="error")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    card = build_alert_card(args.title, args.body, args.level)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → alert(%s)" % ("成功" if ok else "失败", args.level))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
