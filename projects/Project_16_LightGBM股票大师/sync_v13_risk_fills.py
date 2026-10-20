# coding: utf-8
"""V13 内置风控成交 -> 外部账本自动补记（消除 4c 人工对账的人工环节）

【背景 / 要解决的问题】
  QMT 内置风控策略 P16_V13_RISK.py 以「自闭环零文件」模式运行（沙箱禁写文件，见 AGENTS.md
  「QMT 文件 IO 沙箱红线」），其止损/止盈/追盈卖出**不会回写外部账本** data/qmt_trade_log.csv
  -> 外部账本滞后于账户真相 -> rebalance_daily.py 在 QMT 查询不可用时会按旧账本估算持仓
  -> 误判「仍持有」-> 需人工 4c 对账补记。
  实例（2026-10-19）：内置风控 09:53:52 止损卖出 002011.SZ 7600 股（-7% 口径），
  账本直到 12:05 才由 TRAE 换仓任务的 4c 协议补记 -> 期间 10:05 guard 按旧账本误判 hold。

【本脚本做什么】
  读 QMT 内置策略的 print 日志（唯一合规输出通道）-> 解析 V13 的风控成交
  -> 与账本比对 -> **幂等**补记缺失的 SELL 行（8 列齐全、带账号戳）。
  纯旁路 + 只读日志 + 幂等追加，**不触碰任何交易/风控逻辑**，也不改 QMT 侧任何文件。

【口径 / 数据源】
  - 成交事实：`<QMT_PATH>/userdata/log/XtClient_FormulaOutput_<date>.log`
      匹配 `[P16V13][RISK] <reason> <sid> <code> 卖<n>股 ret=0 | 现价<x> ...`
        + `[P16V13][FILLED] <sid> <code> deal=<n>`（含「原单已全成交」「撤前已全成交」变体）
      source: strategy/strategy_p16_v13_risk_src.py L1035 / L628 / L720 / L872
  - 成交均价/委托号：账户当日委托（query_stock_orders，只读；**不用 query_stock_trades**——
    该接口非交易时段会无限挂起，见 T-20261016-002），取 m_dTradedPrice / m_nOrderID；
    账户不可用时降级用日志「现价」并如实标注（price_src=log_ref）。

【用法】
  python sync_v13_risk_fills.py                 # 默认今天，dry-run（只报告，不写）
  python sync_v13_risk_fills.py --date 20261019
  python sync_v13_risk_fills.py --apply         # 真实补记账本（写前自动备份）
  python sync_v13_risk_fills.py --no-account    # 跳过账户查询，纯日志（离线可跑）

【退出码】0=正常（含无待补）；2=有写入失败/校验失败
"""
import argparse
import os
import re
import shutil
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmt_config as C  # noqa: E402

ACCOUNT_ID = C.ACCOUNT_ID
LEDGER = C.TRADE_LOG                      # data/qmt_trade_log.csv
LOG_DIR = os.path.join(C.QMT_PATH, "userdata", "log")
LEDGER_FIELDS = ["time", "code", "side", "vol", "price", "score", "order_id", "account_id"]

# [P16V13][RISK] SELL_STOP P16_20261019_RSK0001 002011.SZ 卖7600股 ret=0 | 现价10.75 跌破止损位10.76
_RE_RISK = re.compile(
    r"\[P16V13\]\[RISK\]\s+(?P<reason>\S+)\s+(?P<sid>\S+)\s+(?P<code>\S+)\s+卖(?P<vol>\d+)股\s+ret=(?P<ret>\S+)"
    r"(?:\s*\|\s*现价(?P<px>[\d.]+))?"
)
# [P16V13][FILLED] <sid> <code> deal=<n>  /  ... 原单已全成交 deal=<n>  /  ... 撤前已全成交 deal=<n>
_RE_FILLED = re.compile(
    r"\[P16V13\]\[FILLED\]\s+(?P<sid>\S+)\s+(?P<code>\S+)\s+(?:原单已全成交\s+|撤前已全成交\s+)?deal=(?P<deal>\d+)"
)
# 日志宿主行时间戳：2026-10-19 09:53:51,975 [INFO] ...
_RE_TS = re.compile(r"^(?P<d>\d{4}-\d{2}-\d{2}) (?P<t>\d{2}:\d{2}:\d{2})")


def _log_path(date):
    return os.path.join(LOG_DIR, "XtClient_FormulaOutput_%s.log" % date)


