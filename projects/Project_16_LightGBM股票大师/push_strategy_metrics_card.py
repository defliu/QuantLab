# coding: utf-8
"""各策略经营指标卡片：聚合各策略的资金池 / 累计收益 / 已实现·浮动 / 持仓 / 心跳，
单独推一张飞书 Card 2.0 卡片（供盘后复盘时一眼看清账户全景）。

数据源（运行时现读，无缓存）——两层，沙箱外优先（2026-10-16 T-20261016-001）：
  ① 沙箱外采集 qmt_account_metrics.py（xtquant 只读 + FormulaOutput 日志）：
     当日成交（remark 归属）、持仓数（code 集合归属）、build（日志）、心跳（日志 [HEART]/[hb]）。
     QMT 沙箱自闭环改造后策略不再回写 QMT_POOL，旧文件将停更 → 此层为主源。
  ② 旧文件兜底（过渡期；策略停写后为最后落盘值）：
     - Project_16 V1.3 : data/strategy_capital.json（资金池/已实现/浮动，仍为唯一源）
                         + D:/QMT_POOL/p16_v13_risk/state/heart_v13_<date>.json
                         + D:/QMT_POOL/p16_v13_risk/cmd/positions_cfg_v13_<date>.json
     - 价值小盘V2      : D:/QMT_POOL/v2_holdings_state.json + strategy_log_v2.txt（nav）
     - G2 大QMT文件桥  : D:/QMT_POOL/g2_bridge/g2_strategy_capital.json
                         + state/heart_<date>.json + data/reconcile_g2/*
     - 融合大QMT文件桥 : D:/QMT_POOL/p16_ensemble_bridge/ens_strategy_capital.json
                         + state/heart_<date>.json + data/reconcile_g2_ens/*
  资金池/已实现/浮动：账户现金为共享资金无法按策略拆分，仍由旧文件提供（策略停写后为
  最后落盘值，asof 可见；沙箱外不臆造资金口径）。

各策略当日成交（买/卖笔数 + 成交明细表）：
  - 沙箱外：qmt_account_metrics deals（remark 归属），已验证与原文件源同口径。
  - 旧源（fallback）：V1.3 fills_v13_*.json / V2 reconcile orders_v2 / G2·融合 fills_*.json
（push_daily_summary_card 复用本模块的 loader 与成交源，单一事实源）

口径：累计收益 = (最新资金池 - 本金 100000) / 100000。
未部署策略（无 state 文件）按「未部署」如实标注，不臆造数据。

用法：
  python push_strategy_metrics_card.py               # 现读数据 → 推卡片
  python push_strategy_metrics_card.py --no-send     # 只打印卡片 JSON（不发送）
  python push_strategy_metrics_card.py --date 20261014
"""
import argparse
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

PROJECT = os.path.dirname(os.path.abspath(__file__))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import qmt_card as QC  # noqa: E402  卡片构建/发送出口
import qmt_account_metrics as QAM  # noqa: E402  沙箱外账户指标采集（xtquant + 日志）

POOL = r"D:/QMT_POOL"
CAP_BASE = 100000.0

V13_CAP = os.path.join(PROJECT, "data", "strategy_capital.json")
V13_HEART_DIR = os.path.join(POOL, "p16_v13_risk", "state")
V13_CMD_DIR = os.path.join(POOL, "p16_v13_risk", "cmd")

V2_STATE = os.path.join(POOL, "v2_holdings_state.json")
V2_LOG = os.path.join(POOL, "strategy_log_v2.txt")
V2_CAP = os.path.join(POOL, "v2_strategy_capital.json")
V2_HEART_DIR = POOL  # v2_heart_<date>.json 直落池根
V2_RECON_DIR = os.path.join(os.path.dirname(PROJECT), "Project_10_价值小盘V2", "data", "reconcile_v2")

