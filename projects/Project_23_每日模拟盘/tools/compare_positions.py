# -*- coding: utf-8 -*-
"""对比账本净持仓（qmt_trade_log.csv）与真实账户持仓（miniQMT 查询），分析差异原因。

口径与 paper/live_nav.py 的 build_v13_nav 一致：
- 300684.SZ 全票剔除（调试错误，用户 2026-10-22 确认）
- 单笔金额 > 本金3倍 剔除
- 2026-08-25 前卖出且策略从未买过的票（历史持仓清理）剔除
只读，不下单。
"""
import json
import sys

import pandas as pd

sys.path.insert(0, r"D:\国金QMT交易端模拟\bin.x64\Lib\site-packages")
from xtquant.xttrader import XtQuantTrader  # noqa: E402
from xtquant.xttype import StockAccount     # noqa: E402

QMT_PATH = r"D:\国金QMT交易端模拟\userdata_mini"
ACCOUNT = "67014907"
TRADE_LOG = r"D:\QuantLab\projects\Project_16_LightGBM股票大师\data\qmt_trade_log.csv"
CAPITAL_JSON = r"D:\QuantLab\projects\Project_16_LightGBM股票大师\data\strategy_capital.json"
OUT = r"D:\QuantLab\projects\Project_23_每日模拟盘\results\positions_diff.json"


def load_ledger_positions():
    t = pd.read_csv(TRADE_LOG)
    t["time"] = pd.to_datetime(t["time"])
    t["amount"] = (t["vol"] * t["price"]).fillna(0)
    n_all = len(t)

    t = t[t["code"] != "300684.SZ"]
    n_dbg = n_all - len(t)

    cap = json.load(open(CAPITAL_JSON, encoding="utf-8"))
    base = cap.get("capital_base", 83663.92)
    m1 = len(t)
    t = t[t["amount"] <= base * 3]
    n_big = m1 - len(t)

    bought = set(t.loc[t["side"].str.upper() == "BUY", "code"])
    cleanup = (t["side"].str.upper() == "SELL") & (~t["code"].isin(bought)) & \
              (t["time"] < pd.Timestamp("2026-08-25"))
    n_cleanup = int(cleanup.sum())
    t = t[~cleanup]

    t["s"] = t["side"].str.upper().map({"BUY": 1, "SELL": -1})
    net = (t["vol"] * t["s"]).groupby(t["code"]).sum()
    print("账本剔除明细: 300684=%d 笔, 金额超限=%d 笔, 历史清理卖出=%d 笔, 参与净额=%d 笔"
          % (n_dbg, n_big, n_cleanup, len(t)))
    return net, t


def query_live_positions():
    trader = XtQuantTrader(QMT_PATH, 20261024)
    trader.start()
    if trader.connect() != 0:
        raise SystemExit("!! miniQMT 连接失败")
    acc = StockAccount(ACCOUNT)
    trader.subscribe(acc)
    pos = trader.query_stock_positions(acc) or []
    trader.stop()
    return {p.stock_code: {"volume": p.volume, "can_use": getattr(p, "can_use_volume", None),
                           "market_value": getattr(p, "market_value", 0) or 0} for p in pos}


def norm(code):
    return code.replace("SH", ".SH").replace("SZ", ".SZ") if "." not in code else code


def main():
    net_ledger, trades = load_ledger_positions()
    live = query_live_positions()

    ledger_pos = net_ledger[net_ledger != 0]
    ledger_neg = net_ledger[net_ledger < 0]

    rows = []
    live_codes = {norm(c) for c in live}
    for code in sorted(set(ledger_pos.index) | live_codes):
        lv = live.get(code) or next((live[k] for k in live if norm(k) == code), None)
        rows.append({
            "code": code,
            "ledger_net": int(net_ledger.get(code, 0)),
            "live_volume": int(lv["volume"]) if lv else 0,
            "live_mv": round(lv["market_value"], 2) if lv else 0.0,
        })
    df = pd.DataFrame(rows)
    df["diff"] = df["live_volume"] - df["ledger_net"]
    df = df.sort_values(["diff", "code"])

    print("\n=== 逐票对比（仅账本净持仓≠0 或 真实账户有持仓）===")
    print(df.to_string(index=False))

    live_only = df[(df["ledger_net"] == 0) & (df["live_volume"] > 0)]
    both = df[(df["ledger_net"] != 0) & (df["live_volume"] > 0)]
    ledger_only = df[(df["ledger_net"] != 0) & (df["live_volume"] == 0)]
    print("\n统计: 真实账户持仓 %d 只 | 账本非零 %d 只（正 %d / 负 %d）"
          % (len(df[df.live_volume > 0]), len(ledger_pos), len(ledger_pos[ledger_pos > 0]), len(ledger_neg)))
    print("仅真实账户有: %d 只 | 两侧都有: %d 只 | 仅账本有: %d 只"
          % (len(live_only), len(both), len(ledger_only)))
    if len(both):
        print("\n两侧都有的票（diff=真实-账本）:")
        print(both.to_string(index=False))

    df.to_json(OUT, orient="records", force_ascii=False, indent=2)
    print("\n已落盘: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
