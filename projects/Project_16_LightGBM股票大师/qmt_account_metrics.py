# coding: utf-8
"""沙箱外账户指标采集器（T-20261016-001）：xtquant 只读直连双账号 → remark 归属 → 留档 + 归一化 JSON。

背景（QMT 沙箱自闭环改造，specs/QMT沙箱自闭环改造方案_20261015.md）：
  策略进程不再回写 D:/QMT_POOL（heart/fills/positions/capital json 全部废止），
  「各策略经营指标」卡片若继续依赖策略回写文件将断供。
  本脚本为沙箱外旁路（只读账户 + 只读日志，策略进程零交互，符合 §3.4 豁免口径），
  为 push_strategy_metrics_card.py 提供不依赖策略写盘的成交/持仓/build/心跳数据。

数据源（全部沙箱外）：
  1) xtquant 直连 67014907 / 70180771（同 reconcile_v2 先例）：
     - query_stock_asset      → 账户 cash / market_value / total_asset（账户级，共享现金不拆策略）
     - query_stock_positions  → 持仓（volume/open_price/market_value，无 remark → 按 code 集合归属）
     - query_stock_trades     → 当日成交（m_strOptName 中文方向 / m_strRemark → 策略归属）
     - query_stock_orders     → 当日委托（order_remark → 策略归属）
  2) FormulaOutput 日志（UTF-8）：D:\\国金QMT交易端模拟\\userdata\\log\\XtClient_FormulaOutput_<date>.log
     → 策略 print 行抽取 BUILD_TAG / build= / [HEART] / [hb]（心跳、版本核对）。

归属规则（deal/order 按 remark；position 按 code 集合，文件仅作过渡期辅助）：
  V2  : remark 含 "V2"        | code ∈ v2_holdings_state.json holdings
  V13 : remark 含 "P16V13"/"planA"/"order_guard"/"traework" | code ∈ qmt_trade_log.csv FIFO 净额>0
  G2  : remark 含 "P16G2I"/"P16G2"     | code ∈ reconcile_g2 ledger
  ENS : remark 含 "P16ENSI"/"P16ENS"   | code ∈ reconcile_g2_ens ledger
  ATR : remark 含 "ATR"       （暂无持仓归属辅助文件）
  其余 → unattributed（如实列出，不臆造归属）

局限（如实标注，不掩盖）：
  - 资金池/已实现/浮动仍是策略级口径，账户 cash 为共享资金无法拆分 → capital/realized/float
    继续由 push_strategy_metrics_card 读旧文件（策略停写后为最后落盘值，asof 可见）。
  - 模拟端 deal/order 仅当日 → 跨日统计依赖本脚本每日留档（ARCHIVE_DIR）。
  - 持仓归属依赖过渡期文件（state/reconcile/trade log），文件停更后 code 集合冻结 →
    归属仍可工作（旧 code 集合 ∩ 当前账户持仓），但新买入若无 remark 归属会进 unattributed。

产物：
  PROJECT/data/account_metrics/qmt_account_metrics_<date>.json（按日留档，增量覆盖同日重跑）

用法：
  python qmt_account_metrics.py                    # 采集当日并留档
  python qmt_account_metrics.py --date 20261016
  python qmt_account_metrics.py --dry-run          # 只打印不落盘
退出码：0=成功（含部分账号失败，失败账号 connected=false）；1=全部账号失败。
"""
import argparse
import csv
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
POOL = r"D:/QMT_POOL"
ARCHIVE_DIR = os.path.join(PROJECT, "data", "account_metrics")
BASELINE_PATH = os.path.join(ARCHIVE_DIR, "version_baseline.json")

V2_STATE = os.path.join(POOL, "v2_holdings_state.json")
V13_TRADE_LOG = os.path.join(PROJECT, "data", "qmt_trade_log.csv")
G2_RECON_DIR = os.path.join(PROJECT, "data", "reconcile_g2")
ENS_RECON_DIR = os.path.join(PROJECT, "data", "reconcile_g2_ens")
G2_CAP = os.path.join(POOL, "g2_bridge", "g2_strategy_capital.json")
ENS_CAP = os.path.join(POOL, "p16_ensemble_bridge", "ens_strategy_capital.json")