G2_CAP = os.path.join(POOL, "g2_bridge", "g2_strategy_capital.json")
G2_HEART_DIR = os.path.join(POOL, "g2_bridge", "state")
G2_RECON_DIR = os.path.join(PROJECT, "data", "reconcile_g2")

ENS_CAP = os.path.join(POOL, "p16_ensemble_bridge", "ens_strategy_capital.json")
ENS_HEART_DIR = os.path.join(POOL, "p16_ensemble_bridge", "state")
ENS_RECON_DIR = os.path.join(PROJECT, "data", "reconcile_g2_ens")

# 成交源（与盘后总览同源；push_daily_summary_card 亦复用本模块）
V13_FILLS = os.path.join(POOL, "p16_v13_risk", "state", "fills_v13_%s.json")
G2_FILLS = os.path.join(POOL, "g2_bridge", "state", "fills_%s.json")
ENS_FILLS = os.path.join(POOL, "p16_ensemble_bridge", "state", "fills_%s.json")

# 已部署 4 个之外的策略（无 state 文件）→ 卡片底部如实标注「未部署」
NOT_DEPLOYED = ["ATR低波等权", "黄氏529主升浪", "全天候底仓"]


# ---------------- 读取工具 ----------------

def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _latest_json(d, prefix, suffix=".json"):
    """目录下匹配 prefix 的最新文件全路径（按文件名排序取最后）；无则 None。"""
    try:
        files = sorted(f for f in os.listdir(d)
                       if f.startswith(prefix) and f.endswith(suffix))
    except Exception:
        files = []
    return os.path.join(d, files[-1]) if files else None


def _heart(d, prefix, date):
    """先精确命中当日，回退最近一份。"""
    exact = _read_json(os.path.join(d, "%s%s.json" % (prefix, date)))
    if exact:
        return exact
    return _read_json(_latest_json(d, prefix)) or {}


def _pick_json(d, prefix, date):
    """先精确命中当日，回退最近一份。"""
    exact = _read_json(os.path.join(d, "%s%s.json" % (prefix, date)))
    if exact:
        return exact
    return _read_json(_latest_json(d, prefix)) or {}


def _bridge_fills(path):
    """读桥的 fills_<date>.json，取 FILLED 且 vol>0。返回 [{action,code,vol,price,status}]。"""
    d = _read_json(path) or {}
    fills = []
    for x in (d.get("fills") or []):
        if str(x.get("status") or "").upper() != "FILLED":
            continue
        if int(x.get("vol") or 0) <= 0:
            continue
        fills.append({"action": x.get("action"), "code": x.get("code"),
                      "vol": x.get("vol"), "price": x.get("price"),
                      "status": x.get("status")})
    return fills


def _v2_fills(recon):
    """从 reconcile_v2 的 orders_v2 取当日成交（traded_volume>0）。
    方向取自 remark（strategy_v2.py 写「V2买入/V2卖出」）。"""
    fills = []
    for o in (recon.get("orders_v2") or []):
        traded = int(o.get("traded_volume") or 0)
        if traded <= 0:
            continue
        rk = str(o.get("remark") or "")
        action = "BUY" if "买入" in rk else ("SELL" if "卖出" in rk else "")
        fills.append({"action": action, "code": o.get("code"), "vol": traded,
                      "price": o.get("price"), "status": o.get("status")})
    return fills


def _count(fills):
    buy = sum(1 for f in fills if f.get("action") == "BUY")
    sell = sum(1 for f in fills if f.get("action") == "SELL")
    return buy, sell


def _hhmm(ts):
    ts = (ts or "").strip()
    return ts[:16] if len(ts) >= 16 else ts


# ---------------- 沙箱外采集层（T-20261016-001） ----------------

