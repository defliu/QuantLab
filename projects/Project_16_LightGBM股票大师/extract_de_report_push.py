# -*- coding: utf-8 -*-
"""提取 DE 持仓复盘报告的推送摘要（覆盖报告全部核心章节）。

用法: python extract_de_report_push.py <report.md>
输出: 摘要正文 -> stdout（只含要推送的内容）
      解析统计 -> stderr（供 de_report_daily.ps1 记日志，不进消息）

退出码:
  0  正常（可能有章节未解析，会在正文显式标注）
  1  FAIL_NO_ROWS  逐票持仓完全无法解析
  1  FAIL_READ     报告文件读不出来

2026-09-28 重写。原实现按固定章节序号（"## 二"取持仓、"## 三"取资金池）与固定列下标
解析，对当日真实报告全部失效（FAIL_NO_ROWS），飞书只收到"报告路径 + 提取失败"。
四处结构性缺陷及本次修法:
  1) 章节按标题关键词定位，不依赖序号 —— 报告增删/调序不影响;
  2) 章节正文递归包含三级子章节 —— 盘后 8 只持仓分散在 G2/融合/V1.3 三张
     ### 子表里，旧实现只能读到第一张;
  3) 列按表头名称映射，不依赖列序 —— 兼容盘后 15 列与午休 12 列两种表头;
  4) 表内无"策略"列时，从所属 ### 标题继承策略 —— 午休成交表靠这个才有归属。
口径: 距止损% = (现价 - 成本x0.93) / 现价，即"还能跌多少才触发"；
      距止盈% = (成本x1.15 - 现价) / 现价，即"还能涨多少才到线"。
      午休原报告用止损价做分母，本推送统一为现价口径并在消息中标注，
      避免同一账号出现两套缓冲数字。
"""
import io
import re
import sys

CODE_RE = re.compile(r"^\d{6}(\.[A-Z]{2})?$")

STOP_K = 0.93
TP_K = 1.15

# 逐票持仓表列映射：按表头文本匹配，元组顺序即优先级
HOLDING_COLS = [
    ("code", r"^代码$|证券代码"),
    ("name", r"^名称$|证券名称"),
    ("strat", r"^策略$|策略"),
    ("shares", r"^股数$|持仓股数|股数"),
    ("cost", r"成本"),
    ("px", r"^收盘价$|^现价$|最新价"),
    ("chg", r"当日涨跌|涨跌幅"),
    ("pnl_pct", r"浮盈亏%|浮盈亏率|收益率"),
    ("hold", r"已持|满期剩余|持有.*剩余"),
]
TRADE_COLS = [
    ("time", r"^时间$|成交时间"),
    ("action", r"^动作$|买卖方向"),
    ("code", r"^代码$|证券代码"),
    ("name", r"^名称$|证券名称"),
    ("strat", r"^策略$|策略"),
    ("shares", r"^股数$|成交股数"),
    ("beat", r"成交价|卖出价|买入价"),
    ("pnl_amt", r"实现盈亏|估算盈亏|浮动盈亏"),
    ("reason", r"原因|说明"),
]
BRIDGE_COLS = [
    ("bridge", r"^桥$|^桥名称$|^名称$"),
    ("acct", r"账号"),
    ("beat", r"心跳|build_tag|构建"),
    ("pending", r"pending|挂单|待处理"),
    ("risk", r"风控|触发"),
    ("note", r"评估|说明|备注|peak|高点"),
]
RISK_COLS = [
    ("rank", r"^排名$|名次"),
    ("code", r"^代码$|证券代码"),
    ("name", r"^名称$|证券名称"),
    ("strat", r"^策略$|策略"),
    ("stop_buf", r"距止损|止损缓冲"),
    ("tp_space", r"距止盈"),
    ("signal", r"信号|关注|结论"),
]
CAP_COLS = [
    ("strat", r"^策略$|^名称$|策略"),
    ("value", r"资金池|金额|余额"),
    ("updated", r"更新时间|更新时刻|^时间$"),
    ("note", r"备注|说明|口径"),
]


def clean(cell):
    """去 Markdown 强调/反引号/转义，压缩空白；保留 *ST 前缀（风险标识）。"""
    s = cell.replace("**", "").replace("`", "").replace("\\", "")
    return re.sub(r"\s+", " ", s).strip()


