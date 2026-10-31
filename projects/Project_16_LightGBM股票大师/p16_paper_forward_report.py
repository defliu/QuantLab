# coding: utf-8
"""P16 纸面前向日报：汇总各纸面臂当日产物，推送一张飞书卡片。

数据源（只读，全部由 paper_forward_daily.ps1 在工作日 16:55 产出）：
  data/real/forward_stats_<YYYY-MM-DD>.md          43f G2 主臂超额统计
  data/real/paper_shadow_b21_<YYYYMMDD>.md         21f 影子臂（B21 k1500）
  data/real/paper_forward_exit_<YYYY-MM-DD>.md     出场规则前向验证
  data/real/paper_forward_downgrade.json           纸面自动降级闸标记

口径（写进卡片，防止拿回填当 OOS）：
  回填样本 = 用同一代码事后补算，等同回测，不作样本外证据；
  只有 source=forward 的样本才算前向。

用法：
  python p16_paper_forward_report.py                 # 取最近交易日 → 推送
  python p16_paper_forward_report.py --date 20261030 # 指定日期
  python p16_paper_forward_report.py --no-send       # 只打印正文/卡片 JSON
  python p16_paper_forward_report.py --wait-min 20   # 等当日报告落盘（定时任务用）

退出码：
  0 推送成功（或 --no-send 成功）
  2 数据陈旧/文件缺失/字段解析失败，但卡片已推送（fail-loud）
  3 卡片推送失败
  4 无任何可用报告，未推送
"""
import argparse
import datetime
import glob
import io
import json
import os
import re
import sys
import time

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

REAL = os.path.join(HERE, "data", "real")
SCHED = os.path.join(HERE, "data", "schedules")

import qmt_card as QC  # noqa: E402


# ---------------------------------------------------------------- 读取与解析
def _read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _pct(v, nd=3):
    if v is None:
        return "—"
    return ("%+." + str(nd) + "f%%") % v


def _t(v):
    if v is None:
        return "—"
    return "%+.2f" % v


def _g(v):
    if v is None:
        return "—"
    return "%g" % v


def _find_dates(prefix, suffix=".md"):
    """返回 <prefix><date><suffix> 里的日期集合（按字符串排序）。"""
    out = set()
    for p in glob.glob(os.path.join(REAL, prefix + "*" + suffix)):
        name = os.path.basename(p)[len(prefix):-len(suffix)] if suffix else os.path.basename(p)[len(prefix):]
        out.add(name)
    return sorted(out)


def _pick(realdir, prefix, want):
    """优先取 want 对应的文件；缺失时回退到不晚于 want 的最近一份。

    want: "YYYY-MM-DD" 或 "YYYYMMDD"
    返回 (path, used_date, fallback) 或 (None, None, False)
    """
    def to_path(d):
        return os.path.join(realdir, prefix + d + ".md")

    if want and os.path.exists(to_path(want)):
        return to_path(want), want, False
    dates = _find_dates(prefix)
    if want:
        dates = [d for d in dates if d.replace("-", "") <= want.replace("-", "")]
    if not dates:
        return None, None, False
    d = dates[-1]
    return to_path(d), d, True


def parse_forward_stats(text):
    d = {}
    m = re.search(r"^# g2 前向纸面验证统计 · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", text, re.M)
    d["generated_at"] = m.group(1) if m else ""
    m = re.search(r"主库最新 (\S+) \| 增量最新 (\S+) \| 持有期 (\d+) 日", text)
    d["main_last"] = m.group(1) if m else ""
    d["inc_last"] = m.group(2) if m else ""
    d["hold"] = int(m.group(3)) if m else None
    m = re.search(r"已回填 (\d+) 笔 / 待回填 (\d+) 笔 / 候选累计 (\d+) 笔", text)
    d["filled"] = int(m.group(1)) if m else None
    d["pending"] = int(m.group(2)) if m else None
    d["total"] = int(m.group(3)) if m else None
    rows = {}
    pat = (r"^\| (\S+) \| (\d+) \| ([+\-\d.]+)% \| ([+\-\d.]+)% \| ([+\-\d.]+)% "
           r"\| ([\d.]+)% \| ([\d.]+)% \| ([+\-\d.]+) \|")
    for label, key in (("全部", "all"), ("TOP3\\(实盘口径\\)", "top3")):
        m = None
        for line in text.splitlines():
            if re.match(r"^\| " + label + r" \|", line):
                m = re.match(pat, line)
                break
        if m:
            rows[key] = {
                "n": int(m.group(2)), "ret": _fnum(m.group(3)), "bench": _fnum(m.group(4)),
                "excess": _fnum(m.group(5)), "win": _fnum(m.group(6)),
                "excess_win": _fnum(m.group(7)), "t": _fnum(m.group(8)),
            }
    d["rows"] = rows
    return d


