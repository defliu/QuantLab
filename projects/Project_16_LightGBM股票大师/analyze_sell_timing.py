# coding: utf-8
"""P16 G2 / V1.3 卖出时机与风控实证统计（只读，不改任何生产文件）。

输入：
  V1.3(67014907)  data/qmt_trade_log.csv
  G2 (70180771)   D:/QMT_POOL/g2_bridge/state/fills_*.json + positions_*.json
输出：
  results/卖出时机与风控评估_数据统计_{ts}.json
"""
import csv
import glob
import json
import os
import sys
from collections import defaultdict, deque
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
BRIDGE = "D:/QMT_POOL/g2_bridge/state"
OUT_DIR = os.path.join(BASE, "results")

# V1.3: 需要剔除的历史清仓（8/20-8/24 对账户原持仓的市价清仓，非策略换仓）
EXCLUDE_REASON = {""}
LEGACY_SELL_CODES_DATE = None


def buy_fee(amt, code):
    """买入费用：佣金万2(最低5元) + 沪市过户费万0.1"""
    return max(5.0, amt * 0.0002) + (amt * 0.00001 if code.startswith("6") else 0.0)


def sell_fee(amt, code):
    """卖出费用：佣金万2(最低5元) + 印花税万5 + 沪市过户费万0.1"""
    return max(5.0, amt * 0.0002) + amt * 0.0005 + (amt * 0.00001 if code.startswith("6") else 0.0)


def load_v13():
    path = os.path.join(DATA, "qmt_trade_log.csv")
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def analyze_v13(rows):
    """FIFO 配对，输出每笔平仓的 (code, buy_date, sell_date, hold_days, ret, reason, amt)。"""
    q = defaultdict(deque)          # code -> deque[(vol, cost含费, date)]
    out = []
    orphan_sell = []
    for r in rows:
        code = r["code"]
        side = r["side"]
        vol = int(float(r["vol"]))
        price = float(r["price"])
        ts = r["time"]
        d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        reason = r["score"] or ""
        if side == "BUY":
            if price <= 0:
                continue
            amt = price * vol
            q[code].append((vol, (amt + buy_fee(amt, code)) / vol, d))
        elif side == "SELL":
            # 历史清仓（price=0 或 score=0.0 且为 8/20-8/24 的老仓）→ 不配对，直接丢弃
            if price <= 0:
                continue
            sv = vol
            while sv > 0 and q[code]:
                v, cp, bd = q[code][0]
                take = min(v, sv)
                sv -= take
                net = price * take - sell_fee(price * take, code)
                ret = net / (cp * take) - 1.0
                out.append({
                    "code": code, "buy_date": str(bd), "sell_date": str(d),
                    "hold_cal_days": (d - bd).days,
                    "vol": take, "cost": round(cp, 4), "sell_price": price,
                    "ret": round(ret, 6), "reason": reason,
                    "pnl": round(net - cp * take, 2),
                })
                q[code][0] = (v - take, cp, bd)
                if q[code][0][0] <= 0:
                    q[code].popleft()
            if sv > 0:
                orphan_sell.append({"code": code, "date": str(d), "vol": sv, "reason": reason})
    return out, orphan_sell, {c: sum(v for v, _, _ in dq) for c, dq in q.items() if sum(v for v, _, _ in dq) > 0}


def load_g2():
    fills = []
    for p in sorted(glob.glob(os.path.join(BRIDGE, "fills_*.json"))):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        for x in d.get("fills", []):
            x["_file_date"] = d.get("date")
            fills.append(x)
    fills.sort(key=lambda x: x.get("ts", ""))
    return fills


def g2_rounds(fills):
    """G2 按 code 汇总：买入总额/卖出总额 → 已实现盈亏。

    成本锚强制以**账户 positions 快照为唯一真相**（AGENTS 红线）：
    9/2 发生过「status=55 误判废单→不撤原单重报→双倍建仓」，fills 账本只记 3000/800 股，
    而账户实际 6400/1000 股 —— 用 fills 推成本会把收益放大 1~2 倍，必须用 positions.avg_price。
    """
    # 账户真实持仓快照（含费均价）作为成本真相
    acct_cost, acct_vol = {}, {}
    for p in sorted(glob.glob(os.path.join(BRIDGE, "positions_*.json"))):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        for it in d.get("positions", []):
            c, v, ap = it.get("code", ""), int(it.get("volume", 0) or 0), float(it.get("avg_price", 0) or 0)
            if c and v > 0 and ap > 0:
                acct_cost[c], acct_vol[c] = ap, v   # 取最新快照

    pos = defaultdict(lambda: {"buy_vol": 0, "buy_amt": 0.0, "sell_vol": 0, "sell_amt": 0.0,
                               "buy_dates": [], "sell_dates": [], "sell_reason": [], "sell_prices": []})
    for f in fills:
        if f.get("status") != "FILLED":
            continue
        c = f["code"]
        v = int(f.get("vol", 0) or 0)
        p = float(f.get("price", 0) or 0)
        if v <= 0 or p <= 0:
            continue
        rec = pos[c]
        if f["action"] == "BUY":
            amt = p * v
            rec["buy_vol"] += v
            rec["buy_amt"] += amt + buy_fee(amt, c)
            rec["buy_dates"].append(f.get("ts", "")[:10])
        else:
            amt = p * v
            rec["sell_vol"] += v
            rec["sell_amt"] += amt - sell_fee(amt, c)
            rec["sell_dates"].append(f.get("ts", "")[:10])
            rec["sell_reason"].append(f.get("reason", ""))
            rec["sell_prices"].append(p)
    out = []
    for c, r in pos.items():
        if r["sell_vol"] == 0:
            continue
        # 成本：账户快照含费均价优先（唯一真相），fills 推导仅作对照
        cost_each = acct_cost.get(c) or (r["buy_amt"] / r["buy_vol"] if r["buy_vol"] else 0)
        cost_src = "positions" if c in acct_cost else "fills"
        # 以「实际卖出股数」配对：成本是每股均价，卖多少配多少（fills 漏记的加仓股同样有真实成本）
        matched = r["sell_vol"]
        realized = r["sell_amt"] - cost_each * matched
        out.append({
            "code": c,
            "buy_dates": sorted(set(r["buy_dates"])),
            "sell_dates": sorted(set(r["sell_dates"])),
            "buy_vol_fills": r["buy_vol"], "acct_vol": acct_vol.get(c, 0),
            "sell_vol": r["sell_vol"],
            "avg_cost": round(cost_each, 4), "cost_src": cost_src,
            "avg_sell": round(sum(r["sell_prices"]) / len(r["sell_prices"]), 4) if r["sell_prices"] else 0,
            "realized_pnl": round(realized, 2),
            "ret": round(realized / (cost_each * matched), 6) if cost_each * matched else 0,
            "reason": r["sell_reason"],
        })
    return out


