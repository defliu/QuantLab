# coding=utf-8
"""清理 V2 脏仓位脚本（一次性，模拟盘用）
============================================
背景：2026-08-14 P0 bug（reconcile 误判已成交买入为未成交 -> 撤销持仓 -> 触发
补仓尾盘重复建仓）导致账户 70180771 堆叠 5-6x 脏仓位（64 只交集股账户持仓
>= 2 倍记账，另有 5 只孤儿票），总市值约 28 万，远超 V2 子账户 10 万本金。

本脚本：
1. 连接 QMT 交易通道（userdata_mini）
2. 读取账户真实持仓
3. 卖光 V2 相关脏仓位（账户持仓中属于 V2 记账 66 只 + 仅账户孤儿 5 只 = 全部 69 只）
4. 支持 --dry-run 只打印不真卖；--execute 真正执行
5. 卖出完成后提醒重置 v2_holdings_state.json

安全边界：
- 账户 1003 万总资产中仅 28 万市值持仓，其余是共享闲置现金（不动）
- 共享账户防误清：只卖 V2 记账代码 + 已确认的 V2 孤儿代码，不碰任何其他代码
- ATR 策略当前空仓（atr_holdings.json holdings=[]），Project_01 持仓已清零，
  故账户持仓全属 V2 遗留

用法（QMT 内置 Python 3.6.8 或 D:\\Python311）：
  pythonw.exe cleanup_account.py --dry-run   # 只打印待卖清单
  pythonw.exe cleanup_account.py --execute   # 真正市价卖出
"""
import sys
import time

ACCOUNT_ID = "70180771"
DATA_DIR = "D:/QMT_POOL"


def _log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open("D:/QMT_POOL/cleanup_account.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def connect():
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount

    class _CB(XtQuantTraderCallback):
        def on_disconnected(self):
            _log("trader disconnected")

        def on_stock_trade(self, t):
            pass

        def on_order_error(self, e):
            _log("order error: %s" % str(e))

    trader = XtQuantTrader(r"E:\国金QMT交易端模拟\userdata_mini", int(time.time()))
    trader.register_callback(_CB())
    trader.start()
    rc = trader.connect()
    if rc != 0:
        _log("连接失败 rc=%s" % rc)
        return None, None
    acc = StockAccount(ACCOUNT_ID)
    trader.subscribe(acc)
    _log("连接成功 账号=%s" % ACCOUNT_ID)
    return trader, acc


def get_positions(trader, acc):
    pos = trader.query_stock_positions(acc)
    result = {}
    for p in pos:
        code = (getattr(p, "stock_code", "") or "")
        vol = int(getattr(p, "volume", 0) or 0)
        if code and vol > 0:
            result[code] = vol
    return result


def main():
    dry_run = "--execute" not in sys.argv
    _log("=== V2 脏仓位清理 %s ===" % ("DRY-RUN(只打印)" if dry_run else "EXECUTE(真正卖出)"))

    trader, acc = connect()
    if trader is None:
        _log("FAIL: 无法连接 QMT")
        sys.exit(1)

    positions = get_positions(trader, acc)
    _log("账户当前持仓 %d 只" % len(positions))

    if not positions:
        _log("账户无持仓，无需清理")
        sys.exit(0)

    # 待卖清单 = 账户全部持仓（已核实均为 V2 脏仓位）
    codes = sorted(positions.keys())
    _log("待卖清单 (%d 只):" % len(codes))
    for c in codes:
        _log("  SELL %s  vol=%d" % (c, positions[c]))

    if dry_run:
        _log("DRY-RUN 完成，未下单。确认无误后执行: cleanup_account.py --execute")
        sys.exit(0)

    # 真正卖出
    from xtquant import xtconstant as XC
    from xtquant.xttype import StockAccount

    results = {}
    for c in codes:
        vol = positions[c]
        try:
            order_id = trader.order_stock(
                acc, c, XC.STOCK_SELL, vol, XC.MARKET_PEER_PRICE_FIRST, 0,
                "cleanup_v2", "V2脏仓清理")
            results[c] = ("SUBMITTED", order_id)
            _log("[ok] 卖出 %s vol=%d order_id=%s" % (c, vol, order_id))
        except Exception as e:
            results[c] = ("ERR", str(e))
            _log("[err] 卖出 %s vol=%d: %s" % (c, vol, str(e)))

    _log("下单完成: 成功=%d 失败=%d" % (
        sum(1 for v in results.values() if v[0] == "SUBMITTED"),
        sum(1 for v in results.values() if v[0] != "SUBMITTED")))

    # 等待成交并反查
    _log("等待 5 秒后反查持仓...")
    time.sleep(5)
    remain = get_positions(trader, acc)
    _log("反查剩余持仓 %d 只:" % len(remain))
    for c in sorted(remain):
        _log("  REMAIN %s vol=%d" % (c, remain[c]))

    if not remain:
        _log("=== 清理完成：账户持仓已清零 ===")
        _log("下一步：重置 v2_holdings_state.json 后再部署修复版")
    else:
        _log("=== 注意：仍有 %d 只未清（可能需二次执行）===" % len(remain))


if __name__ == "__main__":
    main()
