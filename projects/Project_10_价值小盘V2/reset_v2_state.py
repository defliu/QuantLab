# coding: utf-8
"""重置 V2 持仓状态文件为空仓（cash=100000）。

用法: D:\\Python311\\python.exe reset_v2_state.py
清仓后执行，旧文件自动备份为 v2_holdings_state_<ts>.bak.json
"""
import json
import os
import shutil
import time

PATH = "D:/QMT_POOL/v2_holdings_state.json"
EMPTY = {
    "cash": 100000.0,
    "holdings": {},
    "entry_prices": {},
    "entry_dates": {},
    "nav_peak": 1.0,
    "today_orders": {},
    "suspended_sells": [],
}

if os.path.exists(PATH):
    bak = PATH.replace(".json", "_%s.bak.json" % time.strftime("%Y%m%d%H%M%S"))
    shutil.copy2(PATH, bak)
    print("备份 -> %s" % bak)
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(EMPTY, f, ensure_ascii=False)
print("已重置: %s (cash=100000 空仓)" % PATH)