def num(s):
    """从单元格取第一个数字（含正负号、千分位），失败返回 None。"""
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", clean(s).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_sep(cells):
    filled = [c for c in cells if c != ""]
    return bool(filled) and all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in filled)


def is_hr(line):
    """Markdown 水平分隔线。渲染列表时必须跳过，否则 "---" 会被并进上一条目末尾。"""
    return bool(re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line or ""))


def r_rank_num(v):
    """报告给出的名次转 int，解析不出返回 None。"""
    n = num(v)
    return int(n) if n is not None else None


def split_sections(lines):
    """按 # ~ #### 标题切段，返回 [(level, title, body_lines)]，保持文档顺序。"""
    secs = []
    cur = (0, "", [])
    for ln in lines:
        m = re.match(r"^(#{1,4})\s+(.*\S)\s*$", ln)
        if m:
            secs.append(cur)
            cur = (len(m.group(1)), m.group(2).strip(), [])
        else:
            cur[2].append(ln)
    secs.append(cur)
    return secs


def section_lines(secs, i):
    """章节 i 的正文 + 其后所有更深层级子章节（标题行保留，供策略继承）。"""
    lvl = secs[i][0]
    out = list(secs[i][2])
    for j in range(i + 1, len(secs)):
        if secs[j][0] > lvl:
            out.append("#" * secs[j][0] + " " + secs[j][1])
            out.extend(secs[j][2])
        else:
            break
    return out


def find_sec(secs, *keywords):
    """按标题关键词定位章节，返回下标；找不到返回 -1。

    多个章节同时命中时取层级最浅的（## 优先于 ###），同级取靠前者。
    否则 "### G2 —— ...风控兜底出场" 会抢在 "## 五、风控体检" 之前命中。
    """
    hits = [i for i, (_lvl, title, _body) in enumerate(secs)
            if any(k in title for k in keywords)]
    if not hits:
        return -1
    return min(hits, key=lambda i: (secs[i][0], i))


def parse_tables(lines):
    """把 | 行切成表格，返回 [(header, rows, ctx_heading)]，ctx_heading 为表上方最近的标题。"""
    out = []
    i, n = 0, len(lines)
    ctx = ""
    while i < n:
        t = lines[i].strip()
        if t.startswith("#"):
            ctx = re.sub(r"^#+\s*", "", t)
            i += 1
            continue
        if not t.startswith("|"):
            i += 1
            continue
        block = []
        while i < n and lines[i].strip().startswith("|"):
            block.append(lines[i])
            i += 1
        rows = [split_row(b) for b in block]
        rows = [r for r in rows if not is_sep(r) and any(c for c in r)]
        if len(rows) >= 2:
            out.append((rows[0], rows[1:], ctx))
    return out


def resolve_cols(header, spec):
    """表头名 -> 列下标。按 spec 顺序扫描，每列只被认领一次。"""
    idx, taken = {}, set()
    cleaned = [clean(h) for h in header]
    for field, pat in spec:
        if field in idx:
            continue
        rx = re.compile(pat)
        for i, h in enumerate(cleaned):
            if i in taken or not rx.search(h):
                continue
            idx[field] = i
            taken.add(i)
            break
    return idx


def cell(row, idx, field):
    i = idx.get(field)
    if i is None or i >= len(row):
        return ""
    return clean(row[i])


def strat_key(s):
    """策略名归一化：去括号内容与 ENS 后缀，用于跨命名匹配。

    资金池表写 "融合 ENS"，持仓表写 "融合"，直接子串比较会漏配；
    归一化后两者同键。融合持仓表策略列本身是 "融合"，无需归一化参与，
    这里只用于匹配侧，渲染侧仍显示报告原文。
    """
    s = re.sub(r"[（(].*", "", s or "")
    s = s.replace("ENS", "").replace(" ", "").replace("_", "")
    return s.strip().upper()


def strat_from_title(title):
    """从 ### 标题推断策略名（午休成交表无策略列时用）。"""
    if "ENS" in title.upper() or "融合" in title:
        return "融合"
    m = re.search(r"V1\.?\d*", title, re.I)
    if m:
        return m.group(0)
    if "G2" in title.upper():
        return "G2"
    return ""


