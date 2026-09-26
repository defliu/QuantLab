# coding: utf-8
"""盯盘信号 → 飞书 Card 2.0 交互卡片：构建与发送。

设计依据：Trae 官方 Lark 插件 lark-im 卡片规范（P0-P7 好看标准）。
  - 版本 Card 2.0（schema="2.0"），header 三件套 + 指标卡 + 字段对 + 主次按钮
  - 主按钮「查看行情」= open_url 跳转东方财富；次按钮「加入关注」= callback 落库
  - 发送失败仅记录、不重试、不抛异常（与 qmt_monitor 容错约定一致）

用法：
    import qmt_card as QC
    QC.send_lark_card(QC.build_signal_card(sig))        # 单信号卡
    QC.send_lark_card(QC.build_heartbeat_card("..."))   # 心跳卡（备用）
"""
import json
import os
import subprocess
import sys
import time

# Windows 控制台编码兜底（同 qmt_monitor，避免 GBK 输出 emoji 报错）
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import qmt_config as C

# 信号类型 → header 模板 / 标签 / 主色（P6 邻近色环：red→orange 一支，green/grey 各自单色）
ACTION_META = {
    "SELL_STOP":        {"template": "red",    "tag": "止损",     "color": "red",    "title": "QMT 盯盘 · 止损信号触发"},
    "SELL_TAKE_PROFIT": {"template": "orange", "tag": "止盈",     "color": "orange", "title": "QMT 盯盘 · 止盈信号触发"},
    "SELL_TRAILING":    {"template": "orange", "tag": "移动止盈", "color": "orange", "title": "QMT 盯盘 · 移动止盈触发"},
    "HEARTBEAT":        {"template": "green",  "tag": "运行中",   "color": "green",  "title": "QMT 盯盘 · 运行中"},
    "SYSTEM":           {"template": "grey",   "tag": "通知",     "color": "grey",   "title": "QMT 盯盘 · 系统通知"},
}


def code_to_eastmoney_url(code):
    """A 股代码(603969.SH/300919.SZ) → 东方财富行情页。"""
    code6 = (code or "").split(".")[0]
    market = "sh" if (code or "").endswith(".SH") else "sz"
    return f"https://quote.eastmoney.com/{market}{code6}.html"


def _env_for_cli(as_ident):
    """构造 lark-cli 子进程环境。

    as_ident="user"：保留宿主注入的 LARKSUITE_CLI_APP_ID / USER_ACCESS_TOKEN（本机唯一可用凭据）。
    as_ident="bot" ：清理外部注入 + Agent 上下文信号 + 关闭 strict-mode，
                     让 lark-cli 回退到 ~/.lark-cli/config.json 的 bot 凭据（secret 存系统 keychain）。
                     服务器部署（无 HERMES_HOME 等变量）同样适用本逻辑。
    """
    env = dict(os.environ)
    if as_ident == "bot":
        env.pop("LARKSUITE_CLI_APP_ID", None)
        env.pop("LARKSUITE_CLI_USER_ACCESS_TOKEN", None)
        env["LARKSUITE_CLI_STRICT_MODE"] = "off"
        # Agent 上下文信号：存在时 lark-cli 只认 hermes/openclaw 绑定、忽略本地 config.json；
        # 清掉后回退到本地 bot 凭据（本机必需；服务器无此变量不受影响）。
        # 2026-09-23 修（T-20260923-004）：原清单漏了 OPENCLAW_CLI —— 该变量在
        # 交互会话为空、在 Windows 计划任务 S4U 会话为 "1"，导致 Windows 侧全部推送
        # 返回 config/not_configured（"openclaw context detected but lark-cli is not bound to it"）。
        # 故改为按前缀整体清扫，避免再有遗漏。
        for _k in list(env.keys()):
            _u = _k.upper()
            if _u.startswith("OPENCLAW") or _u.startswith("HERMES") or _u == "LARK_CHANNEL":
                env.pop(_k, None)
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    return env


def src_tag():
    """推送来源标记（2026-09-23，T-20260923-003）。

    WorkBuddy 侧自动化在子进程环境设 LARK_SRC_TAG=[WB] 后，其推送标题/文本会带标记前缀，
    用于区分「同一 bot、同一私聊」下 WorkBuddy 与 TRAE/Windows 侧重复推送的来源。
    未设置该环境变量时返回空串 → 标题与改造前逐字节一致，不影响既有任务。
    """
    t = (os.environ.get("LARK_SRC_TAG") or "").strip()
    return (t + " ") if t else ""