def parse_risk_fills(date):
    """解析日志 -> [{sid, code, reason, risk_vol, deal, ts, ref_px}]，只保留 ret=0 且已 FILLED 的。"""
    path = _log_path(date)
    if not os.path.exists(path):
        print("!! 日志不存在: %s" % path)
        return []
    risks = {}      # sid -> dict
    fills = {}      # sid -> (code, deal)
    last_ts = ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m_ts = _RE_TS.match(line)
            if m_ts:
                last_ts = "%s %s" % (m_ts.group("d"), m_ts.group("t"))
            m = _RE_RISK.search(line)
            if m:
                sid = m.group("sid")
                risks[sid] = {
                    "sid": sid,
                    "code": m.group("code").strip(),
                    "reason": m.group("reason").strip(),
                    "risk_vol": int(m.group("vol")),
                    "ret": m.group("ret").strip(),
                    "ref_px": float(m.group("px")) if m.group("px") else 0.0,
                    "ts": last_ts,
                }
                continue
            m = _RE_FILLED.search(line)
            if m:
                fills[m.group("sid")] = (m.group("code").strip(), int(m.group("deal")))

    out = []
    for sid, r in sorted(risks.items()):
        if r["ret"] not in ("0", "0.0"):
            continue                      # 未受理/失败 -> 不补记（RISK-FAIL 已在策略侧留痕）
        fl = fills.get(sid)
        if not fl:
            print("   [SKIP] %s %s 有 RISK 无 FILLED（可能未成交），不补记" % (sid, r["code"]))
            continue
        code, deal = fl
        if code and code != r["code"]:
            print("   [WARN] %s 代码不一致 RISK=%s FILLED=%s，以 FILLED 为准" % (sid, r["code"], code))
            r["code"] = code
        r["deal"] = deal
        out.append(r)
    return out