# ---------------- 多实例配置（2026-10-19 立，T-20261019-003） ----------------
# 两个 QMT 实例各自独立：账号 67014907 跑 V13/V2，账号 70180771 跑 G2/ENS。
# 关键：**日志必须按实例分别读** —— 此前只读主实例日志，导致 G2/ENS 的
# BUILD_TAG / 心跳 / [HOLD] 全部解析不到（被误判为"未加载"）。
# 交易通道：两账号均可从主实例 xtquant 连上（实测 connected=True）；
# 第二实例的 userdata_mini 连不上（CONNECT_RC=-1），故 xtquant 统一走主实例，
# 仅日志解析按实例分流。
INSTANCES = [
    {
        "key": "main",
        "name": "主实例",
        "xtpack": r"D:\国金QMT交易端模拟\bin.x64\Lib\site-packages",
        "userdata": r"D:\国金QMT交易端模拟\userdata_mini",
        "log_dir": r"D:\国金QMT交易端模拟\userdata\log",
        "accounts": ["67014907"],
    },
    {
        "key": "second",
        "name": "第二实例",
        "xtpack": r"D:\QMT交易端模拟\bin.x64\Lib\site-packages",
        "userdata": r"D:\QMT交易端模拟\userdata_mini",
        "log_dir": r"D:\QMT交易端模拟\userdata\log",
        "accounts": ["70180771"],
        # 第二实例交易通道不可直连 → 账户查询回退到主实例（已验证可查 70180771）
        "query_via": "main",
    },
]

# xtquant 连接统一走主实例（已验证 67014907 / 70180771 均可连）
XTPACK = INSTANCES[0]["xtpack"]
USERDATA = INSTANCES[0]["userdata"]

ACCOUNTS = ["67014907", "70180771"]

# 账号 → 实例 key
ACCOUNT_INSTANCE = {}
for _inst in INSTANCES:
    for _a in _inst["accounts"]:
        ACCOUNT_INSTANCE[_a] = _inst["key"]

# 账号 → 活跃策略标签
ACCOUNT_STRATEGIES = {
    "67014907": ["V13", "V2"],
    "70180771": ["G2I", "ENSI"],
}

# remark → 策略归属（按序首中即停；V2 在前防 "V2" 误吞其他前缀）
# 2026-10-20 改名（T-20261020-004）：G2/ENS 下单 remark 改为 "P16G2I桥" / "P16ENSI桥"。
# ⚠️ 顺序敏感：新值 P16G2I/P16ENSI 必须排在旧值 P16G2/P16ENS 之前——子串匹配下
#    "P16G2I桥" 也含 "P16G2"，若旧值在前会把新单误判为旧版（虽然归到同一标签 G2I/ENSI，
#    但语义上应显式区分）。旧值保留以兼容改名前的历史成交。
REMARK_MAP = [
    ("V2", "V2"),
    ("V13", "P16V13"),
    ("V13", "planA"),
    ("V13", "order_guard"),
    ("V13", "traework"),
    ("G2I", "P16G2I"),
    ("G2I", "P16G2"),
    ("ENSI", "P16ENSI"),
    ("ENSI", "P16ENS"),
    ("ATR", "ATR"),
]
# 策略标签（统计口径）。G2I/ENSI = G2/ENS 信号内产版（2026-10-19 起独立统计）；
# G2/ENS 保留标签名但不再产出新数据（文件桥版历史）。
STRATEGY_TAGS = ["V13", "V2", "G2I", "ENSI"]

STRATEGY_LABEL = {
    "V13": "V1.3 风控",
    "V2": "价值小盘V2",
    "G2I": "G2 内产版",
    "ENSI": "融合内产版",
}

# 内产版认定：BUILD_TAG >= 该值 = 信号内产版（G2 235317 / ENS 235319，
# 由 T-20261017-003 重建；此前为纯风控版 20261016-0834xx 及文件桥版）
INPROD_BUILD_PREFIXES = ("20261017-2353", "20261017-235319", "20261017-235317")

# FormulaOutput reqid 文件名 → 策略标签
FILE_TAG_MAP = [
    ("STRATEGY_V2", "V2"),
    ("P16_V13_RISK", "V13"),
    ("p16_g2_bridge", "G2I"),
    ("p16_ens_bridge", "ENSI"),
    ("strategy_atr", "ATR"),
]