def _sandbox_metrics(date):
    """优先现采（xtquant 只读 + FormulaOutput 日志），失败回退当日留档。

    现采成功即写留档（同日幂等覆盖）→ 任何一次卡片推送都顺带维护档案，
    卡片失败也不丢当日数据（T-20261016-001：模拟端 deal/order 仅当日，留档是跨日统计刚需）。
    采集/回退均失败返回 {}（各 loader 自动退回旧文件源，不阻断卡片）。
    """
    try:
        m = QAM.collect(date, dry_run=False, write=True)
        if m and any(a.get("connected") for a in (m.get("accounts") or [])):
            return m
    except Exception as e:
        print("[SANDBOX] 现采失败, 回退留档: %r" % (e,))
    try:
        m = QAM.load(date)
        if m:
            print("[SANDBOX] 使用当日留档 %s" % m.get("generated_at", ""))
            return m
    except Exception:
        pass
    return {}


def _sb_account_ok(m, tag):
    """策略 tag 对应账号是否连通（V13/V2=67014907，G2I/ENSI/ATR=70180771）。

    账号未连通时沙箱数据为空壳，返回 False → loader 退回旧文件源。
    """
    want = "67014907" if tag in ("V13", "V2") else "70180771"
    for a in (m.get("accounts") or []):
        if a.get("account_id") == want:
            return bool(a.get("connected"))
    return False


def _slice_txt(m, tag):
    """版本切片文案：内产版独立口径（基线→当前）或文件桥版历史。"""
    if not m:
        return ""
    sl = ((m.get("version_slice") or {}).get("slice") or {}).get(tag)
    if not sl:
        return ""
    if sl.get("version") != "inprod":
        return "版本 文件桥版(历史) · build %s" % (sl.get("build") or "—")
    if sl.get("pnl_since_baseline") is None:
        return "版本 内产版 · build %s · 基线缺失" % (sl.get("build") or "—")
    return ("**内产版独立业绩** 自 %s 基线 %s → 现 %s　Δ%s（%+.2f%%）"
            % (sl.get("baseline_date") or "—",
               _money(sl.get("baseline_capital")),
               _money(sl.get("current_capital")),
               _signed(sl.get("pnl_since_baseline")),
               float(sl.get("return_since_baseline") or 0) * 100))


def _sb_fills(m, tag):
    """沙箱成交 → 卡片 fills 格式 [{action,code,vol,price,status}]。

    账号未连通或 tag 缺失返回 None（loader 保留旧文件源）；
    连通则返回列表（可为空=当日无成交，如实覆盖）。
    """
    if not m or not _sb_account_ok(m, tag):
        return None
    s = (m.get("strategies") or {}).get(tag)
    if s is None:
        return None
    return [{"action": f.get("action"), "code": f.get("code"),
             "vol": f.get("vol"), "price": f.get("price"), "status": "FILLED"}
            for f in (s.get("fills") or []) if f.get("vol")]


def _sb_hold(m, tag):
    """持仓数：账户连通用账户真相；第二实例无直连时退回日志 [HOLD] 计数。"""
    if not m:
        return None
    s = (m.get("strategies") or {}).get(tag) or {}
    h = s.get("hold")
    if h is not None:
        return h
    hh = ((m.get("log") or {}).get("holds") or {}).get(tag)
    return hh.get("n") if hh else None


def _sb_float(m, tag):
    """沙箱实时浮动（账户市值 − 成本×股数）。账号未连通返回 None（保留旧文件值）。"""
    if not m or not _sb_account_ok(m, tag):
        return None
    s = (m.get("strategies") or {}).get(tag) or {}
    return s.get("float_pnl")


def _sb_cost_src(m, tag):
    """浮动/市值的来源：account=账户真实持仓；unavailable=无来源（不可当 0 用）。"""
    s = ((m.get("strategies") or {}).get(tag)) or {}
    return s.get("cost_src") or ""


def _sb_money(m, tag):
    """沙箱 (持仓市值, 持仓成本)。账号未连通或无真实持仓数据返回 (None, None)。"""
    if not m or not _sb_account_ok(m, tag) or _sb_cost_src(m, tag) != "account":
        return None, None
    s = (m.get("strategies") or {}).get(tag) or {}
    return s.get("market_value"), s.get("cost")