def apply_src_tag(card):
    """在卡片 header.title 上打来源标记（幂等：已带标记不重复加）。原地修改并返回 card。"""
    tag = src_tag()
    if not tag or not isinstance(card, dict):
        return card
    try:
        title = (card.get("header") or {}).get("title") or {}
        content = title.get("content") or ""
        if content and tag.strip() not in content:
            title["content"] = tag + content
    except Exception:
        pass
    return card


def build_signal_card(sig, name=None):
    """构建单信号 Card 2.0 卡片。sig 字段：code/action/note/last_price/cost/time/[auto_sold]/[order_id]"""
    meta = ACTION_META.get(sig.get("action"), ACTION_META["SYSTEM"])
    code = sig.get("code", "")
    last = float(sig.get("last_price", 0) or 0)
    cost = float(sig.get("cost", 0) or 0)
    pnl = (last - cost) / cost * 100 if cost else 0.0
    ts = sig.get("time", time.strftime("%Y-%m-%d %H:%M:%S"))
    note = sig.get("note", "")

    if sig.get("auto_sold") is True:
        exec_txt = "已自动卖出"
        if sig.get("order_id") and sig["order_id"] > 0:
            exec_txt += f" (order={sig['order_id']})"
    elif sig.get("auto_sold") is False:
        exec_txt = "自动卖出失败"
    else:
        exec_txt = "仅预警"

    # 现价/成本/浮动盈亏 三指标卡（数值统一用主题色，描述 grey → 单色系 + grey，过 P6）
    # 注意：column 组件不支持 corner_radius 属性（飞书 API 校验会拒绝），圆角交给背景块容器
    pnl_sym = "+" if pnl >= 0 else ""
    kpi_columns = [
        {"tag": "column", "width": "weighted", "weight": 1,
         "background_style": "grey-50", "padding": "12px", "vertical_spacing": "2px",
         "elements": [
             {"tag": "markdown", "content": f"## <font color='{meta['color']}'>{last:.2f}</font>", "text_align": "center"},
             {"tag": "markdown", "content": "<font color='grey'>现价(元)</font>", "text_align": "center", "text_size": "notation"}]},
        {"tag": "column", "width": "weighted", "weight": 1,
         "background_style": "grey-50", "padding": "12px", "vertical_spacing": "2px",
         "elements": [
             {"tag": "markdown", "content": f"## <font color='{meta['color']}'>{cost:.2f}</font>", "text_align": "center"},
             {"tag": "markdown", "content": "<font color='grey'>成本(元)</font>", "text_align": "center", "text_size": "notation"}]},
        {"tag": "column", "width": "weighted", "weight": 1,
         "background_style": "grey-50", "padding": "12px", "vertical_spacing": "2px",
         "elements": [
             {"tag": "markdown", "content": f"## <font color='{meta['color']}'>{pnl_sym}{pnl:.1f}%</font>", "text_align": "center"},
             {"tag": "markdown", "content": "<font color='grey'>浮动盈亏</font>", "text_align": "center", "text_size": "notation"}]},
    ]

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": meta["title"]},
            "subtitle": {"tag": "plain_text", "content": f"{ts} · 实时盯盘"},
            "template": meta["template"],
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": meta["tag"]}, "color": meta["color"]}],
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "8px",
            "elements": [
                {"tag": "interactive_container", "width": "fill", "has_border": True,
                 "border_color": f"{meta['color']}-100", "background_style": f"{meta['color']}-50",
                 "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
                 "margin": "0px 0px 12px 0px",
                 "elements": [
                     {"tag": "markdown",
                      "content": f"**<font color='{meta['color']}'>{code}</font>**" + (f"　{name}" if name else "")},
                     {"tag": "markdown", "content": note}]},
                {"tag": "column_set", "flex_mode": "trisect", "horizontal_spacing": "12px",
                 "margin": "0px 0px 12px 0px", "columns": kpi_columns},
                {"tag": "div", "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**执行**\n{exec_txt}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**触发时间**\n{ts}"}}]},
                {"tag": "markdown", "content": "<font color='grey'>点击下方按钮查看行情，或一键加入盯盘关注</font>",
                 "text_size": "notation", "text_align": "center"},
                {"tag": "button", "text": {"tag": "plain_text", "content": "查看行情"},
                 "type": "primary_filled", "width": "fill",
                 "behaviors": [{"type": "open_url", "default_url": code_to_eastmoney_url(code)}]},
                {"tag": "button", "text": {"tag": "plain_text", "content": "加入关注"},
                 "type": "default", "width": "fill",
                 "behaviors": [{"type": "callback", "value": {"action": "add_watch", "symbol": code}}]},
            ],
        },
    }


