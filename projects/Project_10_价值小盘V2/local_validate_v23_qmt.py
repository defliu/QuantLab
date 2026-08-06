# coding=utf-8
"""Project 10 V2 v2.3 本地 miniQMT 验证 (不下单)
连本地 miniQMT 实时行情, 跑真实 strategy_v2.py 的选股+退市排雷+buffer 排名管线。
验证: 连接/股票池/评分/排雷过滤/buffer排名/行情可达性 是否通。
用法: C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe local_validate_v23_qmt.py"""
import sys, os, time, json

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
QUANTLAB = os.path.dirname(os.path.dirname(PROJ_DIR))
sys.path.insert(0, PROJ_DIR)
sys.path.insert(0, QUANTLAB)

from broker.local_context import LocalContext, connect_data, load_strategy_source

t0 = time.time()
print("=== V2 v2.3 本地 miniQMT 验证 ===")

# 1. 连接
print("[1] 连接 miniQMT...", end=" ", flush=True)
connect_data()
ctx = LocalContext()
print("OK (%.1fs)" % (time.time() - t0))

# 2. 加载真实策略源码
print("[2] 加载 strategy_v2.py...", end=" ", flush=True)
mod = load_strategy_source(os.path.join(PROJ_DIR, "strategy_v2.py"))
print("OK  BUFFER_KEEP_MAX=%s" % mod.BUFFER_KEEP_MAX)

today = time.strftime("%Y-%m-%d")

# 3. 股票池 (实时)
print("[3] 获取股票池...", end=" ", flush=True)
pool = mod._load_pool(ctx)
print("%d 只 (%.1fs)" % (len(pool), time.time() - t0))
if not pool:
    print("FAIL: 股票池为空, 检查 miniQMT 是否在线"); sys.exit(1)

# 4. CSV 数据
fin_data = mod._load_financial()
delist_info = mod._load_delist_info()
bp_hist = mod._load_bp_history()
ind_map = mod._load_industry_map()
print("[4] CSV: fin=%d delist=%d bp_hist=%d ind=%d (total_mv覆盖=%d)" % (
    len(fin_data), len(delist_info), len(bp_hist), len(ind_map),
    sum(1 for v in fin_data.values() if "total_mv" in v)))

# 5. 评分
print("[5] 评分...", end=" ", flush=True)
scores = mod._score_stocks(ctx, pool, fin_data, bp_hist, ind_map, today)
print("候选 %d 只 (%.1fs)" % (len(scores), time.time() - t0))
if not scores:
    print("FAIL: 无候选"); sys.exit(1)

# 6. 退市排雷过滤
n_before = len(scores)
excluded = [(c, s) for c, s in scores.items() if mod._delist_hit_qmt(c, fin_data, delist_info, today)]
scores_ok = {c: s for c, s in scores.items() if not mod._delist_hit_qmt(c, fin_data, delist_info, today)}
print("[6] 退市排雷: 剔除 %d 只 (剩 %d)" % (len(excluded), len(scores_ok)))
for c, s in sorted(excluded, key=lambda x: -x[1])[:8]:
    info = delist_info.get(c, ("", ""))
    tmv = fin_data.get(c, {}).get("total_mv", 0)
    print("     剔除 %-10s status=%s delist=%s 总市值=%.1f亿" % (c, info[0], info[1] or "-", (tmv or 0) / 10000.0))

# 7. buffer 排名
ranked = [c for c, s in sorted(scores_ok.items(), key=lambda x: -x[1])]
rank_map = {c: i + 1 for i, c in enumerate(ranked)}
print("[7] buffer 排名: 共 %d 只, 保留界=%d" % (len(ranked), mod.BUFFER_KEEP_MAX))

# 当前持仓 (状态文件) -> 演示 buffer 留/卖
state_path = os.path.join(mod.DATA_DIR, "v2_holdings_state.json")
holdings = {}
if os.path.exists(state_path):
    try:
        holdings = json.load(open(state_path, encoding="utf-8")).get("holdings", {})
    except Exception:
        holdings = {}
if holdings:
    keep = [c for c in holdings if rank_map.get(c) is not None and rank_map[c] <= mod.BUFFER_KEEP_MAX]
    sell = [c for c in holdings if not (rank_map.get(c) is not None and rank_map[c] <= mod.BUFFER_KEEP_MAX)]
    print("     当前持仓 %d 只 -> buffer 保留 %d / 卖出 %d" % (len(holdings), len(keep), len(sell)))
    for c in sell[:10]:
        print("       卖 %-10s rank=%s" % (c, rank_map.get(c, "出池")))
else:
    print("     (无历史持仓, 首次建仓场景: buffer 无卖出, 买入 top-%d)" % mod.N_STOCKS)

# 8. 行情可达性 (top5)
top5 = ranked[:5]
print("[8] 实时行情验证 (top5):")
try:
    q = ctx.get_market_data_ex(stock_code=top5, period="1d", count=1)
    for c in top5:
        ok = q and c in q and q[c] is not None and len(q[c]) > 0
        if ok:
            last = q[c].iloc[-1]
            print("     %-10s rank=%-4d close=%.2f  有行情" % (c, rank_map[c], last.get("close", 0)))
        else:
            print("     %-10s rank=%-4d 无行情" % (c, rank_map[c]))
except Exception as e:
    print("     行情拉取失败:", e)

print("\n=== 验证完成 (%.1fs) 选股/排雷/buffer 管线通 ===" % (time.time() - t0))