def _sb_build(m, tag):
    s = ((m.get("strategies") or {}).get(tag)) or {}
    return s.get("build") or ""


def _sb_heartbeat(m, tag):
    s = ((m.get("strategies") or {}).get(tag)) or {}
    h = s.get("heart") or {}
    return h.get("ts") or ""


# ---------------- 各策略加载 ----------------

def _load_v13(date, m=None):
    cap = _read_json(V13_CAP) or {}
    heart = _heart(V13_HEART_DIR, "heart_v13_", date)
    pos = _pick_json(V13_CMD_DIR, "positions_cfg_v13_", date)
    hold = len([p for p in (pos.get("positions") or [])
                if int(p.get("vol", 0) or 0) > 0])
    fills = _bridge_fills(V13_FILLS % date)
    # 沙箱外优先：成交/持仓/build/心跳（策略停写后旧文件停更）
    sb_hold = _sb_hold(m, "V13") if m else None
    sb_fills = _sb_fills(m, "V13") if m else None
    sb_build = _sb_build(m, "V13") if m else ""
    sb_hb = _sb_heartbeat(m, "V13") if m else ""
    if sb_hold is not None:
        hold = sb_hold
    if sb_fills is not None:
        fills = sb_fills
    buy, sell = _count(fills)

    realized = cap.get("realized")
    float_ = cap.get("float_pnl")
    mv = None
    derived = False
    # 沙箱实时浮动优先（账户市值−成本，2026-10-16 截图核对：旧文件浮动失真）
    sb_float = _sb_float(m, "V13") if m else None
    if sb_float is not None:
        float_ = sb_float
        mv, _c = _sb_money(m, "V13")
        if realized is not None:
            capital = round(CAP_BASE + float(realized) + float(sb_float), 2)
            derived = "已实现(文件) + 浮动(账户实时)"
        else:
            capital = cap.get("capital")
    else:
        capital = cap.get("capital")

    return {
        "short": "V1.3",
        "name": "Project_16 V1.3",
        "channel": "miniQMT · 67014907",
        "capital": capital,
        "realized": realized,
        "float": float_,
        "derived": derived,
        "mv": mv,
        "hold": hold,
        "buy": buy, "sell": sell, "fills": fills,
        "heartbeat": sb_hb or heart.get("last_heartbeat", ""),
        "build": sb_build or heart.get("build_tag", ""),
        "asof": cap.get("updated_at", ""),
    }


