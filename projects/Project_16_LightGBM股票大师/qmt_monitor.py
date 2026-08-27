# coding: utf-8
"""盯盘层：miniQMT 实时行情监控 → 止损/止盈/移动止盈 → 预警（可选自动卖出）。

流程：
  1) 读取持仓（从 qmt_trade_log.csv 自动关联成本价，或用 --positions 指定）
  2) 通过 xtdata 订阅并轮询实时行情
  3) 逐持仓检查：硬止损 / 目标止盈 / 移动止盈（最高价回撤）
  4) 触发 → 控制台预警 + 写入 data/qmt_signal.json
  5) [可选自动执行] --auto-sell 开启后，触发信号直接调用 miniQMT 卖出接口

安全约定：
  - 默认只预警不自动交易（AUTO_SELL=False / 不传 --auto-sell）
  - --auto-sell 为真实卖出委托，务必先用模拟盘验证，确认账户/参数后再实盘开启

前提：miniQMT 客户端已登录运行。

用法：
  python qmt_monitor.py                          # 自动读持仓成本，仅预警
  python qmt_monitor.py --positions "001378.SZ:19.90:1000"
  python qmt_monitor.py --auto-sell              # 触发即自动卖出（真实委托）
  python qmt_monitor.py --once --auto-sell       # 单次检查+自动执行（适合定时快照）
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request

# 编码兜底：Windows 控制台默认 GBK 无法输出 emoji（✅❌⛔ 等），强制 stdout/stderr 用 UTF-8
# 否则 qmt_monitor 在 --auto-sell 打印自动卖出开关时会抛 UnicodeEncodeError 而中断盯盘
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import qmt_config as C

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)


def load_positions_from_log():
    """从 qmt_trade_log.csv 读取最近买入作为持仓(成本, 数量)。返回 (positions, vols)。"""
    if not os.path.exists(C.TRADE_LOG):
        return {}, {}
    positions, vols = {}, {}
    with open(C.TRADE_LOG, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("side") == "BUY":
                code = row["code"]
                positions[code] = float(row["price"])
                try:
                    vols[code] = int(float(row["vol"]))
                except (TypeError, ValueError):
                    vols[code] = 0
    return positions, vols


def load_positions_from_qmt():
    """从 miniQMT 实时查询账户持仓（成本=open_price，数量=可用）。失败返回空 dict。

    返回 (positions, vols)：positions[code]=成本价，vols[code]=可卖数量。
    """
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            print("    !! QMT 交易通道连接失败")
            return {}, {}
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(5)  # 等待持仓数据异步就绪
        positions = trader.query_stock_positions(account)
        if not positions:
            print("    !! QMT 未查询到持仓（可能查询超时）")
            return {}, {}
        pos_out, vol_out = {}, {}
        for p in positions:
            code = getattr(p, "stock_code", "")
            vol = int(getattr(p, "can_use_volume", 0) or getattr(p, "volume", 0))
            if not code or vol <= 0:
                continue
            pos_out[code] = float(getattr(p, "open_price", 0) or 0)
            vol_out[code] = vol
        print(f"    QMT 实时持仓 {len(pos_out)} 只")
        return pos_out, vol_out
    except Exception as e:
        print(f"    !! QMT 查询持仓异常: {e!r}")
        return {}, {}
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


def evaluate(position_cost, tick, peak_high):
    """返回 (动作, 说明)。peak_high 为该持仓历史最高价。"""
    last = float(tick.get("lastPrice", 0))
    if last <= 0:
        return "NO_DATA", "无行情"
    if position_cost is None or position_cost <= 0:
        return "HOLD", f"无成本信息(成本={position_cost})，跳过信号判断"
    high = max(float(tick.get("high", last)), last, peak_high)
    if last <= position_cost * (1 + C.STOP_LOSS_PCT):
        return "SELL_STOP", f"现价{last:.2f} 跌破止损位{cost_stop(position_cost):.2f}"
    if last >= position_cost * (1 + C.TAKE_PROFIT_PCT):
        return "SELL_TAKE_PROFIT", f"现价{last:.2f} 达止盈位{cost_tp(position_cost):.2f}"
    if high > position_cost:
        trailing_line = high * (1 - C.TRAILING_PCT)
        if last <= trailing_line:
            return "SELL_TRAILING", f"从高点{high:.2f}回撤{C.TRAILING_PCT:.0%}，触发移动止盈(线{trailing_line:.2f})"
    return "HOLD", f"现价{last:.2f} 正常"


def cost_stop(cost):
    return cost * (1 + C.STOP_LOSS_PCT)


def cost_tp(cost):
    return cost * (1 + C.TAKE_PROFIT_PCT)


# ---- 自动执行：触发信号后直接卖出 ----

def _sell(code, vol, price):
    """通过 miniQMT 卖出持仓，返回 order_id。"""
    from xtquant import xttrader, xttype, xtconstant
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    if trader.connect() != 0:
        return -1
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    time.sleep(3)  # 等待订阅就绪
    if C.AUTO_SELL_PRICE_TYPE == "FIX":
        price_type, px = xtconstant.FIX_PRICE, round(price * 0.995, 2)
    else:
        price_type, px = xtconstant.LATEST_PRICE, 0.0
    order_id = trader.order_stock(account, code, xtconstant.STOCK_SELL, vol,
                                  price_type, px, "traework_monitor", "auto_sell_signal")
    trader.stop()
    return order_id


# ---- 飞书推送（lark-cli bot 私聊通道，未配置则跳过）----

def notify_feishu(text):
    """通过 lark-cli bot 身份私聊推送文本。返回是否成功。"""
    cli = getattr(C, "LARK_CLI", "") or ""
    uid = getattr(C, "FEISHU_OPEN_ID", "") or ""
    if not cli or not uid:
        print("    (未配置 LARK_CLI/FEISHU_OPEN_ID，跳过飞书推送)")
        return False
    try:
        import subprocess
        env = dict(os.environ)
        # 外部注入的 app 只有 user token、无 bot 凭据且 strict-mode=user 会挡住 bot；
        # 移除注入并关闭 strict-mode，让 lark-cli 用 config.json 里的 Trae app(cli_aa0f...，有 bot 凭据)
        env.pop("LARKSUITE_CLI_APP_ID", None)
        env.pop("LARKSUITE_CLI_USER_ACCESS_TOKEN", None)
        env["LARKSUITE_CLI_STRICT_MODE"] = "off"
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        r = subprocess.run(
            [cli, "im", "+messages-send", "--user-id", uid, "--text", text, "--as", "bot"],
            capture_output=True, text=True, encoding="utf-8", timeout=15, env=env,
        )
        ok = r.returncode == 0
        print(f"    飞书推送: {'成功' if ok else '失败'} {(r.stdout or r.stderr).strip()[:120]}")
        return ok
    except Exception as e:
        print(f"    !! 飞书推送异常: {e!r}")
        return False


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

    if args.positions:
        positions, vols = parse_positions(args.positions)
    else:
        positions, vols = load_positions_from_qmt()
        src = "QMT 实时持仓"
        log_pos, log_vols = load_positions_from_log()
        if positions:
            # 成本缺失用本地买入价补全；仍无成本则剔除（避免误报止盈/止损）
            for c in list(positions.keys()):
                if positions[c] <= 0:
                    if c in log_pos:
                        positions[c] = log_pos[c]
                        print(f"    补成本 {c}: {log_pos[c]:.2f}（本地买入价）")
                    else:
                        print(f"    !! {c} 无成本信息，剔除盯盘")
                        del positions[c]
                        vols.pop(c, None)
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

    while True:
        try:
            ticks = get_ticks(watchlist)
        except Exception as e:
            print(f"!! 行情获取失败（miniQMT 未启动或未登录？）: {e}")
            return

        signals = []
        for code, cost in positions.items():
            if code in sold:
                continue
            tick = ticks.get(code)
            if not tick:
                print(f"    {code}: 无行情")
                continue
            action, note = evaluate(cost, tick, peak.get(code, cost))
            last = float(tick.get("lastPrice", 0))
            peak[code] = max(peak.get(code, cost), last)
            flag = {"HOLD": ".", "SELL_STOP": "[STOP]", "SELL_TAKE_PROFIT": "[TP]", "SELL_TRAILING": "[TRAIL]"}.get(action, "?")
            print(f"    {flag} {code} 现价{last:>7.2f} | 成本{cost:>7.2f} | {note}")
            if action == "HOLD":
                continue
            # 触发信号
            sig = {"code": code, "action": action, "note": note, "last_price": last,
                   "cost": cost, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            if auto_sell:
                vol = vols.get(code, 0)
                if vol <= 0:
                    print(f"      [!] {code} 无持仓数量，无法自动卖出（--positions 需带 vol 或补充交易记录）")
                else:
                    order_id = _sell(code, vol, last)
                    if order_id is not None and order_id > 0:
                        print(f"      [ALERT] 自动卖出 {code} {vol}股 @ 市价 -> order_id={order_id}")
                        sold.add(code)
                        sig["auto_sold"] = True
                        sig["order_id"] = order_id
                        with open(C.TRADE_LOG, "a", encoding="utf-8-sig", newline="") as f:
                            w = csv.writer(f)
                            if not os.path.exists(C.TRADE_LOG) or os.path.getsize(C.TRADE_LOG) == 0:
                                w.writerow(["time", "code", "side", "vol", "price", "score", "order_id"])
                            w.writerow([sig["time"], code, "SELL", vol, last, action, order_id])
                    else:
                        print(f"      [FAIL] 自动卖出 {code} 失败（检查 miniQMT 客户端/账号）")
                        sig["auto_sold"] = False
            signals.append(sig)

        if signals:
            os.makedirs(os.path.dirname(C.SIGNAL_FILE), exist_ok=True)
            with open(C.SIGNAL_FILE, "w", encoding="utf-8") as f:
                json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "signals": signals},
                          f, ensure_ascii=False, indent=2)
            print(f"    [ALERT] 触发 {len(signals)} 条信号，已写入 {C.SIGNAL_FILE}")
            notify_feishu(build_push_text(signals))
        else:
            notify_feishu(f"【盯盘 {time.strftime('%H:%M')}】无触发信号，持仓正常，持有中")

        if args.once:
            break
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
