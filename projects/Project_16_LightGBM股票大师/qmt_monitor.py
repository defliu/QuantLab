# coding: utf-8
"""盯盘层：miniQMT 实时行情监控 → 止损/止盈/移动止盈 → 预警（可选自动卖出）。

流程：
  1) 读取持仓（从 qmt_trade_log.csv 自动关联成本价，或用 --positions 指定）
  2) 通过 xtdata 订阅并轮询实时行情
  3) 逐持仓检查：硬止损 / 目标止盈 / 移动止盈（最高价回撤）
  4) 触发 → 控制台预警 + 写入 data/qmt_signal.json
  5) [可选自动执行] --auto-sell 开启后，触发信号经 order_guard 委托守护卖出
     （下单→轮询60s→未成撤单→更新价格重试→最多5次→涨跌停跳过；仅实际成交才记账，
      T-20260909 方案A：修掉"裸下单即记成交"导致的挂单滞留+假成交）

安全约定：
  - 默认只预警不自动交易（AUTO_SELL=False / 不传 --auto-sell）
  - --auto-sell 为真实卖出委托，务必先用模拟盘验证，确认账户/参数后再实盘开启

前提：miniQMT 客户端已登录运行。

用法：
  python qmt_monitor.py                          # 自动读持仓成本，仅预警
  python qmt_monitor.py --positions "001378.SZ:19.90:1000"
  python qmt_monitor.py --auto-sell              # 触发即自动卖出（真实委托，经 order_guard 守护）
  python qmt_monitor.py --once --auto-sell       # 单次检查+自动执行（适合定时快照）
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict, deque

# 编码兜底：Windows 控制台默认 GBK 无法输出 emoji（✅❌⛔ 等），强制 stdout/stderr 用 UTF-8
# 否则 qmt_monitor 在 --auto-sell 打印自动卖出开关时会抛 UnicodeEncodeError 而中断盯盘
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import qmt_config as C
import qmt_card as QC  # 盯盘信号 → 飞书 Card 2.0 交互卡片

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)


def _buy_fee(amt, code):
    """买入手续费：佣金(最低5元) + 沪市过户费（与 strategy_capital.py 同口径）。"""
    return max(C.COMM_MIN, amt * C.COMM_RATE) + (amt * C.TRANS_RATE if code.startswith("6") else 0.0)


def fifo_positions_from_log():
    """从 qmt_trade_log.csv 用 FIFO 推导当前持仓与成本（含费均价，与 strategy_capital 同口径）。

    返回 {code: (net_vol, avg_cost_含费)}。price=0 的市价清仓记录同样扣减；未买入过的代码忽略。
    """
    q = {}
    qq = defaultdict(deque)
    rows = C.load_trade_log_rows()
    rows.sort(key=lambda r: r.get("time", ""))
    for r in rows:
        code, side = r.get("code", ""), r.get("side", "")
        if not code:
            continue
        try:
            vol = int(float(r["vol"]))
            price = float(r["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if side == "BUY":
            amt = price * vol
            qq[code].append((vol, (amt + _buy_fee(amt, code)) / vol))
        elif side == "SELL":
            sv = vol
            while sv > 0 and qq[code]:
                v, cp = qq[code][0]
                take = min(v, sv)
                sv -= take
                qq[code][0] = (v - take, cp)
                if qq[code][0][0] <= 0:
                    qq[code].popleft()
    for code, dq in qq.items():
        tv = sum(v for v, _ in dq)
        if tv > 0:
            tc = sum(v * c for v, c in dq)
            q[code] = (tv, tc / tv)
    return q


def load_positions_from_log():
    """从 qmt_trade_log.csv 用 FIFO 推导持仓（净额 + 含费成本）。返回 (positions, vols)。"""
    positions, vols = {}, {}
    for code, (vol, cost) in fifo_positions_from_log().items():
        positions[code] = cost
        vols[code] = vol
    return positions, vols


def load_positions_from_qmt():
    """从 miniQMT 实时查询账户持仓。失败返回空 dict。

    返回 (positions, vols, sellable)：
      positions[code] = 成本价（优先成交记录 FIFO 含费成本；QMT open_price 不可靠，仅兜底）
      vols[code]      = 总持仓量（volume，含 T+1 锁定，盯盘判断用）
      sellable[code]  = 今日可卖量（can_use_volume，自动卖出用）
    """
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            print("    !! QMT 交易通道连接失败")
            return {}, {}, {}
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(5)  # 等待持仓数据异步就绪
        positions = trader.query_stock_positions(account)
        if not positions:
            print("    !! QMT 未查询到持仓（可能查询超时）")
            return {}, {}, {}
        fifo = fifo_positions_from_log()
        pos_out, vol_out, sell_out = {}, {}, {}
        for p in positions:
            code = getattr(p, "stock_code", "")
            vol = int(getattr(p, "volume", 0) or 0)
            if not code or vol <= 0:
                continue
            vol_out[code] = vol
            sell_out[code] = int(getattr(p, "can_use_volume", 0) or 0)
            if code in fifo and fifo[code][0] > 0:
                pos_out[code] = fifo[code][1]          # 权威成本：成交记录 FIFO 含费均价
            else:
                pos_out[code] = float(getattr(p, "open_price", 0) or 0)
        print(f"    QMT 实时持仓 {len(pos_out)} 只（成本优先取成交记录 FIFO）")
        return pos_out, vol_out, sell_out
    except Exception as e:
        print(f"    !! QMT 查询持仓异常: {e!r}")
        return {}, {}, {}
    finally:
        trader.stop()


def parse_positions(s):
    """解析 "code:cost[:vol],..."。返回 (positions, vols)。"""
    positions, vols = {}, {}
    for item in s.split(","):
        parts = item.strip().split(":")
        if len(parts) >= 2:
            code = parts[0]
            positions[code] = float(parts[1])
            vols[code] = int(float(parts[2])) if len(parts) >= 3 and parts[2] else 0
    return positions, vols


def get_ticks(watchlist):
    from xtquant import xtdata
    xtdata.subscribe_quote(watchlist, period="tick", count=-1)
    time.sleep(0.5)
    return xtdata.get_full_tick(watchlist)


def evaluate(position_cost, tick, peak_high, sellable_vol=0):
    """返回 (动作, 说明)。peak_high 为该持仓历史最高价；sellable_vol<=0（T+1 锁定）不评估、不并入峰值。

    T-20260907-002 修复：①T+1 锁定(can_use=0)的当日买入直接跳过信号评估（买了卖不掉，报信号=噪音）；
    ②追盈加激活阈值（峰值须 ≥ 成本×(1+TRAILING_ACTIVATE_PCT) 才追踪）+ 保本底线
    line=max(成本, peak×(1-TRAILING_PCT))，杜绝"高点仅微盈即回撤8%"在亏损位以追盈名义卖出。
    """
    last = float(tick.get("lastPrice", 0))
    if last <= 0:
        return "NO_DATA", "无行情"
    if position_cost is None or position_cost <= 0:
        return "HOLD", f"无成本信息(成本={position_cost})，跳过信号判断"
    if sellable_vol <= 0:
        return "HOLD", f"T+1锁定(可卖0)，不评估卖出信号"
    high = max(float(tick.get("high", last)), last, peak_high)
    if last <= position_cost * (1 + C.STOP_LOSS_PCT):
        return "SELL_STOP", f"现价{last:.2f} 跌破止损位{cost_stop(position_cost):.2f}"
    if last >= position_cost * (1 + C.TAKE_PROFIT_PCT):
        return "SELL_TAKE_PROFIT", f"现价{last:.2f} 达止盈位{cost_tp(position_cost):.2f}"
    if high >= position_cost * (1 + C.TRAILING_ACTIVATE_PCT):
        trailing_line = max(position_cost, high * (1 - C.TRAILING_PCT))
        if last <= trailing_line:
            return "SELL_TRAILING", f"从高点{high:.2f}回撤{C.TRAILING_PCT:.0%}，触发移动止盈(线{trailing_line:.2f})"
    return "HOLD", f"现价{last:.2f} 正常"


def cost_stop(cost):
    return cost * (1 + C.STOP_LOSS_PCT)


def cost_tp(cost):
    return cost * (1 + C.TAKE_PROFIT_PCT)


# ---- 自动执行：触发信号后直接卖出（方案A，T-20260909：接入 order_guard 委托守护）----

def _connect_trader():
    """建立 miniQMT 交易连接（供 order_guard 委托守护轮询/撤单重试复用）。

    原 _sell 每次裸 order_stock 下单即断连：无成交确认、无超时撤单、无重挂，
    跌停/流动性差时卖单滞留挂单队列且被误记成交（2026-09-09 300413 实证）。
    现改为复用一条连接传给 order_guard.order_with_guard（下单→轮询60s→未成撤单→
    更新价格重试→最多5次→涨跌停跳过），成交确认后才允许记账。
    返回 (trader, account)；失败返回 (None, None)。
    """
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            print("    !! QMT 交易通道连接失败（--auto-sell 本次仅预警、不下单）")
            trader.stop()
            return None, None
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(3)  # 等待订阅就绪
        return trader, account
    except Exception as e:
        print(f"    !! QMT 交易连接异常: {e!r}")
        try:
            trader.stop()
        except Exception:
            pass
        return None, None


def _sell(trader, account, code, vol, price):
    """通过 miniQMT 卖出持仓，接入 order_guard 委托守护。

    返回 order_guard 结果 dict：{"ok", "action", "traded_vol", "order_id", "attempts", "note"}。
    action: FILLED 全部成交 / LIMIT_SKIP 涨跌停跳过 / REJECTED 废单 / CANCELED_TIMEOUT 超时未全成。
    记账规则由调用方执行：仅 ok=True 且 traded_vol>0（已确认成交）才 sold.add + 写成交记录；
    LIMIT_SKIP/CANCELED_TIMEOUT 等不确认成交的状态绝不写成交记录（T-20260909 防假成交）。
    """
    import order_guard
    return order_guard.order_with_guard(trader, account, code, "SELL", vol, price,
                                        remark="auto_sell_signal")


# ---- 飞书推送（lark-cli bot 私聊通道，未配置则跳过）----

def notify_feishu(text):
    """通过 lark-cli 推送文本。默认目标取 LARK_PUSH_CHAT_ID（群发），否则私聊 FEISHU_OPEN_ID。"""
    cli = getattr(C, "LARK_CLI", "") or ""
    uid = getattr(C, "FEISHU_OPEN_ID", "") or ""
    chat_id = getattr(C, "LARK_PUSH_CHAT_ID", "") or ""
    as_ident = getattr(C, "LARK_PUSH_AS", "bot") or "bot"
    if not cli or (not uid and not chat_id):
        print("    (未配置 LARK_CLI/FEISHU_OPEN_ID/LARK_PUSH_CHAT_ID，跳过飞书推送)")
        return False
    target = ["--chat-id", chat_id] if chat_id else ["--user-id", uid]
    try:
        import subprocess
        r = subprocess.run(
            [cli, "im", "+messages-send"] + target + ["--text", text, "--as", as_ident],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            env=QC._env_for_cli(as_ident),
        )
        ok = r.returncode == 0
        print(f"    飞书推送[{as_ident}] {'群' if chat_id else '私聊'}: {'成功' if ok else '失败'} {(r.stdout or r.stderr).strip()[:120]}")
        return ok
    except Exception as e:
        print(f"    !! 飞书推送异常: {e!r}")
        return False


def push_signals_cards(signals):
    """信号 → 卡片：默认目标私聊 FEISHU_OPEN_ID（LARK_PUSH_CHAT_ID 非空时改为群发）。
    返回是否发送成功。"""
    ok_any = False
    for s in signals:
        ok_any |= QC.send_lark_card(QC.build_signal_card(s))
    return ok_any


def build_push_text(signals):
    """把本轮触发信号组装成飞书文本。"""
    lines = [f"【盯盘信号 {time.strftime('%H:%M')}】{len(signals)} 只触发"]
    for s in signals:
        act = {"SELL_STOP": "⛔止损", "SELL_TAKE_PROFIT": "🟢止盈", "SELL_TRAILING": "🟡移动止盈"}.get(s["action"], s["action"])
        exec_txt = "已自动卖出" if s.get("auto_sold") else ("自动卖出失败" if s.get("auto_sold") is False else "仅预警")
        if s.get("order_id") and s["order_id"] > 0:
            exec_txt += f"(order={s['order_id']})"
        lines.append(f"· {s['code']} 现价{s['last_price']:.2f} 成本{s['cost']:.2f} | {act} | {exec_txt}")
        lines.append(f"  {s['note']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", default=None, help='持仓 "code:cost[:vol],..."；缺省自动读成交记录')
    ap.add_argument("--watchlist", default=None, help="额外盯盘股票(逗号分隔)")
    ap.add_argument("--interval", type=float, default=C.MONITOR_INTERVAL)
    ap.add_argument("--once", action="store_true", help="只检查一次退出")
    ap.add_argument("--auto-sell", action="store_true", help="触发信号后自动卖出（真实委托，慎用）")
    args = ap.parse_args()

    sellable = {}
    if args.positions:
        positions, vols = parse_positions(args.positions)
    else:
        positions, vols, sellable = load_positions_from_qmt()
        src = "QMT 实时持仓"
        log_pos, log_vols = load_positions_from_log()
        if positions:
            # 成本缺失用本地 FIFO 成本补全；仍无成本则剔除（避免误报止盈/止损）
            for c in list(positions.keys()):
                if positions[c] <= 0:
                    if c in log_pos and log_pos[c] > 0:
                        positions[c] = log_pos[c]
                        print(f"    补成本 {c}: {log_pos[c]:.2f}（本地 FIFO 成本）")
                    else:
                        print(f"    !! {c} 无成本信息，剔除盯盘")
                        del positions[c]
                        vols.pop(c, None)
                        sellable.pop(c, None)
        else:
            print("    QMT 无持仓数据，回退本地成交记录")
            src = "本地成交记录"
            positions, vols = log_pos, log_vols
        if not positions:
            print("!! 无持仓可盯盘（QMT 与本地成交记录均无），可用 --positions 手动指定")
            return
        print(f"    持仓来源: {src}")

    auto_sell = C.AUTO_SELL or args.auto_sell
    watchlist = list(positions.keys())
    if args.watchlist:
        watchlist += [c.strip() for c in args.watchlist.split(",")]
    watchlist = list(dict.fromkeys(watchlist))

    print(f"[盯盘] 持仓 {len(positions)} 只 | 止损 {C.STOP_LOSS_PCT:.0%} | 止盈 {C.TAKE_PROFIT_PCT:.0%} | 移动止盈 {C.TRAILING_PCT:.0%}")
    print(f"       自动卖出: {'[ON] 开启(真实委托)' if auto_sell else '[OFF] 关闭(仅预警)'} | 连接: {C.USERDATA}")

    try:
        from xtquant import xtdata
    except Exception as e:
        print("!! 无法导入 xtquant:", e)
        return

    peak = {c: c_cost for c, c_cost in positions.items()}
    sold = set()  # 已自动卖出的持仓，避免重复

    # 方案A（T-20260909）：--auto-sell 时建立一条交易连接供 order_guard 委托守护复用；
    # 连接失败则本次仅预警、不下单（防"连不上却裸下单静默挂单"）。
    trader = account = None
    if auto_sell:
        trader, account = _connect_trader()

    while True:
        try:
            ticks = get_ticks(watchlist)
        except Exception as e:
            print(f"!! 行情获取失败（miniQMT 未启动或未登录？）: {e}")
            break

        signals = []
        for code, cost in positions.items():
            if code in sold:
                continue
            tick = ticks.get(code)
            if not tick:
                print(f"    {code}: 无行情")
                continue
            action, note = evaluate(cost, tick, peak.get(code, cost), sellable.get(code, vols.get(code, 0)))
            last = float(tick.get("lastPrice", 0))
            if sellable.get(code, vols.get(code, 0)) > 0:  # T+1 锁定不并入峰值（T-20260907-002）
                peak[code] = max(peak.get(code, cost), last)
            flag = {"HOLD": ".", "SELL_STOP": "[STOP]", "SELL_TAKE_PROFIT": "[TP]", "SELL_TRAILING": "[TRAIL]"}.get(action, "?")
            print(f"    {flag} {code} 现价{last:>7.2f} | 成本{cost:>7.2f} | {note}")
            if action == "HOLD":
                continue
            # 触发信号
            sig = {"code": code, "action": action, "note": note, "last_price": last,
                   "cost": cost, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            if auto_sell:
                if trader is None or account is None:
                    print(f"      [!] {code} 交易连接不可用，仅预警不自动卖出")
                    sig["auto_sold"] = False
                else:
                    vol = vols.get(code, 0)
                    sell_vol = min(vol, sellable.get(code, vol)) if sellable else vol
                    if sell_vol <= 0:
                        print(f"      [!] {code} 无可卖数量（T+1 锁定），跳过自动卖出")
                    else:
                        r = _sell(trader, account, code, sell_vol, last)
                        if r["traded_vol"] > 0:
                            # 有实际成交量（FILLED 全部 或 CANCELED_TIMEOUT 部分成交）→ 按真实量记账；
                            # 部分成交剩余未卖时不 sold.add，后续轮次继续评估剩余
                            print(f"      [ALERT] 自动卖出 {code} {r['traded_vol']}股 -> {r['note']}")
                            sig["auto_sold"] = True
                            sig["order_id"] = r["order_id"]
                            sig["traded_vol"] = r["traded_vol"]
                            C.append_trade_rows([[sig["time"], code, "SELL",
                                                  r["traded_vol"], last, action, r["order_id"]]])
                            if r["ok"]:
                                sold.add(code)
                        else:
                            # 0 成交（LIMIT_SKIP 涨跌停/REJECTED 废单/CANCELED_TIMEOUT 超时等）
                            # 绝不写成交记录（T-20260909 防假成交）；涨跌停跳过时后续轮次继续评估
                            print(f"      [SKIP/FAIL] {code} {r['action']} -> {r['note']}")
                            sig["auto_sold"] = False
                            sig["order_note"] = r["note"]
            signals.append(sig)

        if signals:
            os.makedirs(os.path.dirname(C.SIGNAL_FILE), exist_ok=True)
            with open(C.SIGNAL_FILE, "w", encoding="utf-8") as f:
                json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "signals": signals},
                          f, ensure_ascii=False, indent=2)
            print(f"    [ALERT] 触发 {len(signals)} 条信号，已写入 {C.SIGNAL_FILE}")
            # 信号 → 推 Card 2.0 交互卡片（群发 + 可选私聊）；全失败时回退纯文本摘要（保证触达）
            card_ok = push_signals_cards(signals)
            if not card_ok:
                notify_feishu(build_push_text(signals))
        else:
            notify_feishu(f"【盯盘 {time.strftime('%H:%M')}】无触发信号，持仓正常，持有中")

        if args.once:
            break
        time.sleep(max(1, args.interval))

    # 方案A（T-20260909）：退出时关闭交易连接（--once / 行情失败 break / 连续盯盘中断均落这里）
    if trader is not None:
        try:
            trader.stop()
        except Exception as e:
            print(f"    !! 关闭交易连接异常: {e!r}")


if __name__ == "__main__":
    main()