def infer_action(reason, sec_title=""):
    """无"动作"列时推断买卖方向。

    先看原因文本，再看所在章节标题 —— 盘后"已实现盈亏明细（当日卖出成交）"
    里的"持有到期（满10日）"不含 SELL/卖出 字样，只有章节标题能定方向，
    否则这类到期卖出会显示成 "—"。
    """
    u = (reason + " " + sec_title).upper()
    if "SELL" in u or "卖出" in u:
        return "SELL"
    if "BUY" in u or "买入" in u:
        return "BUY"
    return "—"


def render_list(body, limit):
    """抽取编号/项目符号条目（含续行合并），超预算显式标注剩余条数。"""
    items = []
    for ln in body:
        s = ln.rstrip()
        if is_hr(s):
            continue
        if re.match(r"^\s*(\d+[.)]|[-*+])\s+", s):
            items.append(re.sub(r"^\s*(\d+[.)]|[-*+])\s+", "", s).strip())
        elif items and s.strip() and not s.lstrip().startswith(("|", "#", ">")):
            items[-1] += " " + s.strip()
    items = [clean(i) for i in items if i]
    if not items:
        return "", 0
    out, used = [], 0
    for it in items:
        if used + len(it) + 4 > limit:
            out.append("  …（另有 %d 条因长度上限未展开，见报告全文）" % (len(items) - len(out)))
            break
        out.append("  " + it)
        used += len(it) + 4
    return "\n".join(out), len(items)


def render_actions(secs, i):
    """策略动作：按 ### 子章节分组输出，保留每个子章节的归属标题。"""
    if i < 0:
        return "", 0
    lvl = secs[i][0]
    blocks, total = [], 0
    for j in range(i, len(secs)):
        if j > i and secs[j][0] <= lvl:
            break
        text, n = render_list(secs[j][2], 1500)
        if not text:
            continue
        total += n
        if j == i:
            blocks.append(text)
        else:
            head = re.split(r"[（(]", secs[j][1])[0]
            head = re.split(r"——| -- ", head)[0].strip() or secs[j][1][:24]
            blocks.append("▸ " + (head[:34] + ("…" if len(head) > 34 else "")))
            blocks.append(text)
    return "\n".join(blocks), total


def risk_flag(stop_buf):
    if stop_buf < 3.0:
        return "🔴"
    if stop_buf < 5.0:
        return "🟡"
    return "🟢"


def strat_emoji(strat):
    if "V1" in strat.upper():
        return "🟦"
    if "G2" in strat.upper():
        return "🟩"
    if "融合" in strat or "ENS" in strat.upper():
        return "🟧"
    return "⬜"


