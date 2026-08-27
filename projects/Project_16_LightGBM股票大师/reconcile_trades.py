# coding: utf-8
"""交易对账：qmt_trade_log.csv 推导持仓 vs miniQMT 实际持仓 + 已实现/浮盈重算。

背景：2026-08-25 有 7,600 股 300684 卖出未入账（QMT 昨日持仓 100,500 vs 日志推导 108,100），
已于 2026-08-27 补记（score=RECON_BACKFILL，价格 85.50 估算）。本脚本用于日常/事后对账。

用法：
  python reconcile_trades.py           # 连接 miniQMT 实测对账（需客户端已登录）
  python reconcile_trades.py --no-qmt  # 仅本地推导（QMT 不可用时，跳过实测对比/浮盈）

输出：
  - 本地推导持仓（BUY - SELL 净额） vs QMT 实际持仓，逐代码 PASS/FAIL
  - 已实现盈亏（FIFO，实盘费率；跳过 price=0 的市价清仓记录）
  - 当前持仓浮盈（以 QMT 持仓现价为主，xtdata 兜底）
"""
import argparse
import csv
import os
import sys
import time
from collections import defaultdict, deque

import qmt_config as C

sys.path.append(C.XTPACK)


# ---------------- 费率（与 strategy_capital.py 一致，实盘口径） ----------------
def buy_fee(amt, code):
    return max(C.COMM_MIN, amt * C.COMM_RATE) + (amt * C.TRANS_RATE if code.startswith("6") else 0.0)


def sell_fee(amt, code):
    return (max(C.COMM_MIN, amt * C.COMM_RATE) + amt * C.STAMP_RATE
            + (amt * C.TRANS_RATE if code.startswith("6") else 0.0))


# 超买/手工标的（策略真实盈利口径，与 strategy_capital.py 保持一致）
EXCLUDE_CODES = {"300684.SZ"}


