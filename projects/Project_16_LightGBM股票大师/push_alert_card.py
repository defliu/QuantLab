# coding: utf-8
"""管道健康告警卡片：候选缺失 / 桥心跳停 / 任务失败 / 对账异常 时主动推送。

用法：
  python push_alert_card.py --title "G2 候选缺失" --body "20260902_g2_top10.csv 不存在，已中止换仓" --level error
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

# 2026-09-28：调度提示词里长期写 --level warning（自然语言习惯），但 choices 只认 warn，
# argparse 在入口直接 exit 2，卡片根本没生成 → 告警彻底丢失。
# 这里把常见别名归一化到三个规范值，避免同类拼写问题再次静默丢消息。
LEVEL_ALIAS = {
    "error": "error",
    "err": "error",
    "warn": "warn",
    "warning": "warn",
    "info": "info",
    "notice": "info",
}


def normalize_level(level):
    """把各种 level 写法归一化；无法识别时返回 None 交由调用方显式报错。"""
    return LEVEL_ALIAS.get((level or "").strip().lower())


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


def _read_body(args):
    """取告警正文，返回 (body, err)。err 非空表示必须拒绝发送。

    --body-file 优先于 --body：两者同时给时以文件为准，避免模型同时传了
    一份截断的命令行正文和一份完整文件正文时，短的那份反而生效。
    """
    if args.body_file:
        if not os.path.exists(args.body_file):
            return "", "正文文件不存在: %s" % args.body_file
        try:
            with open(args.body_file, encoding="utf-8") as f:
                txt = f.read()
        except Exception as e:
            return "", "正文文件读取失败: %r" % (e,)
        if not txt.strip():
            return "", "正文文件为空: %s" % args.body_file
        return txt, ""
    if not (args.body or "").strip():
        # 空正文发出去是一条"成功但零信息"的消息，比不发更糟：
        # 它会在推送记录里留下 ok:true，事后审计无法区分"真发过"和"发了空的"
        return "", "告警正文为空（请用 --body 或 --body-file 提供正文）"
    return args.body, ""


def main():
    ap = argparse.ArgumentParser(description="管道健康告警卡片推送")
    ap.add_argument("--title", default="管道告警")
    ap.add_argument("--body", default="",
                    help="告警正文。短文本可直接用；长正文必须改用 --body-file "
                         "(Windows 命令行有长度上限且正文里的引号/换行会被 shell 吃掉)")
    ap.add_argument("--body-file", default=None,
                    help="从 UTF-8 文件读告警正文（长文本唯一安全通道）")
    # 不在 argparse 层用 choices 校验：choices 是精确字符串匹配，
    # 会抢在 normalize_level 之前拒掉 --level WARNING 这类大小写变体。
    ap.add_argument(
        "--level",
        default="error",
        help="告警级别：error/warn/info，兼容 warning/err/notice 等写法（大小写不敏感）",
    )
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    level = normalize_level(args.level)
    if not level:
        ap.error("--level 取值非法: %r（可用: %s）"
                 % (args.level, ", ".join(sorted(LEVEL_ALIAS.keys()))))
    body, berr = _read_body(args)
    if berr:
        print("[FAIL] %s" % berr)
        sys.exit(3)
    card = build_alert_card(args.title, body, level)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → alert(%s)" % ("成功" if ok else "失败", level))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