def parse_exit(text):
    d = {}
    m = re.search(r"^# P16 出场规则前向验证（paper_forward_exit）· (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", text, re.M)
    d["generated_at"] = m.group(1) if m else ""
    m = re.search(r"已到期 (\d+) 笔 / 未到期 (\d+) 笔 / 无入场价 (\d+) 笔 / 候选累计 (\d+) 笔", text)
    d["due"] = int(m.group(1)) if m else None
    d["not_due"] = int(m.group(2)) if m else None
    d["no_entry"] = int(m.group(3)) if m else None
    d["total"] = int(m.group(4)) if m else None
    rules = {}
    for name in ("无止损\\(纯alpha上界\\)", "固定-7%/\\+15%\\(回测原口径\\)",
                 "实盘口径:-7%\\+8%追盈\\+15%", "ATR自适应 2\\.0/2\\.0"):
        m = re.search(r"^\| " + name + r" \| (\d+) \| ([+\-\d.]+)% \| ([\d.]+)% \|", text, re.M)
        if m:
            rules[name.replace("\\", "")] = {
                "n": int(m.group(1)), "mean": _fnum(m.group(2)), "win": _fnum(m.group(3)),
            }
    d["rules"] = rules
    m = re.search(r"^## 判定\s*\n+(.+?)(?:\n## |\Z)", text, re.M | re.S)
    d["verdict"] = re.sub(r"\s*\n\s*", " ", m.group(1)).strip() if m else ""
    return d


