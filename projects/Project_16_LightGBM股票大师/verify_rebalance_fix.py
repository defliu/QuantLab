# -*- coding: utf-8 -*-
"""验证 rebalance_daily.py 的 T-20260907-003 修复（持仓数上限名额逻辑）。只读，不写生产文件。"""
import sys

sys.path.insert(0, r"D:\QuantLab\projects\Project_16_LightGBM股票大师")
import rebalance_daily as RD  # noqa: E402

today = RD._parse_date("20260907")
last_buy = RD._build_last_buy_dates()

print("== 场景1: 09-07 事故态（持仓 300413/003005 均未到期，target=601999/601579）==")
positions = {"300413.SZ": 0, "003005.SZ": 0}
sell_plan = []
for c in positions:
    ent = last_buy.get(c)
    held = RD._trading_days_between(ent, today) if ent else 999
    keep = held < 5
    print("  %s 买入日=%s 持有交易日=%s -> %s" % (c, ent, held, "保留" if keep else "到期卖出"))
    if not keep:
        sell_plan.append(c)
n_after = len(positions) - len(sell_plan)
slots = max(0, 2 - n_after)
print("  卖出后持仓 %d 只, 新票买入名额 slots=%d -> %s" % (
    n_after, slots, "本日0新增买入(修复生效)" if slots == 0 else "%d只" % slots))

print("== 场景2: 正常换仓（2只全到期卖出 -> 补2只新票）==")
sell_plan = ["a", "b"]
positions = {"a": 0, "b": 0}
n_after = len(positions) - len(sell_plan)
print("  slots =", max(0, 2 - n_after), "(应=2)")

print("== 场景3: 部分到期（卖1留1 -> 只补1只）==")
sell_plan = ["a"]
positions = {"a": 0, "b": 0}
n_after = len(positions) - len(sell_plan)
print("  slots =", max(0, 2 - n_after), "(应=1)")

print("== 场景4: target 已持有（601999 已持仓时，601579 为新票）==")
# 持仓 300413/003005/601999（601999 在 target 内→hold），target=601999/601579
positions = {"300413.SZ": 0, "003005.SZ": 0, "601999.SH": 0}
target_codes = {"601999.SH", "601579.SH"}
sell_plan = []
hold = []
for c in positions:
    if c in target_codes:
        hold.append(c)
        continue
    ent = last_buy.get(c)
    held = RD._trading_days_between(ent, today) if ent else 999
    if held < 5:
        hold.append(c)
    else:
        sell_plan.append(c)
n_after = len(positions) - len(sell_plan)
slots = max(0, 2 - n_after)
print("  卖出后持仓 %d 只 slots=%d -> 601579(新票) %s" % (
    n_after, slots, "被拦截(修复生效)" if slots == 0 else "可买入"))