def _load_v2(date, mets=None):
    # 参数名 mets：函数体下方有 `m = re.search(...)`，与沙箱 metrics 参数同名会互相覆盖
    st = _read_json(V2_STATE) or {}
    holdings = st.get("holdings") or {}
    entry = st.get("entry_prices") or {}
    hold = len([c for c, v in holdings.items() if float(v or 0) > 0])

    # 原生产物优先（2026-10-14 起 V2 自报 capital/heart，与其他 3 个策略同构）；
    # 缺失则回退「日志 nav + 账本反推」（旧版策略兼容）。
    cap_native = _read_json(V2_CAP) or {}
    heart = _heart(V2_HEART_DIR, "v2_heart_", date)
    recon = _pick_json(V2_RECON_DIR, "reconcile_v2_", date)

    nav = None
    hb = ""          # 日志兜底心跳
    log_build = ""   # 日志兜底 build（[init] 行，取最后一个）
    try:
        with open(V2_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r"nav=([0-9.]+)", line)
                if m:
                    nav = float(m.group(1))
                    hb = line.strip()[:19]
                mb = re.search(r"\[init\].*?\bbuild=([A-Za-z0-9\-]+)", line)
                if mb:
                    log_build = mb.group(1)
    except Exception:
        pass

    cash = st.get("cash")
    # 账本反推（旧版回退口径）。资金池 = 现金 + 持仓市值；浮动 = 持仓市值 − 持仓成本：
    #   cost     = Σ entry_price × 持股数
    #   realized = cash + cost − 本金       （不依赖 nav，精确）
    #   float    = (资金池 − cash) − cost   （依赖 nav，日志 3 位小数 → ±50 元粒度）
    # 任一持仓缺 entry_price（账本不完整）则整体留空，不臆造。
    cost = realized = float_ = None
    if cash is not None:
        _cost = 0.0
        complete = True
        for c, v in holdings.items():
            v = float(v or 0)
            if v <= 0:
                continue
            px = entry.get(c)
            if px is None:
                complete = False
                break
            _cost += float(px) * v
        if complete:
            cost = round(_cost, 2)
            realized = round(float(cash) + cost - CAP_BASE, 2)

    capital = cap_native.get("capital")
    if capital is None:
        capital = round(CAP_BASE * nav, 2) if nav else None
    # 原生 realized/float 优先（精确到元），否则用反推值
    if cap_native.get("realized") is not None:
        realized = cap_native.get("realized")
    if cap_native.get("float_pnl") is not None:
        float_ = cap_native.get("float_pnl")
    elif capital is not None and cash is not None and cost is not None:
        float_ = round(float(capital) - float(cash) - cost, 2)

    mv = None
    if capital is not None and cash is not None:
        mv = round(float(capital) - float(cash), 2)

    # 对账视角（reconcile_v2_<date>.json）：交叉持仓（同账号他策略）/ 孤儿 / 异常
    cross = recon.get("cross_strategy") or []
    orphans = recon.get("orphans") or []
    issues = recon.get("issues") or []
    recon_txt = ""
    if recon:
        seg = []
        if cross:
            tag = "V1.3" if (recon.get("v13_hold_codes") or []) else "他策略"
            seg.append("交叉 %d只(%s)" % (len(cross), tag))
        if orphans:
            seg.append("孤儿 %d只" % len(orphans))
        seg.append("对账正常" if not issues else "对账异常 %d" % len(issues))
        recon_txt = " · ".join(seg)

    fills = _v2_fills(recon)
    buy, sell = _count(fills)
    pending = heart.get("pending_count")
    derived = False
    sb_hold = _sb_hold(mets, "V2") if mets else None
    sb_fills = _sb_fills(mets, "V2") if mets else None
    sb_build = _sb_build(mets, "V2") if mets else ""
    sb_hb = _sb_heartbeat(mets, "V2") if mets else ""
    sb_float = _sb_float(mets, "V2") if mets else None
    if sb_hold is not None:
        hold = sb_hold
    if sb_fills is not None:
        fills = sb_fills
        buy, sell = _count(fills)
    # 沙箱实时浮动优先（账户市值−成本；2026-10-16 截图核对：旧 nav 反推浮动失真）
    if sb_float is not None:
        float_ = sb_float
        mv, _c = _sb_money(mets, "V2")
        if realized is not None:
            capital = round(CAP_BASE + float(realized) + float(sb_float), 2)
            derived = "已实现(文件) + 浮动(账户实时)"
    return {
        "short": "价值小盘V2",
        "name": "价值小盘V2",
        "channel": "QMT单文件 · 67014907",
        "capital": capital,
        "realized": realized,
        "float": float_,
        "derived": derived if derived else
                   (cap_native.get("realized") is None and realized is not None),
        "cost": cost,
        "mv": mv,
        "hold": hold,
        "buy": buy, "sell": sell, "fills": fills,
        "pending": pending,
        "suspended": len(heart.get("suspended_sells") or []),
        "recon": recon_txt,
        "heartbeat": sb_hb or heart.get("last_heartbeat", "") or hb,
        "build": sb_build or heart.get("build_tag", "") or cap_native.get("build_tag", "") or log_build,
        "asof": cap_native.get("updated_at", "") or (heart.get("last_heartbeat", "") or hb),
        "cash": cash,
        "nav": nav if nav is not None else heart.get("nav"),
        "nav_peak": st.get("nav_peak"),
    }