def clip(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def fmt_amt(n):
    return "{:+,.2f}".format(n)


def fmt_num(n):
    return "{:,.2f}".format(n)


def fmt_pct(n):
    return "{:+,.2f}%".format(n)


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("USAGE: extract_de_report_push.py <report.md>\n")
        print("FAIL_ARGS")
        sys.exit(1)
    path = sys.argv[1]
    try:
        text = io.open(path, encoding="utf-8").read()
    except Exception as e:
        sys.stderr.write("[抽取] 读取报告失败: %s\n" % e)
        print("FAIL_READ")
        sys.exit(1)

    lines = text.splitlines()
    secs = split_sections(lines)
    stat, warn = {}, []

    # 报告类型决定哪些章节属于"应当存在"。午休报告天然不含资金池/风控排行，
    # 对这些做缺位告警只会制造噪声；盘后缺了就说明生成不完整，必须提示。
    is_close = "midday" not in path and "午休" not in text[:300]

    def note_missing(block, sec_idx, parsed, expect_in=None):
        """章节缺位要显式说明，否则用户无法区分"报告没写"和"我没解析出来"。

        expect_in 给出该任务应含的章节名；午休报告本就不含资金池/风控，
        属正常缺位，只记 stderr 不进消息，避免噪声。
        """
        if sec_idx < 0:
            stat["缺章节:" + block] = 1
            if expect_in == "required":
                warn.append("❓ %s：报告未找到该章节，请确认生成是否完整" % block)
            return
        if parsed == 0:
            warn.append("⚠️ %s：报告有该章节，但未解析出结构化数据，请查报告全文" % block)

    # ---------------- 逐票持仓 ----------------
    hold_i = find_sec(secs, "逐票持仓", "持仓明细", "持仓表")
    rows = []
    if hold_i >= 0:
        for header, body_rows, ctx in parse_tables(section_lines(secs, hold_i)):
            idx = resolve_cols(header, HOLDING_COLS)
            if not all(k in idx for k in ("code", "name", "cost", "px")):
                continue
            ctx_strat = strat_from_title(ctx)
            for r in body_rows:
                code = cell(r, idx, "code")
                if not CODE_RE.match(code):
                    continue
                cost = num(cell(r, idx, "cost"))
                px = num(cell(r, idx, "px"))
                if cost is None or px is None or cost <= 0:
                    continue
                rows.append({
                    "code": code,
                    "name": cell(r, idx, "name"),
                    "strat": cell(r, idx, "strat") or ctx_strat or "未标注",
                    "shares": num(cell(r, idx, "shares")) or 0.0,
                    "cost": cost,
                    "px": px,
                    "chg": num(cell(r, idx, "chg")),
                    "hold": cell(r, idx, "hold"),
                    "rep_pnl_pct": num(cell(r, idx, "pnl_pct")),
                })
    stat["持仓行"] = len(rows)

    if not rows:
        sys.stderr.write("[抽取] 逐票持仓未解析到任何行（章节命中=%d）\n" % (hold_i >= 0))
        print("FAIL_NO_ROWS")
        sys.exit(1)
    note_missing("逐票持仓", hold_i, len(rows))

    seen = set()
    uniq = []
    for r in rows:
        k = (r["code"], r["strat"], round(r["shares"], 2))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    if len(uniq) != len(rows):
        stat["持仓去重"] = len(rows) - len(uniq)
    rows = uniq

    for r in rows:
        r["mv"] = r["px"] * r["shares"]
        r["pnl"] = (r["px"] - r["cost"]) * r["shares"]
        # 收益率按现价/成本重算，不照抄报告表。报告里的"浮盈亏%"分母口径不统一
        #（688450 报告写 -2.24%，按收盘价 31.94/成本 33.4354 重算为 -4.47%，
        #  疑似用成本含费前的均价），重算值可自洽；不一致会在消息中显式标注。
        r["pnl_pct"] = (r["px"] / r["cost"] - 1.0) * 100.0
        stop = r["cost"] * STOP_K
        tp = r["cost"] * TP_K
        r["stop"] = stop
        r["tp"] = tp
        r["stop_buf"] = (r["px"] - stop) / r["px"] * 100.0
        r["tp_space"] = (tp - r["px"]) / r["px"] * 100.0
        # 上下空间比 = 剩余止盈空间 / 剩余止损空间，大于 1 表示上行空间大于下行风险。
        # 注意不是持仓盈亏比：后者等于 pnl_pct 本身，重复展示无意义。
        r["rr"] = r["tp_space"] / r["stop_buf"] if r["stop_buf"] > 1e-9 else 99.99

    # 报告原表浮盈亏% 与重算值偏差过大时留痕，交由人工核对口径
    pct_mismatch = []
    for r in rows:
        rep = r.get("rep_pnl_pct")
        if rep is None:
            continue
        if abs(rep - r["pnl_pct"]) > 0.5:
            pct_mismatch.append("%s 报告 %s / 重算 %s" % (
                r["code"], fmt_pct(rep), fmt_pct(r["pnl_pct"])))
    if pct_mismatch:
        stat["浮盈亏率口径不一致"] = len(pct_mismatch)
        warn.append("🔍 浮盈亏率与报告原表不一致（本消息按现价/成本重算）："
                    + "；".join(pct_mismatch))

    total_mv = sum(r["mv"] for r in rows)
    total_pnl = sum(r["pnl"] for r in rows)

    # ---------------- 当日成交 ----------------
    trades, seen_t = [], set()
    for i, (_lvl, title, _body) in enumerate(secs):
        if not any(k in title for k in ("成交", "已实现", "卖出", "交易")):
            continue
        for header, body_rows, ctx in parse_tables(section_lines(secs, i)):
            idx = resolve_cols(header, TRADE_COLS)
            if "code" not in idx or not ({"action", "reason"} & set(idx)):
                continue
            if "time" not in idx and "beat" not in idx:
                continue
            ctx_strat = strat_from_title(ctx)
            for r in body_rows:
                code = cell(r, idx, "code")
                if not CODE_RE.match(code):
                    continue
                reason = cell(r, idx, "reason")
                action = cell(r, idx, "action") or infer_action(reason, title)
                shares = num(cell(r, idx, "shares")) or 0.0
                tm = cell(r, idx, "time")
                key = (code, action, tm, round(shares, 2))
                if key in seen_t:
                    continue
                seen_t.add(key)
                trades.append({
                    "time": tm,
                    "action": action,
                    "code": code,
                    "name": cell(r, idx, "name"),
                    "strat": cell(r, idx, "strat") or ctx_strat or "—",
                    "shares": shares,
                    "beat": num(cell(r, idx, "beat")),
                    "pnl": num(cell(r, idx, "pnl_amt")),
                    "reason": reason,
                })
    trades.sort(key=lambda t: (t["time"] or "99") if t["time"] else "99")
    stat["成交笔数"] = len(trades)

    realized = None
    m = re.search(r"(?:当日)?(?:已)?实现盈亏合计[^\d+\-]{0,40}([-+]?[\d,]+\.?\d*)", text)
    if m:
        realized = num(m.group(1))
    if realized is None:
        got = [t["pnl"] for t in trades if t["pnl"] is not None]
        if got:
            realized = sum(got)
    if realized is not None:
        stat["已实现"] = round(realized, 2)

    # ---------------- 资金池 ----------------
    # 全文档扫表（不依赖标题，容错最好），但记录首个命中章节下标用于缺位提示
    cap, seen_c, cap_i = [], set(), -1
    for i, (_lvl, _title, _body) in enumerate(secs):
        for header, body_rows, _ctx in parse_tables(section_lines(secs, i)):
            idx = resolve_cols(header, CAP_COLS)
            if "strat" not in idx or "value" not in idx:
                continue
            if cap_i < 0:
                cap_i = i
            for r in body_rows:
                nm = cell(r, idx, "strat")
                v = num(cell(r, idx, "value"))
                if not nm or v is None:
                    continue
                key = (nm[:6], round(v, 2))
                if key in seen_c:
                    continue
                seen_c.add(key)
                cap.append({"strat": nm, "value": v,
                            "updated": cell(r, idx, "updated"),
                            "note": cell(r, idx, "note")})
    stat["资金池"] = len(cap)
    # 盘后必有资金池章节；午休报告本身不含，不作为告警
    note_missing("资金池", cap_i, len(cap), "required" if is_close else None)

    # ---------------- 桥健康 ----------------
    br_i = find_sec(secs, "桥健康", "心跳", "桥状态")
    bridges, seen_b = [], set()
    if br_i >= 0:
        for header, body_rows, _ctx in parse_tables(section_lines(secs, br_i)):
            idx = resolve_cols(header, BRIDGE_COLS)
            if "bridge" not in idx:
                continue
            for r in body_rows:
                nm = cell(r, idx, "bridge")
                if not nm or not re.search(r"[A-Za-z\u4e00-\u9fa5]", nm):
                    continue
                if nm in seen_b:
                    continue
                seen_b.add(nm)
                bridges.append({
                    "name": nm,
                    "acct": cell(r, idx, "acct"),
                    "beat": cell(r, idx, "beat"),
                    "pending": cell(r, idx, "pending"),
                    "risk": cell(r, idx, "risk"),
                    "note": cell(r, idx, "note"),
                })
    stat["桥"] = len(bridges)
    note_missing("桥健康", br_i, len(bridges))

    # ---------------- 风控排行 ----------------
    rk_i = find_sec(secs, "风控体检", "风险排行", "缓冲排行", "风控")
    risk_rows = []
    if rk_i >= 0:
        for header, body_rows, _ctx in parse_tables(section_lines(secs, rk_i)):
            idx = resolve_cols(header, RISK_COLS)
            if "code" not in idx or "stop_buf" not in idx:
                continue
            for r in body_rows:
                code = cell(r, idx, "code")
                sb = num(cell(r, idx, "stop_buf"))
                if not CODE_RE.match(code) or sb is None:
                    continue
                risk_rows.append({
                    "rank": cell(r, idx, "rank"),
                    "code": code,
                    "name": cell(r, idx, "name"),
                    "strat": cell(r, idx, "strat"),
                    "stop_buf": sb,
                    "tp_space": num(cell(r, idx, "tp_space")),
                    "signal": cell(r, idx, "signal"),
                })
    stat["风控行"] = len(risk_rows)
    # 风控排行是盘后风险体检主体；午休报告本来就不出这张表
    if is_close:
        note_missing("风控排行", rk_i, len(risk_rows), "required")
    elif rk_i >= 0 and not risk_rows:
        note_missing("风控排行", rk_i, 0)

    # ---------------- 策略动作 ----------------
    act_i = find_sec(secs, "动作汇总", "策略动作", "各策略动作", "已成交动作")
    actions, n_act = render_actions(secs, act_i)
    stat["策略动作条数"] = n_act
    note_missing("策略动作", act_i, n_act)

    # ---------------- 关注点 ----------------
    fc_i = find_sec(secs, "关注点", "明日关注", "午后关注", "次日")
    focus, n_focus = ("", 0)
    if fc_i >= 0:
        focus, n_focus = render_list(section_lines(secs, fc_i), 1500)
    stat["关注点条数"] = n_focus
    note_missing("关注点", fc_i, n_focus, "required")

    # ---------------- 数据口径 ----------------
    dc_i = find_sec(secs, "数据缺失", "口径声明", "数据来源", "缺失标注")
    caveat, n_dc = ("", 0)
    if dc_i >= 0:
        caveat, n_dc = render_list(section_lines(secs, dc_i), 900)
    stat["口径条数"] = n_dc
    note_missing("数据口径", dc_i, n_dc)

    # ---------------- 组装 ----------------
    m = re.search(r"(\d{8})", path) or re.search(r"(\d{8})", text[:200])
    d = ""
    if m:
        g = m.group(1)
        d = "%s-%s-%s" % (g[:4], g[4:6], g[6:8])
    label = "盘后" if is_close else "午休"
    focus_title = "明日关注" if label == "盘后" else "午后关注"

    out = ["📊 P16 %s持仓复盘 · %s" % (label, d)]
    line = "💼 %d 只 ｜ 市值 %s 元 ｜ 浮盈亏 %s 元" % (
        len(rows), fmt_num(total_mv), fmt_amt(total_pnl))
    if realized is not None:
        line += " ｜ 当日已实现 %s 元" % fmt_amt(realized)
    out.append(line)

    hot = sorted(rows, key=lambda r: r["stop_buf"])[:3]
    out.append("⚠️ 距止损最近：" + " ｜ ".join(
        "%s %s 还能跌%.2f%%%s" % (h["code"], h["name"], h["stop_buf"], risk_flag(h["stop_buf"]))
        for h in hot))

    out.append("")
    out.append("── 逐票持仓（距止损=还能跌%触发，止损=成本×0.93，止盈=成本×1.15）──")
    order, groups = [], {}
    for r in rows:
        if r["strat"] not in groups:
            groups[r["strat"]] = []
            order.append(r["strat"])
        groups[r["strat"]].append(r)
    for strat in order:
        grp = groups[strat]
        gmv = sum(r["mv"] for r in grp)
        gpnl = sum(r["pnl"] for r in grp)
        gpct = (gpnl / (gmv - gpnl) * 100.0) if (gmv - gpnl) > 0 else 0.0
        head = "%s %s ｜ %d只 ｜ 市值 %s ｜ 浮盈亏 %s 元(%s)" % (
            strat_emoji(strat), strat, len(grp), fmt_num(gmv), fmt_amt(gpnl), fmt_pct(gpct))
        for c in cap:
            if strat_key(c["strat"]) == strat_key(strat):
                dlt = c["value"] - 100000.0
                head += "\n     💰 资金池 %s 元 ｜ 较10万 %s(%s)" % (
                    fmt_num(c["value"]), fmt_amt(dlt), fmt_pct(dlt / 1000.0))
                if c["updated"]:
                    head += " ｜ 更新 %s" % c["updated"]
                if c["note"]:
                    head += "\n     ⚠ %s" % clip(c["note"], 150)
                break
        out.append(head)
        for r in grp:
            ln = "  · %s %s %d股 现价%.2f" % (
                r["code"], r["name"], int(r["shares"]) if r["shares"] else 0, r["px"])
            if r["chg"] is not None:
                ln += " 当日%s" % fmt_pct(r["chg"])
            ln += " ｜ 成本%.4f ｜ %s%s元(%s)" % (
                r["cost"], "浮盈" if r["pnl"] >= 0 else "浮亏",
                fmt_amt(r["pnl"]), fmt_pct(r["pnl_pct"]))
            ln += " ｜ 止损%.2f余%.2f%%%s ｜ 止盈%.2f还差%.2f%% ｜ 上下空间比%.2f" % (
                r["stop"], r["stop_buf"], risk_flag(r["stop_buf"]),
                r["tp"], r["tp_space"], r["rr"])
            if r["hold"]:
                ln += " ｜ %s" % r["hold"]
            out.append(ln)

    if trades:
        out.append("")
        out.append("── 当日成交 %d 笔 ──" % len(trades))
        for t in trades:
            ln = "  · %s %s %s %s %d股" % (
                t["time"] or "--:--", t["action"], t["code"], t["name"], int(t["shares"]))
            if t["beat"] is not None:
                ln += " @%.3f" % t["beat"]
            if t["pnl"] is not None:
                ln += " 实现%s元" % fmt_amt(t["pnl"])
            if t["strat"] and t["strat"] != "—":
                ln += " [%s]" % t["strat"]
            if t["reason"]:
                ln += " %s" % clip(t["reason"], 60)
            out.append(ln)
    else:
        out.append("")
        out.append("── 当日成交：报告未给出成交明细 ──")

    if bridges:
        out.append("")
        out.append("── 桥健康 ──")
        for b in bridges:
            nm = b["name"] + ("(%s)" % b["acct"] if b["acct"] else "")
            ln = "  · %s ｜ 心跳 %s" % (nm, b["beat"] or "—")
            if b["pending"]:
                ln += " ｜ pending %s" % b["pending"]
            if b["risk"]:
                ln += " ｜ 风控 %s" % b["risk"]
            if b["note"]:
                ln += "\n     %s" % clip(b["note"], 170)
            out.append(ln)

    if risk_rows:
        # 报告标注"升序"但实际名次有错（000610 3.92% 排在 605098 3.77% 之前），
        # 风险排序的唯一用途是"谁最危险"，所以按数值重排，同时保留报告原名次。
        ordered = sorted(risk_rows, key=lambda r: r["stop_buf"])
        stat["风控重排"] = sum(
            1 for pos, r in enumerate(ordered)
            if r_rank_num(r["rank"]) not in (None, pos + 1))
        out.append("")
        out.append("── 风控排行（按距止损升序，本消息重排）──")
        for pos, r in enumerate(ordered):
            rn = r_rank_num(r["rank"])
            tag = "" if rn in (None, pos + 1) else "（报告原第%s名）" % rn
            ln = "  · %d %s %s %s ｜ 距止损%.2f%%%s%s" % (
                pos + 1, r["code"], r["name"], r["strat"],
                r["stop_buf"], risk_flag(r["stop_buf"]), tag)
            if r["tp_space"] is not None:
                ln += " ｜ 距止盈%.2f%%" % r["tp_space"]
            if r["signal"]:
                ln += " ｜ %s" % clip(r["signal"], 90)
            out.append(ln)

    if actions:
        out.append("")
        out.append("── 各策略当日动作 ──")
        out.append(actions)

    if focus:
        out.append("")
        out.append("── %s ──" % focus_title)
        out.append(focus)

    if caveat:
        out.append("")
        out.append("── 数据口径与缺失 ──")
        out.append(caveat)

    if warn:
        out.append("")
        out.append("── 抽取告警 ──")
        for w in warn:
            out.append("  " + w)

    sys.stderr.write("[抽取] " + " ｜ ".join("%s=%s" % kv for kv in stat.items()) + "\n")
    if warn:
        sys.stderr.write("[抽取] 告警: " + "; ".join(warn) + "\n")
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