def parse_shadow(text):
    d = {}
    m = re.search(r"^# 21f 纸面影子臂（B21 k1500）· (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", text, re.M)
    d["generated_at"] = m.group(1) if m else ""
    m = re.search(r"数据截止：`(\d{8})`", text)
    d["asof"] = m.group(1) if m else ""
    m = re.search(r"\*\*样本\*\*：forward (\d+) 笔 / backfill (\d+) 笔", text)
    d["forward"] = int(m.group(1)) if m else None
    d["backfill"] = int(m.group(2)) if m else None
    m = re.search(
        r"^\| 全部候选 \| n=(\d+) \| ret5 均值 ([+\-\d.]+)% / 中位 ([+\-\d.]+)% / 胜率 (\d+)% \(n=(\d+)\) "
        r"\| ret10 均值 ([+\-\d.]+)% / 中位 ([+\-\d.]+)% / 胜率 (\d+)% \(n=(\d+)\) "
        r"\| ret15 均值 ([+\-\d.]+)% / 中位 ([+\-\d.]+)% / 胜率 (\d+)% \(n=(\d+)\) \|", text, re.M)
    if m:
        d["all"] = {"n": int(m.group(1))}
        for i, k in enumerate(("5", "10", "15")):
            d["all"]["ret" + k] = _fnum(m.group(2 + i * 4))
            d["all"]["win" + k] = _fnum(m.group(4 + i * 4))
            d["all"]["n" + k] = int(m.group(5 + i * 4))
    cmp_rows = {}
    for label, key in ((r"21f 影子（本脚本）", "shadow"), (r"43f live 纸面（同引擎重算）", "live43")):
        m = re.search(r"^\| " + label + r" \| (\d+) \| ([+\-\d.]+)% \| ([+\-\d.]+)% \| (\d+)% \|", text, re.M)
        if m:
            cmp_rows[key] = {
                "n": int(m.group(1)), "ret10": _fnum(m.group(2)),
                "ret15": _fnum(m.group(3)), "win15": _fnum(m.group(4)),
            }
    d["cmp"] = cmp_rows
    m = re.search(r"重叠信号日 (\d+) 个", text)
    d["overlap_days"] = int(m.group(1)) if m else None
    m = re.search(r"名字重合 \*\*(\d+) / (\d+)\*\*（(\d+)%）", text)
    if m:
        d["name_hit"], d["name_all"], d["name_pct"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return d


def load_downgrade():
    path = os.path.join(REAL, "paper_forward_downgrade.json")
    if not os.path.exists(path):
        return None
    try:
        return json.loads(_read(path))
    except Exception:
        return None


# ---------------------------------------------------------------- 选日期
def _today():
    return datetime.date.today()


def _is_weekday(d):
    return d.weekday() < 5


def _dash(compact):
    """'20261030' → '2026-10-30'。"""
    if not compact:
        return ""
    if "-" in compact:
        return compact
    return "%s-%s-%s" % (compact[0:4], compact[4:6], compact[6:8])


def select_date(want, wait_min):
    """返回 (date_str 'YYYY-MM-DD', warnings[])。

    当天是工作日但报告还没落盘时，最多等 wait_min 分钟（定时任务用；
    人工 --no-send 调试传 0 即可）。forward_stats 文件名是紧凑日期
    （forward_stats_20261030.md），另外两份报告分别是紧凑/带横线。
    """
    warnings = []
    today = _today()
    if want:
        return _dash(want), warnings
    d = today.strftime("%Y-%m-%d")
    deadline = time.time() + max(0, wait_min) * 60
    while True:
        if os.path.exists(os.path.join(REAL, "forward_stats_%s.md" % today.strftime("%Y%m%d"))):
            return d, warnings
        if not _is_weekday(today) or time.time() >= deadline:
            break
        time.sleep(60)
    dates = _find_dates("forward_stats_")
    if not dates:
        return None, warnings
    latest = dates[-1]
    if _is_weekday(today):
        warnings.append("今日 %s 的 forward_stats 未落盘（管道可能失败），以下为 %s 的数据" % (d, _dash(latest)))
    return _dash(latest), warnings


# ---------------------------------------------------------------- 卡片
def _arm_state(mark, key):
    st = (mark or {}).get(key) or {}
    return st


def build_body(fs, ex, sh, mark, date_str, warnings, missing):
    lines = []
    for w in warnings:
        lines.append("**⚠ %s**" % w)
    for m in missing:
        lines.append("**⚠ %s**" % m)
    if warnings or missing:
        lines.append("")

    # ① 43f G2 主臂
    if fs:
        lines.append("**① 43f G2 纸面主臂** · 持有 %s 日 open→open" % (fs.get("hold") or "—"))
        lines.append("回填 %s / 待回填 %s / 累计 %s（主库 %s）" % (
            fs.get("filled", "—"), fs.get("pending", "—"), fs.get("total", "—"),
            fs.get("main_last") or "—"))
        r = fs.get("rows", {}).get("all")
        if r:
            lines.append("- 全部：收益 %s · 基准 %s · 超额 %s · 胜率 %s%% · 超额胜率 %s%% · t %s" % (
                _pct(r["ret"]), _pct(r["bench"]), _pct(r["excess"]),
                r["win"], r["excess_win"], _t(r["t"])))
        r = fs.get("rows", {}).get("top3")
        if r:
            lines.append("- TOP3：收益 %s · 基准 %s · 超额 %s · 胜率 %s%% · 超额胜率 %s%% · t %s" % (
                _pct(r["ret"]), _pct(r["bench"]), _pct(r["excess"]),
                r["win"], r["excess_win"], _t(r["t"])))
        lines.append("")

    # ② 21f 影子臂
    if sh:
        lines.append("**② 21f 影子臂（B21 k1500）** · 持有 15 日 · 数据截止 %s" % (sh.get("asof") or "—"))
        fwd, bf = sh.get("forward"), sh.get("backfill")
        if fwd == 0:
            lines.append("forward 0 / backfill %s（**尚无前向样本**，当前全部为回测等价回填）" % (bf,))
        else:
            lines.append("forward %s / backfill %s" % (fwd, bf))
        a = sh.get("all")
        if a:
            lines.append("- ret10 均值 %s · 胜率 %s%%（n=%s）" % (
                _pct(a.get("ret10"), 2), _g(a.get("win10")), a.get("n10")))
            lines.append("- ret15 均值 %s · 胜率 %s%%（n=%s）" % (
                _pct(a.get("ret15"), 2), _g(a.get("win15")), a.get("n15")))
        if sh.get("name_all") is not None:
            lines.append("- 与 43f 名字重合 %s/%s（%s%%）· 重叠信号日 %s" % (
                sh["name_hit"], sh["name_all"], sh["name_pct"], sh.get("overlap_days", "—")))
        lines.append("")

    # ③ 出场规则前向验证
    if ex:
        lines.append("**③ 出场规则前向验证** · 到期 %s / 未到期 %s" % (
            ex.get("due", "—"), ex.get("not_due", "—")))
        rules = ex.get("rules") or {}
        live = rules.get("实盘口径:-7%+8%追盈+15%")
        fixed = rules.get("固定-7%/+15%(回测原口径)")
        if live:
            lines.append("- live_trail（现跑）均值 %s · 胜率 %s%%（n=%s）" % (
                _pct(live["mean"]), live["win"], live["n"]))
        if fixed:
            lines.append("- fixed（回测基线）均值 %s · 胜率 %s%%（n=%s）" % (
                _pct(fixed["mean"]), fixed["win"], fixed["n"]))
        if ex.get("verdict"):
            lines.append("- 判定：%s" % ex["verdict"])
        lines.append("")

    # ④ 降级闸
    if mark:
        lines.append("**④ 纸面自动降级闸**")
        for key in ("G2", "V1.3", "ENS"):
            st = _arm_state(mark, key)
            if not st:
                lines.append("- %s：无记录" % key)
                continue
            if st.get("frozen"):
                lines.append("- %s：**冻结** since %s（n=%s，超额 %s）" % (
                    key, (st.get("since") or "—")[:16], st.get("n", "—"),
                    _pct((st.get("mean_excess") or 0) * 100)))
            else:
                extra = "评估基线 %s" % st["eval_since"] if st.get("eval_since") else "正常"
                lines.append("- %s：正常（%s）" % (key, extra))
        lines.append("")

    lines.append("<font color='grey'>口径：回填样本=回测等价，不作 OOS 证据；forward 才是前向样本。</font>")
    return "\n".join(lines).rstrip()


def build_card(title, subtitle, body, level):
    meta = {
        "ok": ("green", "green", "正常"),
        "warn": ("orange", "orange", "待关注"),
        "bad": ("red", "red", "异常"),
    }[level]
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": meta[0],
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag",
                               "text": {"tag": "plain_text", "content": meta[2]},
                               "color": meta[1]}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": [
                {"tag": "interactive_container", "width": "fill", "has_border": True,
                 "border_color": "%s-100" % meta[1], "background_style": "%s-50" % meta[1],
                 "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
                 "elements": [{"tag": "markdown", "content": body}]},
                {"tag": "markdown",
                 "content": "<font color='grey'>生成 %s · 自动任务 Quant_P16_PaperForward_Report</font>" % ts,
                 "text_size": "notation", "text_align": "center"},
            ],
        },
    }


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="YYYYMMDD，默认取最近交易日产物")
    ap.add_argument("--wait-min", type=int, default=0, help="工作日等当日报告落盘的分钟数")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(SCHED):
        os.makedirs(SCHED)

    date_str, warnings = select_date(args.date, args.wait_min)
    if not date_str:
        print("[FWD] 无任何 forward_stats 报告，未推送")
        return 4

    compact = date_str.replace("-", "")
    missing = []

    fs_path = os.path.join(REAL, "forward_stats_%s.md" % compact)
    fs = parse_forward_stats(_read(fs_path)) if os.path.exists(fs_path) else None
    if fs is None:
        print("[FWD] forward_stats_%s.md 不存在，未推送" % compact)
        return 4
    if fs.get("filled") is None or not fs.get("rows"):
        missing.append("forward_stats 字段解析失败（格式可能已变）")

    ex_path, ex_date, ex_fb = _pick(REAL, "paper_forward_exit_", date_str)
    ex = parse_exit(_read(ex_path)) if ex_path else None
    if ex is None:
        missing.append("出场规则前向报告缺失")
    elif ex_fb:
        missing.append("出场规则前向用 %s 的报告（当日缺失）" % ex_date)
    elif ex.get("due") is None or not ex.get("rules"):
        missing.append("出场规则前向字段解析失败")

    sh_path, sh_date, sh_fb = _pick(REAL, "paper_shadow_b21_", compact)
    sh = parse_shadow(_read(sh_path)) if sh_path else None
    if sh is None:
        missing.append("21f 影子臂报告缺失")
    elif sh_fb:
        missing.append("21f 影子臂用 %s 的报告（当日缺失）" % sh_date)
    elif sh.get("forward") is None or not sh.get("all"):
        missing.append("21f 影子臂字段解析失败")

    mark = load_downgrade()
    if mark is None:
        missing.append("降级闸标记缺失或损坏")

    frozen = [k for k in ("G2", "V1.3", "ENS")
              if ((mark or {}).get(k) or {}).get("frozen")]
    level = "ok"
    if warnings or missing:
        level = "warn"
    if frozen:
        level = "warn"

    subtitle = "%s · 数据日 %s" % (time.strftime("%Y-%m-%d"), date_str)
    body = build_body(fs, ex, sh, mark, date_str, warnings, missing)
    card = build_card("P16 纸面前向日报", subtitle, body, level)

    out = os.path.join(SCHED, "p16_paper_forward_%s.md" % compact)
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("# P16 纸面前向日报 · %s\n\n%s\n" % (date_str, body))
    print(body)
    print("\n[FWD] 正文已落盘 %s" % out)

    try:
        with io.open(os.path.join(SCHED, "p16_paper_forward.log"), "a", encoding="utf-8") as f:
            f.write(u"[%s] date=%s level=%s warnings=%d missing=%d frozen=%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), date_str, level,
                len(warnings), len(missing), ",".join(frozen) or "-"))
    except Exception:
        pass

    rc = 0
    if warnings or missing:
        rc = 2

    if args.no_send:
        print("\n[FWD] --no-send：跳过推送")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return rc

    os.environ.setdefault("LARK_SRC_TAG", "[QD]")
    ok = QC.send_lark_card(card)
    if not ok:
        return 3
    return rc


if __name__ == "__main__":
    sys.exit(main())