def summarize(trades, key="reason"):
    g = defaultdict(list)
    for t in trades:
        g[t[key]].append(t)
    out = {}
    for k, v in sorted(g.items()):
        rets = [x["ret"] for x in v]
        wins = [r for r in rets if r > 0]
        out[k] = {
            "n": len(v),
            "avg_ret": round(sum(rets) / len(rets), 6),
            "sum_ret": round(sum(rets), 6),
            "win_rate": round(len(wins) / len(rets), 4),
            "avg_hold_cal_days": round(sum(x["hold_cal_days"] for x in v) / len(v), 2),
            "pnl_sum": round(sum(x["pnl"] for x in v), 2),
            "best": round(max(rets), 6), "worst": round(min(rets), 6),
        }
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = load_v13()
    trades, orphan, open_pos = analyze_v13(rows)
    # 300684 超买事故单独标记（8/24 误用账户全量资金买入 939 万，非策略资金池行为）
    accident = [t for t in trades if t["code"].startswith("300684")]
    normal = [t for t in trades if not t["code"].startswith("300684")]
    g2_fills = load_g2()
    g2 = g2_rounds(g2_fills)

    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "v13": {
            "all_trades": trades,
            "by_reason": summarize(trades),
            "by_reason_ex_300684": summarize(normal),
            "accident_300684": accident,
            "orphan_sells": orphan,
            "open_positions": open_pos,
        },
        "g2": {"rounds": g2, "fills_all": g2_fills},
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUT_DIR, "卖出时机与风控评估_数据统计_%s.json" % ts)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print("=" * 78)
    print("V1.3（67014907）平仓统计 —— 按卖出原因")
    print("=" * 78)
    print("%-16s %4s %10s %8s %8s %10s" % ("原因", "笔数", "平均收益", "胜率", "持有天", "盈亏(元)"))
    for k, v in res["v13"]["by_reason"].items():
        print("%-16s %4d %9.3f%% %7.1f%% %8.2f %10.0f" % (
            k or "(空)", v["n"], v["avg_ret"] * 100, v["win_rate"] * 100, v["avg_hold_cal_days"], v["pnl_sum"]))
    print("\n-- 剔除 300684 超买事故后 --")
    tot = {"n": 0, "pnl": 0.0, "rets": []}
    for k, v in res["v13"]["by_reason_ex_300684"].items():
        print("%-16s %4d %9.3f%% %7.1f%% %8.2f %10.0f" % (
            k or "(空)", v["n"], v["avg_ret"] * 100, v["win_rate"] * 100, v["avg_hold_cal_days"], v["pnl_sum"]))
        tot["n"] += v["n"]
        tot["pnl"] += v["pnl_sum"]
        tot["rets"] += [x["ret"] for x in normal if (x["reason"] or "") == k]
    if tot["n"]:
        print("%-16s %4d %9.3f%% %7.1f%% %8s %10.0f" % (
            "合计", tot["n"], sum(tot["rets"]) / len(tot["rets"]) * 100,
            len([r for r in tot["rets"] if r > 0]) / len(tot["rets"]) * 100, "-", tot["pnl"]))
    print("\n300684 超买事故小计: %d 笔, 盈亏 %.0f 元" % (
        len(accident), sum(a["pnl"] for a in accident)))
    print("孤儿卖出(无对应买入): %s" % orphan)
    print("当前持仓: %s" % open_pos)

    print("\n" + "=" * 78)
    print("G2（70180771）平仓统计")
    print("=" * 78)
    print("%-12s %-12s %-12s %8s %8s %9s %10s" % ("代码", "买入日", "卖出日", "成本", "卖出", "收益", "盈亏(元)"))
    for g in g2:
        print("%-12s %-12s %-12s %8.2f %8.2f %8.2f%% %10.0f" % (
            g["code"], ",".join(g["buy_dates"]), ",".join(g["sell_dates"]),
            g["avg_cost"], g["avg_sell"], g["ret"] * 100, g["realized_pnl"]))
    if g2:
        print("%-12s %-12s %-12s %8s %8s %8.2f%% %10.0f" % (
            "合计", "", "", "", "",
            sum(x["realized_pnl"] for x in g2) / 100000 * 100,
            sum(x["realized_pnl"] for x in g2)))
    print("\n输出: %s" % out)


if __name__ == "__main__":
    main()