_RE_INFO = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+\[INFO\].*?, output = (.*)$")
_RE_BARE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\[.*\].*)$")
_RE_BUILD = re.compile(r"BUILD_TAG=([0-9\-]+)|\bbuild=([0-9A-Za-z\-_]+)")
_RE_HEART = re.compile(
    r"\[(?:HEART|hb)\].*?(?:持仓|holdings|positions)[=\s]*(\d+)"
    r"(?:.*?(?:cash|现金)[=\s]*([0-9.\-]+))?", re.I)
_RE_TAG = re.compile(r"\[(P16V13|P16G2I|P16G2|P16ENSI|P16ENS|V2|ATR[A-Z0-9_]*)\]")
# 日志内策略标签 → 统计标签（G2/ENS 信号内产版独立统计，2026-10-19）
# 2026-10-20 改名（T-20261020-004）：新前缀 P16G2I/P16ENSI 与旧 P16G2/P16ENS 归到同一统计标签。
_LOG_TAG_NORM = {
    "P16G2I": "G2I",
    "P16G2": "G2I",
    "P16ENSI": "ENSI",
    "P16ENS": "ENSI",
}
_RE_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
# [HOLD] 行：n=<N> CODE=vol@cost ...（T-20261017-003 新增，自闭环后持仓唯一旁路源）
_RE_HOLD = re.compile(r"\[HOLD\]\s+\S+\s+\S+\s+n=(\d+)\s*(.*)$")
_RE_HOLD_ITEM = re.compile(r"([0-9]{6}\.[A-Z]{2})=(\d+)@([0-9.]+)")


def _norm_code(code):
    return str(code or "").strip().split(".")[0]


def _attr_by_remark(remark):
    rk = str(remark or "")
    for tag, key in REMARK_MAP:
        if key in rk:
            return tag
    return None


# ---------------- 过渡期持仓归属辅助（code 集合） ----------------