def _load_bridge(cap_path, heart_dir, heart_prefix, recon_dir, recon_prefix,
                 date, name, channel, fills_path=None, short=None, sb_tag=None, m=None):
    cap = _read_json(cap_path) or {}
    heart = _heart(heart_dir, heart_prefix, date)
    realized = float_ = None
    m_re = re.search(r"realized=(-?\d+(?:\.\d+)?)\s+float=(-?\d+(?:\.\d+)?)",
                     str(cap.get("note", "") or ""))
    if m_re:
        realized = float(m_re.group(1))
        float_ = float(m_re.group(2))
    recon = _pick_json(recon_dir, recon_prefix, date)
    hold = len([c for c, v in (recon.get("ledger") or {}).items()
                if int(v or 0) > 0])
    fills = _bridge_fills(fills_path % date) if fills_path else []
    buy, sell = _count(fills)
    sb_hold = _sb_hold(m, sb_tag) if (m and sb_tag) else None
    sb_fills = _sb_fills(m, sb_tag) if (m and sb_tag) else None
    sb_build = _sb_build(m, sb_tag) if (m and sb_tag) else ""
    sb_hb = _sb_heartbeat(m, sb_tag) if (m and sb_tag) else ""
    sb_float = _sb_float(m, sb_tag) if (m and sb_tag) else None
    if sb_hold is not None:
        hold = sb_hold
    if sb_fills is not None:
        fills = sb_fills
        buy, sell = _count(fills)
    # 沙箱实时浮动优先（账户市值−成本；2026-10-16 截图核对：旧文件浮动失真）
    mv = None
    derived = False
    capital = cap.get("capital")
    # 仅在确有账户侧浮动数据时才用派生口径；第二实例无 xtquant 直连时
    # sb_float 恒为 0/None，必须保留 capital 文件原值，否则会凭空算出假回撤
    if sb_float is not None and _sb_account_ok(m, sb_tag) and _sb_cost_src(m, sb_tag) == "account":
        float_ = sb_float
        mv, _c = _sb_money(m, sb_tag)
        if realized is not None:
            capital = round(CAP_BASE + float(realized) + float(sb_float), 2)
            derived = "已实现(文件) + 浮动(账户实时)"
    return {
        "short": short or name,
        "name": name,
        "channel": channel,
        "capital": capital,
        "realized": realized,
        "float": float_,
        "derived": derived,
        "mv": mv,
        "hold": hold,
        "buy": buy, "sell": sell, "fills": fills,
        "heartbeat": sb_hb or heart.get("last_heartbeat", ""),
        "build": sb_build or heart.get("build_tag", ""),
        "asof": cap.get("updated_at", ""),
        "version_slice": _slice_txt(m, sb_tag) if sb_tag else "",
    }


# ---------------- 数值格式化 ----------------

def _fmt(x, nd=2):
    try:
        return ("%." + str(nd) + "f") % float(x)
    except (TypeError, ValueError):
        return "—"


def _money(x):
    try:
        return "¥{:,.0f}".format(float(x))
    except (TypeError, ValueError):
        return "—"


def _signed(x, nd=0):
    try:
        return ("{:+,." + str(nd) + "f}").format(float(x))
    except (TypeError, ValueError):
        return "—"


def _signed_pct(x, nd=2):
    """盈亏比例：金额 / 本金 10 万 × 100%。"""
    try:
        return ("{:+,." + str(nd) + "f}%").format(float(x) / CAP_BASE * 100)
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


def _signed_with_pct(x, nd=0):
    """「金额（比例）」组合，如 +1,234（+1.23%）；缺失返回 —。"""
    if x is None:
        return "—"
    return "%s（%s）" % (_signed(x, nd), _signed_pct(x))