def read_ledger():
    """读账本，返回 (rows, 原始行列表)。"""
    if not os.path.exists(LEDGER):
        return [], []
    rows = []
    with open(LEDGER, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read().splitlines()
    for ln in raw[1:]:                    # 跳过表头
        parts = ln.split(",")
        if len(parts) < len(LEDGER_FIELDS):
            continue
        rows.append(dict(zip(LEDGER_FIELDS, parts)))
    return rows, raw


def ledger_sold_keys(rows):
    """已有 SELL 的幂等键集合：{(日期, code)}（同日同票不重复补记）。"""
    keys = set()
    for r in rows:
        if (r.get("side") or "").strip().upper() == "SELL":
            d = (r.get("time") or "")[:10].replace("-", "")
            keys.add((d, (r.get("code") or "").strip()))
    return keys


def query_orders_map():
    """账户当日委托 -> {(code, SELL): (traded_price, order_id)}；失败返回 {}（不抛）。"""
    try:
        if C.XTPACK not in sys.path:
            sys.path.append(C.XTPACK)
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import StockAccount
    except Exception as e:
        print("   [INFO] import xtquant 失败，降级纯日志: %r" % (e,))
        return {}
    trader = None
    try:
        trader = XtQuantTrader(C.USERDATA, int(time.time()))
        try:
            trader.register_callback(XtQuantTraderCallback())
        except Exception:
            pass
        trader.start()
        if trader.connect() != 0:
            print("   [INFO] 账户连接失败（rc!=0），降级纯日志")
            return {}
        acc = StockAccount(ACCOUNT_ID)
        trader.subscribe(acc)
        time.sleep(2)
        out = {}
        for o in (trader.query_stock_orders(acc) or []):
            code = str(getattr(o, "m_strStockCode", "") or "").strip()
            opt = str(getattr(o, "m_strOptName", "") or "")
            if not code or "卖出" not in opt:
                continue
            px = float(getattr(o, "m_dTradedPrice", 0) or 0) or float(getattr(o, "m_dPrice", 0) or 0)
            oid = str(getattr(o, "m_strOrderRef", "") or getattr(o, "m_nOrderID", "") or "")
            if px > 0:
                out[(code, "SELL")] = (px, oid)
        return out
    except Exception as e:
        print("   [INFO] 账户查询异常，降级纯日志: %r" % (e,))
        return {}
    finally:
        if trader is not None:
            try:
                trader.stop()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="V13 内置风控成交 -> 账本自动补记（旁路只读+幂等）")
    ap.add_argument("--date", default=time.strftime("%Y%m%d"), help="交易日 YYYYMMDD（默认今天）")
    ap.add_argument("--apply", action="store_true", help="真实补记账本（默认 dry-run）")
    ap.add_argument("--no-account", action="store_true", help="跳过账户查询，纯日志口径")
    args = ap.parse_args()
    date = args.date

    print("=" * 68)
    print("V13 风控成交 -> 账本补记  date=%s  mode=%s" % (date, "APPLY" if args.apply else "DRY-RUN"))
    print("=" * 68)

    fills = parse_risk_fills(date)
    print("[1] 日志解析：V13 已成交风控卖出 %d 笔" % len(fills))
    for r in fills:
        print("    %s %s %s 卖%d股 deal=%d ref_px=%.2f ts=%s"
              % (r["reason"], r["sid"], r["code"], r["risk_vol"], r["deal"], r["ref_px"], r["ts"]))
    if not fills:
        print("    无待处理（当日无 V13 风控成交）-> 结束")
        return 0

    rows, raw = read_ledger()
    sold = ledger_sold_keys(rows)
    print("[2] 账本：%d 行，已有 SELL 幂等键 %d 个" % (len(rows), len(sold)))

    todo = [r for r in fills if (date, r["code"]) not in sold]
    if not todo:
        print("    全部已补记过 -> 无待补（幂等）")
        return 0
    print("    待补记 %d 笔" % len(todo))

    omap = {} if args.no_account else query_orders_map()
    if omap:
        print("[3] 账户委托匹配：拿到 %d 条卖出委托（用于精确成交均价/委托号）" % len(omap))
    else:
        print("[3] 账户委托不可用 -> price 用日志「现价」参考值（price_src=log_ref）")

    new_rows = []
    for r in todo:
        hit = omap.get((r["code"], "SELL"))
        if hit:
            px, oid = hit
            px_src = "acct"
        else:
            px, oid = (r["ref_px"], "LOG_%s" % r["sid"])
            px_src = "log_ref"
        new_rows.append({
            "time": r["ts"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": r["code"],
            "side": "SELL",
            "vol": str(r["deal"]),
            "price": ("%.4f" % px).rstrip("0").rstrip(".") if px else "",
            "score": r["reason"],
            "order_id": oid,
            "account_id": ACCOUNT_ID,
            "_px_src": px_src,
        })

    print("[4] 计划追加：")
    for nr in new_rows:
        print("    %s,%s,%s,%s,%s,%s,%s,%s   (price_src=%s)"
              % (nr["time"], nr["code"], nr["side"], nr["vol"], nr["price"],
                 nr["score"], nr["order_id"], nr["account_id"], nr["_px_src"]))

    if not args.apply:
        print("\n[DRY-RUN] 未写入。加 --apply 才真实补记。")
        return 0

    # ---- 写前备份 ----
    if os.path.exists(LEDGER):
        bak = "%s.bak_presync_%s" % (LEDGER, time.strftime("%Y%m%d_%H%M%S"))
        try:
            shutil.copy2(LEDGER, bak)
            print("[5] 已备份 -> %s" % os.path.basename(bak))
        except Exception as e:
            print("!! 备份失败，中止写入: %r" % (e,))
            return 2

    # ---- 追加（新文件无表头则先写表头）----
    need_header = not os.path.exists(LEDGER) or os.path.getsize(LEDGER) == 0
    try:
        with open(LEDGER, "a", encoding="utf-8-sig", newline="") as f:
            if need_header:
                f.write(",".join(LEDGER_FIELDS) + "\n")
            for nr in new_rows:
                f.write(",".join([nr[k] for k in LEDGER_FIELDS]) + "\n")
    except Exception as e:
        print("!! 写账本失败: %r" % (e,))
        return 2

    # ---- 写后校验（行数 / 列数 / 账号戳）----
    rows2, raw2 = read_ledger()
    bad = 0
    for ln in raw2[1:]:
        if len(ln.split(",")) < len(LEDGER_FIELDS):
            bad += 1
    acct_bad = sum(1 for r in rows2 if (r.get("account_id") or "").strip() != ACCOUNT_ID)
    print("[6] 写后校验：账本 %d 行（+%d）、列数异常 %d、账号戳不符 %d"
          % (len(rows2), len(rows2) - len(rows), bad, acct_bad))
    if bad or acct_bad:
        print("!! 校验未通过 -> 请从备份 %s 还原后排查" % (bak,))
        return 2
    print("补记完成：%d 笔 -> %s" % (len(new_rows), LEDGER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