def _v2_hold_codes():
    if not os.path.exists(V2_STATE):
        return set()
    try:
        with open(V2_STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return set()
    codes = set()
    for k, v in (st.get("holdings") or {}).items():
        c = _norm_code(k)
        if c and float(v or 0) > 0:
            codes.add(c)
    return codes


def _v13_hold_codes(account_id="67014907"):
    """从 V1.3 成交日志 FIFO 净额>0 推导持仓 code（与 reconcile_v2._v13_hold_codes 同口径）。"""
    codes = set()
    if not os.path.exists(V13_TRADE_LOG):
        return codes
    net = {}
    try:
        with open(V13_TRADE_LOG, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if str(row.get("account_id", "")).strip() != account_id:
                    continue
                code = _norm_code(row.get("code", ""))
                side = str(row.get("side", "")).strip().upper()
                try:
                    vol = int(float(row.get("vol", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if not code or vol <= 0:
                    continue
                net[code] = net.get(code, 0) + (vol if side == "BUY" else -vol)
    except Exception:
        return codes
    for c, v in net.items():
        if v > 0:
            codes.add(c)
    return codes


def _recon_ledger_codes(d, prefix):
    """取 reconcile 目录下最新一份的 ledger code 集合（vol>0）。"""
    try:
        files = sorted(f for f in os.listdir(d)
                       if f.startswith(prefix) and f.endswith(".json"))
    except Exception:
        files = []
    if not files:
        return set()
    try:
        with open(os.path.join(d, files[-1]), encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:
        return set()
    codes = set()
    for k, v in (rec.get("ledger") or {}).items():
        c = _norm_code(k)
        if c and float(v or 0) > 0:
            codes.add(c)
    return codes


# ---------------- xtquant 连接与查询 ----------------

# query_stock_trades 非交易时段会无限阻塞（order_guard T-20261008-008 实证：2026-10-08 收盘后
# 卡死 8 小时）。本采集脚本由 15:40 盘后定时任务调用，裸调会让整个任务挂死
# （2026-10-16 实证：15:40 采集未落盘、无 de_metrics_card_*.pushed 标记，仅 11:47 人工留档）。
# 包守护线程 + join 超时，超时按「成交明细不可用」降级返回 None，不影响资产/持仓/委托三项主数据。
_TRADES_QUERY_TIMEOUT = 10


def _query_trades_with_timeout(trader, account, timeout=_TRADES_QUERY_TIMEOUT):
    """query_stock_trades 超时包裹（同 order_guard.py 已审计实现）。

    超时/空返回 None；异常原样抛出交调用方 except 分支处理。
    """
    import threading

    box = []

    def _worker():
        try:
            box.append(trader.query_stock_trades(account))
        except Exception as e:
            box.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    if not box:
        return None
    r = box[0]
    if isinstance(r, Exception):
        raise r
    return r


def _query_account(acct_id):
    """连接单账号，返回归一化 dict；连接失败 connected=false。"""
    out = {"account_id": acct_id, "connected": False,
           "asset": None, "positions": [], "deals": [], "orders": [],
           "error": ""}
    try:
        if XTPACK not in sys.path:
            sys.path.append(XTPACK)
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import StockAccount
    except Exception as e:
        out["error"] = "import xtquant 失败: %r" % (e,)
        return out
    trader = None
    try:
        trader = XtQuantTrader(USERDATA, int(time.time()))
        try:
            trader.register_callback(XtQuantTraderCallback())
        except Exception:
            pass
        trader.start()
        rc = trader.connect()
        if rc != 0:
            out["error"] = "connect rc=%s" % rc
            return out
        acc = StockAccount(acct_id)
        trader.subscribe(acc)
        time.sleep(2)
        out["connected"] = True

        try:
            a = trader.query_stock_asset(acc)
            if a:
                out["asset"] = {
                    "cash": float(getattr(a, "m_dCash", 0) or 0),
                    "frozen_cash": float(getattr(a, "m_dFrozenCash", 0) or 0),
                    "market_value": float(getattr(a, "m_dMarketValue", 0) or 0),
                    "total_asset": float(getattr(a, "m_dTotalAsset", 0) or 0),
                }
        except Exception as e:
            out["error"] += " asset:%r" % (e,)

        try:
            for p in (trader.query_stock_positions(acc) or []):
                vol = int(getattr(p, "m_nVolume", 0) or 0)
                code = str(getattr(p, "m_strStockCode", "") or "").strip()
                if not code or vol <= 0:
                    continue
                out["positions"].append({
                    "code": code,
                    "vol": vol,
                    "can_use": int(getattr(p, "m_nCanUseVolume", 0) or 0),
                    "open_price": float(getattr(p, "m_dOpenPrice", 0) or 0),
                    "market_value": float(getattr(p, "m_dMarketValue", 0) or 0),
                })
        except Exception as e:
            out["error"] += " positions:%r" % (e,)

        try:
            for d in (_query_trades_with_timeout(trader, acc) or []):
                vol = float(getattr(d, "m_nVolume", 0) or 0)
                price = float(getattr(d, "m_nPrice", 0) or 0)
                if vol <= 0:
                    continue
                opt = str(getattr(d, "m_strOptName", "") or "")
                remark = str(getattr(d, "m_strRemark", "") or "")
                out["deals"].append({
                    "code": str(getattr(d, "m_strSecurityCode", "")
                                or getattr(d, "m_strInstrumentID", "") or "").strip(),
                    "action": "BUY" if "买入" in opt else ("SELL" if "卖出" in opt else opt),
                    "vol": int(vol),
                    "price": price,
                    "time": str(getattr(d, "m_strTradeTime", "")
                                or getattr(d, "m_strOrderTime", "") or ""),
                    "remark": remark,
                    "strategy": _attr_by_remark(remark) or "unattributed",
                })
        except Exception as e:
            out["error"] += " deals:%r" % (e,)

        try:
            for o in (trader.query_stock_orders(acc) or []):
                remark = str(getattr(o, "m_strRemark", "") or
                             getattr(o, "order_remark", "") or "")
                out["orders"].append({
                    "code": str(getattr(o, "m_strStockCode", "") or
                                getattr(o, "stock_code", "") or "").strip(),
                    "order_id": str(getattr(o, "m_nOrderID", "") or
                                    getattr(o, "order_id", "") or ""),
                    "order_vol": int(getattr(o, "m_nOrderVolume", 0) or 0),
                    "traded_vol": int(getattr(o, "m_nTradedVolume", 0) or 0),
                    "price": float(getattr(o, "m_dPrice", 0) or 0),
                    "status": str(getattr(o, "m_nOrderStatus", "") or
                                  getattr(o, "order_status", "") or ""),
                    "remark": remark,
                    "strategy": _attr_by_remark(remark) or "unattributed",
                })
        except Exception as e:
            out["error"] += " orders:%r" % (e,)
    except Exception as e:
        out["error"] += " fatal:%r" % (e,)
    finally:
        if trader is not None:
            try:
                trader.stop()
            except Exception:
                pass
    return out


# ---------------- FormulaOutput 日志解析 ----------------

def _tag_from_filename(text):
    for key, tag in FILE_TAG_MAP:
        if key in text:
            return tag
    return None


def parse_formula_log(date, log_dir=None):
    """解析当日 XtClient_FormulaOutput_<date>.log（UTF-8），抽 build / 心跳。

    log_dir 缺省用主实例目录；多实例场景由 parse_all_logs 逐实例调用。
    """
    path = os.path.join(log_dir or INSTANCES[0]["log_dir"],
                        "XtClient_FormulaOutput_%s.log" % date)
    out = {"path": path, "exists": os.path.exists(path),
           "builds": {}, "hearts": {}, "forbidden_io": 0}
    if not out["exists"]:
        return out
    contents = []  # (ts, content, file_tag)
    cur = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line.strip():
                    continue
                m = _RE_INFO.match(line)
                if m:
                    if cur:
                        contents.append(cur)
                    cur = [m.group(1), m.group(2), _tag_from_filename(line)]
                    continue
                m = _RE_BARE.match(line)
                if m:
                    if cur:
                        contents.append(cur)
                    cur = [m.group(1), m.group(2), None]
                    continue
                if cur is not None and not _RE_TS.match(line):
                    cur[1] += "\n" + line  # 同一次 print 的多行续行
                    continue
                if cur:
                    contents.append(cur)
                cur = None
    except Exception as e:
        out["error"] = "read log fail: %r" % (e,)
        return out
    if cur:
        contents.append(cur)

    for ts, content, ftag in contents:
        if "Foribdden" in content:
            out["forbidden_io"] += 1
        # 标签归一：日志内 tag（[P16G2]/[P16ENS]）→ 统计标签（G2I/ENSI）
        _m_tag = _RE_TAG.search(content)
        tag = ftag or (_LOG_TAG_NORM.get(_m_tag.group(1)) if _m_tag else None)
        if tag is None and _m_tag:
            tag = _m_tag.group(1)
        mb = _RE_BUILD.search(content)
        if mb and tag:
            build = mb.group(1) or mb.group(2)
            if build:
                out["builds"][tag] = build
        mh = _RE_HEART.search(content)
        if mh and tag:
            try:
                hold = int(mh.group(1))
            except (TypeError, ValueError):
                hold = None
            try:
                cash = float(mh.group(2)) if mh.group(2) else None
            except (TypeError, ValueError):
                cash = None
            out["hearts"][tag] = {"ts": ts, "hold": hold, "cash": cash,
                                  "raw": content[:200]}
        mhd = _RE_HOLD.search(content)
        if mhd and tag:
            items = []
            for m in _RE_HOLD_ITEM.finditer(mhd.group(2)):
                items.append({"code": m.group(1), "vol": int(m.group(2)),
                              "cost": float(m.group(3))})
            out.setdefault("holds", {})[tag] = {"ts": ts, "n": int(mhd.group(1)),
                                                "items": items}
    return out


# ---------------- 归属聚合 ----------------

def parse_all_logs(date):
    """逐实例解析 FormulaOutput 日志，合并为统一结构（多实例，T-20261019-003）。

    返回 {path, exists, builds, hearts, forbidden_io, by_instance:{key:{...}}, holds:{tag:[...]}}。
    builds/hearts 以策略标签为键直接合并（两实例策略标签不重叠，无冲突）。
    """
    merged = {"path": "", "exists": False, "builds": {}, "hearts": {},
              "forbidden_io": 0, "by_instance": {}, "holds": {}}
    paths = []
    for inst in INSTANCES:
        one = parse_formula_log(date, inst["log_dir"])
        merged["by_instance"][inst["key"]] = {
            "name": inst["name"], "log_dir": inst["log_dir"],
            "path": one["path"], "exists": one["exists"],
            "builds": one.get("builds") or {},
            "hearts": one.get("hearts") or {},
            "forbidden_io": one.get("forbidden_io", 0),
        }
        if one["exists"]:
            merged["exists"] = True
            paths.append(one["path"])
        merged["forbidden_io"] += one.get("forbidden_io", 0)
        merged["builds"].update(one.get("builds") or {})
        merged["hearts"].update(one.get("hearts") or {})
        holds = one.get("holds") or {}
        if holds:
            merged["holds"].update(holds)
    merged["path"] = " | ".join(paths)
    return merged


def _is_inprod_build(build):
    """BUILD_TAG 是否属于信号内产版（>= 20261017-2353xx）。"""
    b = str(build or "")
    return any(b.startswith(p) for p in INPROD_BUILD_PREFIXES)


def load_baseline():
    """读版本切片基线（version_baseline.json）。缺失返回 {}。"""
    try:
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def slice_versions(metrics):
    """版本切片：按 BUILD_TAG 判定当前跑的是内产版还是文件桥版，
    并给出「内产版业绩」的独立口径（内产版基线 → 当前）。

    基线文件结构：
      {"generated_at": ..., "baseline_date": "20261019",
       "G2I": {"capital": 95399.82, "date": "20261019", "source": "..."},
       "ENSI": {...}}
    """
    base = load_baseline()
    out = {"inprod_build": {}, "legacy_build": {}, "slice": {}}
    builds = (metrics.get("log") or {}).get("builds") or {}
    for tag in ("G2I", "ENSI"):
        b = builds.get(tag) or ""
        if not b:
            continue
        if _is_inprod_build(b):
            out["inprod_build"][tag] = b
        else:
            out["legacy_build"][tag] = b
    for tag in ("G2I", "ENSI"):
        b0 = base.get(tag) or {}
        cur = (metrics.get("strategies") or {}).get(tag) or {}
        capital = cur.get("capital")
        start = b0.get("capital")
        s = {
            "version": "inprod" if tag in out["inprod_build"] else "legacy",
            "build": builds.get(tag) or "",
            "baseline_date": b0.get("date"),
            "baseline_capital": start,
            "current_capital": capital,
            "pnl_since_baseline": None,
            "return_since_baseline": None,
        }
        if capital is not None and start:
            try:
                pnl = float(capital) - float(start)
                s["pnl_since_baseline"] = round(pnl, 2)
                s["return_since_baseline"] = round(pnl / float(start), 6)
            except (TypeError, ValueError):
                pass
        out["slice"][tag] = s
    return out


def _deal_history_tags(date=None):
    """从历史留档 + 当日成交回溯 code→strategy（新买入归属兜底）。

    背景：V2 自闭环后 v2_holdings_state.json 停写、V1.3 停写 trade log →
    新买入的 code 不在任何旧集合里会进 unattributed（hold 数不涨）。
    兜底：deal 带 remark（下单时策略自己标的），按日留档累积 code→strategy 映射，
    越新的归属覆盖旧的（换仓后 code 可能易主）。date 及之后的当日 deal 由调用方另行并入。
    """
    mapping = {}  # code6位 -> strategy tag
    try:
        files = sorted(f for f in os.listdir(ARCHIVE_DIR)
                       if f.startswith("qmt_account_metrics_") and f.endswith(".json"))
    except Exception:
        files = []
    for fn in files:
        d8 = fn[len("qmt_account_metrics_"): -len(".json")]
        if date and d8 >= date:
            continue  # 当日档会被本次 collect 覆盖，不回读（当日 deal 由 accounts 并入）
        try:
            with open(os.path.join(ARCHIVE_DIR, fn), encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        for acct in (m.get("accounts") or []):
            for d in (acct.get("deals") or []):
                c = _norm_code(d.get("code"))
                tag = d.get("strategy")
                if c and tag and tag != "unattributed":
                    mapping[c] = tag
    return mapping


def _attributize(account, hist_tags=None, holds=None):
    """按 code 集合给持仓打策略标签（remark 不适用于 position）。

    归属优先级（2026-10-19 多实例改造）：
      [HOLD] 日志旁路（自闭环后唯一活状态源，最高可信）
      > 旧账本集合（存量真相）
      > 成交历史回溯（新买入兜底）
      > unattributed。
    """
    acct = account["account_id"]
    sets = {}
    if acct == "67014907":
        v2 = _v2_hold_codes()
        v13 = _v13_hold_codes(acct)
        sets = {"V2": v2, "V13": v13}
    elif acct == "70180771":
        # 内产版独立标签；账本仅作历史存量兜底
        sets = {"G2I": _recon_ledger_codes(G2_RECON_DIR, "reconcile_g2_"),
                "ENSI": _recon_ledger_codes(ENS_RECON_DIR, "reconcile_g2_ens_")}
    # [HOLD] 日志旁路（第二实例无 xtquant 直连时的持仓真相）
    hold_sets = {}
    for t, h in (holds or {}).items():
        hold_sets[t] = set(_norm_code(i["code"]) for i in (h.get("items") or []))
    # 当日成交也参与回溯（当天新买当天就能归属）
    live_tags = dict(hist_tags or {})
    for d in (account.get("deals") or []):
        c = _norm_code(d.get("code"))
        tag = d.get("strategy")
        if c and tag and tag != "unattributed":
            live_tags[c] = tag
    for p in account["positions"]:
        c = _norm_code(p["code"])
        # 实时浮动（账户真相）：市值 − 成本(open_price)×持股数
        try:
            _mv = float(p.get("market_value") or 0)
            _cost = float(p.get("open_price") or 0) * int(p.get("vol") or 0)
        except (TypeError, ValueError):
            _mv = _cost = 0.0
        p["cost"] = round(_cost, 2)
        p["float_pnl"] = round(_mv - _cost, 2)
        tag = None
        for t in STRATEGY_TAGS:  # 顺序即优先级
            if t in hold_sets and c in hold_sets[t]:
                tag = t
                p["strategy_src"] = "log_hold"
                break
        if tag is None:
            for t in STRATEGY_TAGS:
                if t in sets and c in sets[t]:
                    tag = t
                    p["strategy_src"] = "ledger"
                    break
        if tag is None:
            tag = live_tags.get(c)  # 新买入兜底：成交历史回溯
            if tag:
                p["strategy_src"] = "deal_history"
        p["strategy"] = tag or "unattributed"
    return account


def _merge_strategies(accounts, log):
    """跨账号聚合成 strategies.{tag}，供卡片消费。"""
    strat = {}
    for t in STRATEGY_TAGS:
        strat[t] = {"fills": [], "orders": [], "positions": [],
                    "buy": 0, "sell": 0, "hold": None,
                    "build": (log.get("builds") or {}).get(t, ""),
                    "heart": (log.get("hearts") or {}).get(t)}
    un_deals, un_pos = [], []
    for acct in accounts:
        for d in acct.get("deals") or []:
            tag = d.get("strategy")
            if tag in strat:
                strat[tag]["fills"].append(d)
            else:
                un_deals.append(d)
        for o in acct.get("orders") or []:
            tag = o.get("strategy")
            if tag in strat:
                strat[tag]["orders"].append(o)
        for p in acct.get("positions") or []:
            tag = p.get("strategy")
            if tag in strat:
                strat[tag]["positions"].append(p)
            else:
                un_pos.append(p)
    for t, s in strat.items():
        s["buy"] = sum(1 for f in s["fills"] if f.get("action") == "BUY")
        s["sell"] = sum(1 for f in s["fills"] if f.get("action") == "SELL")
        # 资金池：G2I/ENSI 读策略 capital 文件（reconcile 滚动口径，与基线同源）
        cap_path = {"G2I": G2_CAP, "ENSI": ENS_CAP}.get(t)
        if cap_path:
            try:
                with open(cap_path, encoding="utf-8") as f:
                    s["capital"] = json.load(f).get("capital")
            except Exception:
                s["capital"] = None
        if s["positions"]:
            s["hold"] = len(s["positions"])
            s["cost"] = round(sum(float(p.get("cost") or 0) for p in s["positions"]), 2)
            s["float_pnl"] = round(sum(float(p.get("float_pnl") or 0)
                                       for p in s["positions"]), 2)
            s["market_value"] = round(sum(float(p.get("market_value") or 0)
                                          for p in s["positions"]), 2)
            s["cost_src"] = "account"
        else:
            s["cost"] = 0.0
            s["float_pnl"] = 0.0
            s["market_value"] = 0.0
        # 账户持仓为空（如第二实例无 xtquant 直连：70180771 查询恒返回空列表）时，
        # 退回日志 [HOLD] 计数；浮盈/市值无来源留 None（不写 0，避免伪造"零回撤"）
        if s["hold"] is None:
            h = (log.get("holds") or {}).get(t)
            if h is not None:
                s["hold"] = h.get("n", 0)
                s["hold_src"] = "log_hold"
                s["cost"] = None
                s["float_pnl"] = None
                s["market_value"] = None
                s["cost_src"] = "unavailable"
    return strat, un_deals, un_pos


# ---------------- 主入口 ----------------

def collect(date=None, dry_run=False, write=True):
    """采集双实例 + 双账号 + 双实例日志 → 归一化 metrics dict，并按日留档。"""
    date = date or time.strftime("%Y%m%d")
    hist_tags = _deal_history_tags(date)  # 历史留档成交回溯（新买入归属兜底）
    log = parse_all_logs(date)            # 多实例日志（主实例 + 第二实例）
    holds = log.get("holds") or {}
    accounts = []
    for a in ACCOUNTS:
        acc = _attributize(_query_account(a), hist_tags, holds=holds)
        acc["instance"] = ACCOUNT_INSTANCE.get(a, "")
        accounts.append(acc)
        print("[QMT] %s(%s) connected=%s pos=%d deals=%d orders=%d %s"
              % (a, acc["instance"], acc["connected"], len(acc["positions"]),
                 len(acc["deals"]), len(acc["orders"]), acc.get("error") or ""))
    for key, inst in (log.get("by_instance") or {}).items():
        print("[LOG] [%s] %s exists=%s builds=%s hearts=%d Foribdden行=%d"
              % (inst["name"], os.path.basename(inst["path"]), inst["exists"],
                 inst.get("builds") or {}, len(inst.get("hearts") or {}),
                 inst.get("forbidden_io", 0)))
    if holds:
        print("[HOLD] 日志旁路持仓: %s"
              % ", ".join("%s=%d" % (t, h.get("n", 0)) for t, h in holds.items()))
    strat, un_deals, un_pos = _merge_strategies(accounts, log)
    for t in STRATEGY_TAGS:
        s = strat[t]
        print("[STRAT] %-4s %-8s hold=%s 买%d/卖%d build=%s"
              % (t, STRATEGY_LABEL.get(t, ""), s["hold"], s["buy"], s["sell"],
                 s["build"] or "—"))
    if un_deals or un_pos:
        print("[ATTR] 未归属 deal=%d pos=%d（如实列出，不臆造）"
              % (len(un_deals), len(un_pos)))

    metrics = {
        "date": date,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "xtquant sandbox-external read-only + multi-instance FormulaOutput log",
        "instances": [{"key": i["key"], "name": i["name"],
                       "log_dir": i["log_dir"], "accounts": i["accounts"],
                       "query_via": i.get("query_via", i["key"])} for i in INSTANCES],
        "account_strategies": ACCOUNT_STRATEGIES,
        "strategy_label": STRATEGY_LABEL,
        "accounts": accounts,
        "strategies": strat,
        "log": log,
        "unattributed": {"deals": un_deals, "positions": un_pos},
    }
    metrics["version_slice"] = slice_versions(metrics)
    vs = metrics["version_slice"]
    for t, s in (vs.get("slice") or {}).items():
        print("[SLICE] %-4s version=%-7s build=%-16s base(%s)=%s cur=%s "
              "Δ=%s (%s)"
              % (t, s["version"], s["build"] or "—", s["baseline_date"] or "—",
                 s["baseline_capital"], s["current_capital"],
                 s["pnl_since_baseline"],
                 ("%.2f%%" % (s["return_since_baseline"] * 100))
                 if s["return_since_baseline"] is not None else "—"))
    if write and not dry_run:
        try:
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            path = os.path.join(ARCHIVE_DIR, "qmt_account_metrics_%s.json" % date)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(metrics, f, ensure_ascii=False, indent=1)
            print("[SAVE] %s" % path)
        except Exception as e:
            print("[SAVE-ERR] %r" % (e,))
    return metrics


def load(date=None):
    """读取当日留档（collect 未跑或失败时的回退）。无则 None。"""
    date = date or time.strftime("%Y%m%d")
    path = os.path.join(ARCHIVE_DIR, "qmt_account_metrics_%s.json" % date)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="沙箱外账户指标采集（xtquant 只读 + FormulaOutput 日志）")
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打印不落盘")
    args = ap.parse_args()
    metrics = collect(args.date, dry_run=args.dry_run)
    ok = any(a["connected"] for a in metrics["accounts"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