def _cum_pct(capital):
    try:
        return (float(capital) / CAP_BASE - 1) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _color(cum):
    # A 股配色惯例（2026-10-14 全局统一）：红涨绿跌 —— 上涨/盈利=红，下跌/亏损=绿
    if cum is None:
        return "grey"
    if cum > 0.05:
        return "red"
    if cum < -0.05:
        return "green"
    return "grey"


# ---------------- 卡片构建 ----------------

def _trade_txt(s):
    """成交笔数行：买/卖来源缺失标「—」（与盘后总览同口径）。"""
    if s.get("buy") is None:
        return "—"
    return "%d笔（买%d/卖%d）" % (int(s["buy"]) + int(s["sell"]),
                                  int(s["buy"]), int(s["sell"]))


def _block(s):
    cum = _cum_pct(s.get("capital"))
    color = _color(cum)
    cum_txt = ("%+.2f%%" % cum) if cum is not None else "—"
    lines = [
        {"tag": "markdown",
         "content": "**%s**　<font color='grey'>%s</font>" % (s["name"], s["channel"])},
        {"tag": "markdown",
         "content": "资金池 **%s**　累计 <font color='%s'>%s</font>　持仓 %d只　成交 %s"
                    % (_money(s.get("capital")), color, cum_txt,
                       int(s.get("hold") or 0), _trade_txt(s))},
    ]
    parts = []
    if s.get("realized") is not None or s.get("float") is not None:
        parts.append("已实现 %s / 浮动 %s"
                     % (_signed_with_pct(s.get("realized")),
                        _signed_with_pct(s.get("float"))))
    if s.get("derived"):
        # 可为 True（默认「由账本反推」）或自定义字符串
        parts.append(s["derived"] if isinstance(s["derived"], str)
                     else "已实现/浮动由账本反推")
    if s.get("version_slice"):
        parts.append(s["version_slice"])
    if s.get("mv") is not None:
        parts.append("持仓市值 %s" % _money(s.get("mv")))
    if s.get("cash") is not None:
        parts.append("现金 %s" % _money(s.get("cash")))
    if s.get("nav") is not None:
        parts.append("nav %s（峰 %s）" % (_fmt(s.get("nav"), 4), _fmt(s.get("nav_peak"), 4)))
    if s.get("pending"):
        parts.append("待成交 %d" % int(s["pending"]))
    if s.get("suspended"):
        parts.append("暂缓卖出 %d" % int(s["suspended"]))
    if s.get("recon"):
        parts.append(s["recon"])
    if s.get("heartbeat"):
        parts.append("心跳 %s" % _hhmm(s.get("heartbeat")))
    if s.get("build"):
        parts.append("build %s" % s["build"])
    if parts:
        lines.append({"tag": "markdown", "text_size": "notation",
                      "content": "<font color='grey'>%s</font>" % " · ".join(parts)})
    return {
        "tag": "interactive_container", "width": "fill", "has_border": True,
        "border_color": "%s-100" % color, "background_style": "%s-50" % color,
        "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
        "margin": "0px 0px 10px 0px", "elements": lines,
    }


def _fills_table(strategies):
    """汇总当日所有成交（跨策略）到一张表。无成交返回 None。"""
    rows = []
    for s in strategies:
        for f in s.get("fills") or []:
            act = "买" if f.get("action") == "BUY" else (
                "卖" if f.get("action") == "SELL" else (f.get("action") or "—"))
            rows.append("| %s | %s | %s | %s | %s | %s |" % (
                s.get("short") or s.get("name") or "", act, f.get("code", ""),
                _fmt(f.get("vol"), 0), _fmt(f.get("price")), f.get("status", "")))
    if not rows:
        return None
    head = "| 策略 | 方向 | 代码 | 数量 | 价格 | 状态 |"
    sep = "|---|---|---|---|---|---|"
    return "\n".join([head, sep] + rows)


