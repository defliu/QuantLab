import csv
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

TRADE_LOG = os.path.join(DATA, "qmt_trade_log.csv")
JSON_PATH = os.path.join(DATA, "rebalance_20260930.json")
SEL_PATH = os.path.join(DATA, "selections", "20260930_selection_full.csv")
DATE = "20260930"
DATE_DASH = "2026-09-30"
ACCOUNT = "67014907"

FIELDS = ["time", "code", "side", "vol", "price", "score", "order_id", "account_id"]

fails = []
notes = []


def add(ok, msg):
    if ok:
        print("  PASS  %s" % msg)
    else:
        print("  FAIL  %s" % msg)
        fails.append(msg)


with open(JSON_PATH, encoding="utf-8") as f:
    js = json.load(f)

print("[1] JSON 核心字段")
add(js.get("date") == DATE, "date=%s" % js.get("date"))
add(js.get("executed_live") is True, "executed_live=%s" % js.get("executed_live"))
add(js.get("tier") == 2, "tier=%s" % js.get("tier"))
add(js.get("eff_top") == 5, "eff_top=%s" % js.get("eff_top"))
add(len(js.get("sell") or []) == 2, "sell 条数=%d" % len(js.get("sell") or []))
add(len(js.get("buy") or []) == 5, "buy 条数=%d" % len(js.get("buy") or []))
add(len(js.get("guard_note") or []) == 7, "guard_note 条数=%d（原始未删改）" % len(js.get("guard_note") or []))
add("manual_reconciliation" in js, "含 manual_reconciliation 区块")
corr = (js.get("manual_reconciliation") or {}).get("corrections") or []
add(len(corr) == 1 and corr[0].get("actual_action") == "FILLED",
    "人工校正记录 603237.SH -> FILLED")

print("[2] 候选清单 SHA256")
with open(SEL_PATH, "rb") as f:
    sha = hashlib.sha256(f.read()).hexdigest()
add(sha == js.get("candidate_sha256"), "sha256 一致 %s" % sha[:16])
with open(SEL_PATH, encoding="utf-8-sig", newline="") as f:
    sel = list(csv.DictReader(f))
add(len(sel) == js.get("candidate_rows_raw"),
    "清单原始行数 %d = candidate_rows_raw %s" % (len(sel), js.get("candidate_rows_raw")))
over = [r for r in sel if float(r.get("total") or 0) >= 58]
add(len(over) == js.get("candidate_rows"),
    "过红线(>=58) 行数 %d = candidate_rows %s" % (len(over), js.get("candidate_rows")))
f2src = sorted({r.get("f2_source", "") for r in sel})
add(f2src == ["tushare_20260929"], "F2 来源=%s" % f2src)

print("[3] 成交账本 %s 当日记录" % DATE)
with open(TRADE_LOG, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
day = [r for r in rows if (r.get("time") or "").startswith(DATE_DASH)]
add(len(day) == 7, "当日账本行数=%d（卖2+买5）" % len(day))
add(all((r.get("account_id") or "").strip() == ACCOUNT for r in rows),
    "全表 %d 行账号戳均为 %s" % (len(rows), ACCOUNT))

log_sell = {r["code"]: r for r in day if r["side"] == "SELL"}
log_buy = {r["code"]: r for r in day if r["side"] == "BUY"}
add(sorted(log_sell) == ["603049.SH", "603237.SH"], "账本卖出=%s" % sorted(log_sell))
add(len(log_buy) == 5, "账本买入=%d 只" % len(log_buy))

print("[4] 账本 vs JSON 逐笔核对")
for s in js.get("sell") or []:
    c = s["code"]
    r = log_sell.get(c)
    ok = r is not None and int(r["vol"]) == int(s["vol"]) and r["score"] == s["reason"]
    add(ok, "SELL %s vol=%s reason=%s 账本一致" % (c, s["vol"], s["reason"]))
for b in js.get("buy") or []:
    c = b["code"]
    r = log_buy.get(c)
    ok = (r is not None and int(r["vol"]) == int(b["vol"])
          and abs(float(r["price"]) - float(b["price"])) < 1e-9)
    add(ok, "BUY  %s vol=%s px=%s 账本一致" % (c, b["vol"], b["price"]))

print("[5] 资金勾稽")
pool = js.get("strategy_capital")
each = js.get("target_value_each")
add(abs(pool * 0.95 / 5 - each) < 0.01,
    "资金池 %.2f x 95%% / 5 = %.2f（单只目标市值）" % (pool, each))
cost = sum(int(log_buy[c]["vol"]) * float(log_buy[c]["price"]) for c in log_buy)
add(cost <= pool, "买入成交金额 %.2f <= 资金池 %.2f" % (cost, pool))
add(round(cost, 2) > 0, "买入成交金额 = %.2f" % cost)

print("[6] 603237.SH 特别核对")
r = log_sell.get("603237.SH")
if r is None:
    add(False, "补记行缺失")
else:
    add(int(r["vol"]) == 2800, "补记行存在且 vol=2800")
    add(re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", r["time"]) is not None,
        "时间格式完整：%s" % r["time"])
    add(r["account_id"] == ACCOUNT, "账号戳=%s" % r["account_id"])
    add(abs(float(r["price"]) - 15.2418) < 1e-9, "成交价=%s" % r["price"])
guard = [g for g in js.get("guard_note") or [] if "603237" in g]
add(bool(guard) and "CANCELED_TIMEOUT" in guard[0],
    "guard_note 保留原始误判：%s" % (guard[0] if guard else "缺失"))

print("[7] 备份文件")
bak = JSON_PATH + ".scriptorig"
add(os.path.exists(bak), "脚本原始备份存在（%s）" % os.path.basename(bak))
md = os.path.join(DATA, "rebalance_%s.md" % DATE)
add(os.path.exists(md), "Markdown 报告存在")
if os.path.exists(md):
    with open(md, encoding="utf-8") as f:
        text = f.read()
    for key in ["67014907", "T=2", "eff_top=5", "603237.SH", "F2 主源 = Tushare T-1 本地",
                "1090531913", "manual_reconciliation", "603083.SH", "002237.SZ"]:
        add(key in text, "md 含关键要素 %s" % key)

print("[8] 调用项目自身函数复算持仓（验证补记行落末尾不改变结果）")
sys.path.insert(0, BASE)
try:
    import rebalance_daily as RD
    pos, volumes, sellable = RD.load_positions_log()
    add(sorted(volumes) == ["000155.SZ", "000920.SZ", "300531.SZ", "600547.SH", "603882.SH"],
        "净持仓 5 只 = %s" % sorted(volumes))
    add("603237.SH" not in volumes, "603237.SH 已不在净持仓（BUY2800-SELL2800=0）")
    add("603049.SH" not in volumes, "603049.SH 已不在净持仓（BUY900-SELL900=0）")
    add(volumes.get("000920.SZ") == 1300 and volumes.get("300531.SZ") == 1100,
        "买入量正确 000920=1300 / 300531=1100")
    add(all(sellable.get(c) == 0 for c in volumes),
        "5 只均为当日买入 -> T+1 锁定 sellable=0（符合预期）")
except Exception as e:
    add(False, "复算持仓异常: %r" % e)

print("")
if fails:
    print("[RESULT] FAIL x%d" % len(fails))
    for m in fails:
        print("  - %s" % m)
    sys.exit(1)
print("[RESULT] ALL PASS")
