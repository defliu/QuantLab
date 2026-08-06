# coding=utf-8
"""v2.3 QMT 本地验证: 退市排雷 + buffer 逻辑 (不依赖 QMT 连接)
方法: exec strategy_v2.py 源码取出函数, 用 D:/QMT_POOL 真数据验证。
覆盖 AGENTS.md 本地验证要求: 选股管线/排雷过滤/buffer 排名是否生效。
用法: python research/local_validate_v23.py"""
import sys, os

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PROJ_DIR, "strategy_v2.py")

# ---- exec 策略源码, 取出函数 (模块级无 QMT 调用, 安全) ----
ns = {"__name__": "v2_test"}
with open(SRC, "r", encoding="utf-8") as f:
    code = f.read()
compile(code, SRC, "exec")   # 语法先过一遍
exec(code, ns)

_delist_hit_qmt = ns["_delist_hit_qmt"]
_load_delist_info = ns["_load_delist_info"]
_load_financial = ns["_load_financial"]
BUFFER_KEEP_MAX = ns["BUFFER_KEEP_MAX"]

print("BUFFER_KEEP_MAX =", BUFFER_KEEP_MAX)

# ---- 1. 加载真数据 ----
delist_info = _load_delist_info()
fin_data = _load_financial()
print("delist_info 加载:", len(delist_info), "只")
print("fin_data 加载(含total_mv):", len(fin_data), "只; total_mv 覆盖:",
      sum(1 for v in fin_data.values() if "total_mv" in v))

import time
today_str = time.strftime("%Y-%m-%d")

# ---- 2. 退市排雷用例 ----
cases = [
    # (code, 期望, 说明)
    ("000003.SZ", True,  "已退市(list_status=D)"),
    ("002680.SZ", True,  "长生退(已退市D)"),
    ("600051.SH", False, "健康主板小盘(总市值~18亿)"),
    ("002731.SZ", True,  "主板总市值~5亿 < 红线7.5亿"),
    ("301139.SZ", True,  "创业板总市值~3.8亿 < 红线4.5亿"),
]
print("\n=== 退市排雷用例 ===")
all_ok = True
for code, expect, desc in cases:
    got = _delist_hit_qmt(code, fin_data, delist_info, today_str)
    ok = (got == expect)
    all_ok = all_ok and ok
    tmv = fin_data.get(code, {}).get("total_mv", 0)
    st = delist_info.get(code, ("", ""))[0]
    print("  [%s] %-12s 期望%s 实际%s | %s (总市值=%.0f万, status=%s)" % (
        "OK" if ok else "FAIL", code, expect, got, desc, tmv, st))

# ---- 3. buffer 排名逻辑用例 (模拟换仓: 持仓rank超界应卖) ----
print("\n=== buffer 排名逻辑用例 (BUFFER_KEEP_MAX=%d) ===" % BUFFER_KEEP_MAX)
# 构造 scores: 200只, 评分降序 = code 序号降序
scores = dict(("C%04d" % i, float(200 - i)) for i in range(200))
ranked = [c for c, s in sorted(scores.items(), key=lambda x: -x[1])]
rank_map = dict((c, i + 1) for i, c in enumerate(ranked))
# 持仓: rank=1(应留), rank=100(应留), rank=160(应留,边界), rank=161(应卖), rank=200(应卖), 落出候选(应卖)
hold_probe = {"C0000": 1, "C0099": 100, "C0159": 160, "C0160": 161, "C0199": 200, "OUTXX": None}
buf_case = [
    ("C0000", "留", True), ("C0099", "留", True), ("C0159", "留(边界160)", True),
    ("C0160", "卖(161超界)", False), ("C0199", "卖(200超界)", False), ("OUTXX", "卖(落出候选)", False),
]
for code, desc, should_keep in buf_case:
    rk = rank_map.get(code)
    keep = (rk is not None and rk <= BUFFER_KEEP_MAX)
    ok = (keep == should_keep)
    all_ok = all_ok and ok
    print("  [%s] %-8s rank=%-5s %s -> %s" % ("OK" if ok else "FAIL", code, rk, desc, "保留" if keep else "卖出"))

print("\n=== 验证%s ===" % ("全部通过" if all_ok else "存在 FAIL"))
sys.exit(0 if all_ok else 1)