def _build_card(strategies, date):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    dep = [s for s in strategies if s.get("capital") is not None]
    tot_cap = sum(float(s["capital"]) for s in dep)
    tot_base = CAP_BASE * len(dep)
    tot_cum = ((tot_cap / tot_base - 1) * 100) if tot_base else None
    tot_color = _color(tot_cum)
    tot_pct = ("%+.2f%%" % tot_cum) if tot_cum is not None else "—"

    elements = [
        {"tag": "markdown",
         "content": "已部署 **%d** 个策略　合计资金池 **%s**　综合累计 "
                    "<font color='%s'>%s</font>"
                    % (len(dep), _money(tot_cap), tot_color, tot_pct)},
    ]
    for s in dep:
        elements.append(_block(s))
    tbl = _fills_table(strategies)
    if tbl:
        elements.append({"tag": "markdown", "content": "**今日成交明细**"})
        elements.append({"tag": "markdown", "content": tbl})
    elements.append({"tag": "markdown", "text_size": "notation",
                     "content": "<font color='grey'>未部署：%s</font>" % " · ".join(NOT_DEPLOYED)})
    elements.append({"tag": "markdown", "text_size": "notation", "text_align": "center",
                     "content": "<font color='grey'>数据现读 %s · 累计收益=(资金池-10万)/10万 · 仅供内部复盘</font>" % ts})

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "各策略经营指标 · %s-%s-%s" % (date[:4], date[4:6], date[6:8])},
            "subtitle": {"tag": "plain_text", "content": "%s · %s" % (
                ts, " + ".join(s.get("short") or s.get("name") for s in dep))},
            "template": "indigo",
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag",
                               "text": {"tag": "plain_text", "content": "经营指标"},
                               "color": "indigo"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="各策略经营指标卡片推送")
    ap.add_argument("--date", default=time.strftime("%Y%m%d"))
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--no-sandbox", action="store_true",
                    help="跳过沙箱外采集，只用旧文件源（排查用）")
    args = ap.parse_args()

    m = {} if args.no_sandbox else _sandbox_metrics(args.date)
    if m:
        print("[SANDBOX] 已接入沙箱外数据源（xtquant + FormulaOutput），账号 %d/%d 连通"
              % (sum(1 for a in (m.get("accounts") or []) if a.get("connected")),
                 len(m.get("accounts") or [])))
    else:
        print("[SANDBOX] 沙箱外数据源不可用，全部退回旧文件源")

    strategies = [
        _load_v13(args.date, m=m),
        _load_v2(args.date, mets=m),
        _load_bridge(G2_CAP, G2_HEART_DIR, "heart_", G2_RECON_DIR, "reconcile_g2_",
                     args.date, "G2 内产版", "QMT内置 · 70180771",
                     fills_path=G2_FILLS, short="G2I", sb_tag="G2I", m=m),
        _load_bridge(ENS_CAP, ENS_HEART_DIR, "heart_", ENS_RECON_DIR, "reconcile_g2_ens_",
                     args.date, "融合内产版", "QMT内置 · 70180771",
                     fills_path=ENS_FILLS, short="ENSI", sb_tag="ENSI", m=m),
    ]
    for s in strategies:
        cum = _cum_pct(s.get("capital"))
        print("[DATA] %-18s 资金池=%s 累计=%s 已实现=%s 浮动=%s 持仓=%s 成交=%s 心跳=%s build=%s"
              % (s["name"], _money(s.get("capital")),
                 ("%+.2f%%" % cum) if cum is not None else "—",
                 _signed_with_pct(s.get("realized")), _signed_with_pct(s.get("float")),
                 s.get("hold"), _trade_txt(s),
                 _hhmm(s.get("heartbeat")) or "—", s.get("build") or "—"))
        if s.get("version_slice"):
            print("[SLICE] %-18s %s" % (s["name"], s["version_slice"]))

    card = _build_card(strategies, args.date)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → 各策略经营指标" % ("成功" if ok else "失败"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
