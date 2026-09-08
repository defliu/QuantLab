# coding: utf-8
"""午休持仓查询：QMT 实时 query_stock_positions 优先，失败回退本地 CSV FIFO。"""
import json
import os
import sys
import time

import qmt_config as C

# 加载 xtquant
sys.path.append(C.XTPACK)

def query_qmt():
    """QMT 实时查询持仓。返回 (positions, vols, sellable) 或 None。"""
    try:
        from xtquant import xttrader, xttype
        trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
        trader.start()
        try:
            if trader.connect() != 0:
                print("QMT 连接失败")
                return None
            account = xttype.StockAccount(C.ACCOUNT_ID)
            trader.subscribe(account)
            time.sleep(5)
            positions = trader.query_stock_positions(account)
            if not positions:
                print("QMT 无持仓数据")
                return None
            from qmt_monitor import fifo_positions_from_log
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
                    pos_out[code] = fifo[code][1]
                else:
                    pos_out[code] = float(getattr(p, "open_price", 0) or 0)
            print("QMT 实时持仓 %d 只" % len(pos_out))
            return pos_out, vol_out, sell_out
        finally:
            trader.stop()
    except Exception as e:
        print("QMT 查询异常: %r" % (e,))
        return None

def query_csv():
    """回退本地 CSV FIFO 推导持仓。"""
    from qmt_monitor import load_positions_from_log
    positions, vols = load_positions_from_log()
    print("本地 CSV 持仓 %d 只" % len(positions))
    return positions, vols, {}

def main():
    result = query_qmt()
    if result is None or not result[0]:
        result = query_csv()
    positions, vols, sellable = result
    out = {
        "positions": {c: {"cost": positions[c], "vol": vols.get(c, 0),
                          "sellable": sellable.get(c, 0)} for c in positions},
        "count": len(positions),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()