def load_rows():
    if not os.path.exists(C.TRADE_LOG):
        return []
    with open(C.TRADE_LOG, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def derive_positions(rows, exclude=frozenset()):
    """BUY - SELL 净额（price=0 的 SELL 为市价清仓记录，同样扣减；未买入过的代码忽略）。
    exclude：超买/手工标的集合，不在其内则不计入（用于策略真实口径）。
    返回 {code: (net_vol, avg_cost_含费_fifo)}
    """
    q = defaultdict(deque)  # code -> deque[(vol, cost_per含费)]
    realized = 0.0
    for r in sorted(rows, key=lambda x: x.get("time", "")):
        code, side = r.get("code", ""), r.get("side", "")
        if not code or code in exclude:
            continue
        try:
            vol = int(float(r["vol"]))
            price = float(r["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if side == "BUY":
            amt = price * vol
            q[code].append((vol, (amt + buy_fee(amt, code)) / vol))
        elif side == "SELL":
            sv = vol
            while sv > 0 and q[code]:
                v, cost_per = q[code][0]
                take = min(v, sv)
                sell_amt = price * take
                realized += (sell_amt - sell_fee(sell_amt, code)) - cost_per * take
                sv -= take
                q[code][0] = (v - take, cost_per)
                if q[code][0][0] <= 0:
                    q[code].popleft()
    pos = {}
    for code, dq in q.items():
        tv = sum(v for v, _ in dq)
        tc = sum(v * c for v, c in dq)
        if tv > 0:
            pos[code] = (tv, tc / tv)
    return pos, realized


def query_qmt_positions():
    """查询 miniQMT 实际持仓（volume>0）。返回 (positions, prices)。
    positions: {code: vol}; prices: {code: last_price}（由 market_value/volume 反推，缺失置 0）
    """
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            print("    !! QMT 交易通道连接失败，跳过实测")
            return None, None
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(4)
        positions = trader.query_stock_positions(account)
        pos, prices = {}, {}
        for p in positions or []:
            code = getattr(p, "stock_code", "")
            vol = int(getattr(p, "volume", 0) or 0)
            if not code or vol <= 0:
                continue
            pos[code] = vol
            mv = float(getattr(p, "market_value", 0) or 0)
            prices[code] = mv / vol if mv > 0 else 0.0
        return pos, prices
    finally:
        trader.stop()


def detect_anomalies(rows):
    """检测成交记录异常（仅针对日志内买过的策略代码，忽略接管前遗留旧仓的清理卖出）：
    - 卖出量 > 买入量（未成交单误记 / 重复记录 / 漏记买入）
    - 策略持仓的 SELL 记录 price<=0（市价清仓未回填成交价，盈亏会失真）
    返回异常列表 [(code, 描述)]。
    """
    bought, sold = defaultdict(int), defaultdict(int)
    price0_strategy = []
    for r in rows:
        code = r.get("code", "")
        if not code:
            continue
        try:
            vol = int(float(r["vol"]))
        except (TypeError, ValueError):
            continue
        side = r.get("side", "")
        if side == "BUY":
            bought[code] += vol
        elif side == "SELL":
            sold[code] += vol
            if float(r.get("price", 0) or 0) <= 0 and bought.get(code, 0) > 0:
                price0_strategy.append(code)
    issues = []
    for code in sorted(set(bought.keys())):  # 只查策略买过的代码
        b, s = bought.get(code, 0), sold.get(code, 0)
        if s > b:
            issues.append((code, f"卖出量 {s} > 买入量 {b}（疑似未成交单误记/重复记录）"))
    if price0_strategy:
        issues.append(("汇总", f"策略持仓 {len(set(price0_strategy))} 只出现 price=0 卖出（未回填成交价，盈亏失真）"))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-qmt", action="store_true", help="跳过 QMT 实测对比与浮盈")
    args = ap.parse_args()

    rows = load_rows()
    pos_log, realized = derive_positions(rows)                       # 全账户口径（含超买，供持仓对账）
    _, realized_real = derive_positions(rows, exclude=EXCLUDE_CODES)  # 策略真实口径（剔除超买）
    issues = detect_anomalies(rows)
    report = []
    report.append(f"# 交易对账报告 · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("")
    report.append(f"- 成交记录：{len(rows)} 条")
    report.append(f"- 已实现盈亏（FIFO，实盘费率）· 全账户：**{realized:,.2f} 元**"
                  f" ｜ 策略真实（剔除 {', '.join(sorted(EXCLUDE_CODES))}）：**{realized_real:,.2f} 元**")
    report.append("")
    report.append("## 异常检测")
    if issues:
        for code, desc in issues:
            report.append(f"- ⚠️ {code}: {desc}")
    else:
        report.append("- 无异常")
    report.append("")
    report.append("## 持仓对账（本地日志推导 vs QMT 实测 volume）")
    fail = 0
    if not args.no_qmt:
        pos_qmt, prices = query_qmt_positions()
        if pos_qmt is None:
            report.append("- QMT 连接失败，未实测")
        else:
            all_codes = sorted(set(list(pos_log.keys()) + list(pos_qmt.keys())))
            for code in all_codes:
                lv = pos_log.get(code, (0, 0))[0]
                qv = pos_qmt.get(code, 0)
                ok = lv == qv
                if not ok:
                    fail += 1
                report.append(f"- [{'PASS' if ok else 'FAIL'}] {code}: 日志={lv}  QMT={qv}"
                              + ("  一致" if ok else "  **不一致**"))
            report.append("")
            report.append(f"合计 {len(all_codes)} 只，不一致 {fail} 只" + (" ✅" if fail == 0 else " ❌"))
            float_sum = 0.0
            report.append("")
            report.append("## 持仓浮盈（成本=FIFO 含费均价 | 现价=QMT 市值反推）")
            for code in sorted(pos_log):
                vol, cost = pos_log[code]
                px = prices.get(code, 0.0)
                if px <= 0:
                    report.append(f"- {code}: 现价缺失（跳过）")
                    continue
                pnl = (px - cost) * vol
                float_sum += pnl
                report.append(f"- {code}: {vol}股 成本{cost:.3f} 现价{px:.2f} 市值{px*vol:,.0f} 浮盈{pnl:,.0f} ({(px/cost-1)*100:+.2f}%)")
            report.append(f"- 浮盈合计：**{float_sum:,.2f} 元**")
            report.append("")
            # 策略真实口径（剔除超买/手工标的，与 strategy_capital.py 一致）
            pos_real, realized_real = derive_positions(rows, exclude=EXCLUDE_CODES)
            float_real = sum((prices.get(c, 0.0) - cp) * v
                             for c, (v, cp) in pos_real.items() if prices.get(c, 0.0) > 0)
            report.append("## 资金池")
            report.append(f"- 全账户口径：初始 {C.START_CAPITAL:,.0f} + 已实现 {realized:,.0f} + 浮盈 {float_sum:,.0f}"
                          f" = **{C.START_CAPITAL + realized + float_sum:,.0f} 元**（含超买标的）")
            report.append(f"- **策略资金池（剔除 {', '.join(sorted(EXCLUDE_CODES))}）**：初始 {C.START_CAPITAL:,.0f}"
                          f" + 已实现 {realized_real:,.0f} + 浮盈 {float_real:,.0f}"
                          f" = **{C.START_CAPITAL + realized_real + float_real:,.0f} 元**")
    else:
        report.append("- （--no-qmt，未对比）")
        for code in sorted(pos_log):
            vol, cost = pos_log[code]
            report.append(f"- {code}: {vol}股 成本{cost:.3f}")
    report.append("")
    report.append("*仅供研究参考，不构成投资建议*")

    text = "\n".join(report)
    print(text)

    # 落盘每日对账报告
    out = os.path.join(os.path.dirname(C.TRADE_LOG), f"reconcile_{time.strftime('%Y%m%d')}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n对账报告已写入: {out}")

    # 退出码：不一致或异常则非 0，供定时任务告警
    if fail > 0 or issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