def build_heartbeat_card(status_text="持仓正常，无触发信号"):
    """构建心跳卡（备用；主流程无信号时仍走纯文本心跳，见 qmt_monitor）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": ACTION_META["HEARTBEAT"]["title"]},
            "subtitle": {"tag": "plain_text", "content": f"{ts} · 自动巡检"},
            "template": "green",
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": "运行中"}, "color": "green"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": [
                {"tag": "interactive_container", "width": "fill", "has_border": True,
                 "border_color": "green-100", "background_style": "green-50",
                 "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
                 "elements": [{"tag": "markdown", "content": f"**<font color='green'>盯盘运行中</font>**"},
                              {"tag": "markdown", "content": status_text}]},
                {"tag": "markdown", "content": f"<font color='grey'>检查时间：{ts}</font>",
                 "text_size": "notation", "text_align": "center"},
            ],
        },
    }


_NO_CHAT = object()  # 哨兵：未传 chat_id（默认回退 LARK_PUSH_CHAT_ID）；None 表示显式禁用群、强制私聊


def send_lark_card(card, uid=None, chat_id=_NO_CHAT, as_ident=None):
    """通过 lark-cli 推送交互卡片。返回是否成功（失败仅记录，不重试）。

    as_ident 默认取 qmt_config.LARK_PUSH_AS（本机 "bot" / 调试 "user"），也可显式传入。
    uid 默认取 qmt_config.FEISHU_OPEN_ID。
    chat_id 未传（默认）→ 取 LARK_PUSH_CHAT_ID 群发；传具体值 → 该群；传 None → 强制私聊（--user-id）。
    """
    cli = getattr(C, "LARK_CLI", "") or ""
    uid = uid or (getattr(C, "FEISHU_OPEN_ID", "") or "")
    if chat_id is _NO_CHAT:
        chat_id = getattr(C, "LARK_PUSH_CHAT_ID", "") or ""
    as_ident = as_ident or (getattr(C, "LARK_PUSH_AS", "bot") or "bot")
    if not cli or (not uid and not chat_id):
        print("    (未配置 LARK_CLI / FEISHU_OPEN_ID / LARK_PUSH_CHAT_ID，跳过卡片推送)")
        return False
    target = ["--chat-id", chat_id] if chat_id else ["--user-id", uid]
    card = apply_src_tag(card)  # 来源标记（LARK_SRC_TAG 未设置时为 no-op）
    payload = json.dumps(card, ensure_ascii=False)
    try:
        r = subprocess.run(
            [cli, "im", "+messages-send"] + target +
            ["--msg-type", "interactive", "--content", payload, "--as", as_ident],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            env=_env_for_cli(as_ident),
        )
        ok = r.returncode == 0
        print(f"    卡片推送[{as_ident}] {'群' if chat_id else '私聊'}: {'成功' if ok else '失败'} {(r.stdout or r.stderr).strip()[:160]}")
        if not ok:
            _write_feishu_fallback(card, "lark-cli 非零退出: %s" % ((r.stderr or r.stdout).strip()[:160]))
        return ok
    except Exception as e:
        print(f"    !! 卡片推送异常: {e!r}")
        _write_feishu_fallback(card, "lark-cli 异常: %s" % e)
        return False


def _write_feishu_fallback(card, reason):
    """R3/P2-4（T-20260915-001）：飞书推送失败时写本地兜底文件，确保补跑失败/告警可被发现。
    写入 data/schedules/feishu_fallback_<date>.json（同文件追加，保持历史）。"""
    try:
        fallback_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "schedules")
        os.makedirs(fallback_dir, exist_ok=True)
        path = os.path.join(fallback_dir, "feishu_fallback_%s.json" % time.strftime("%Y%m%d"))
        title = (card or {}).get("header", {}).get("title", {}).get("content", "未知")
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "title": title, "reason": reason}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
        if not isinstance(data, list):
            data = []
        data.append(rec)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"    [fallback] 飞书推送失败已写本地兜底: {path}")
    except Exception as e:
        print(f"    !! fallback 文件写入失败: {e!r}")


if __name__ == "__main__":
    # 自检：打印一张演示卡 JSON，便于人工核对结构
    demo = build_signal_card({
        "code": "603969.SH", "action": "SELL_STOP",
        "note": "现价 12.85 跌破止损位 13.20", "last_price": 12.85, "cost": 14.20,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    print(json.dumps(demo, ensure_ascii=False, indent=2))
