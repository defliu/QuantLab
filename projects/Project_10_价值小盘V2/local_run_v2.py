# coding=utf-8
"""Project 10 V2 真实源码本地验证（工作流第4节模式）
import strategy_v2.py 部署源码本身（含GBK头/UTF-8编码处理），
用 LocalContext 映射 C.* 到本地 xtdata，跑真实 _load_pool/_score_stocks。
用法: C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe local_run_v2.py"""
import os
import sys
import time

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
QUANTLAB = os.path.dirname(os.path.dirname(PROJ_DIR))
sys.path.insert(0, PROJ_DIR)
sys.path.insert(0, QUANTLAB)

from broker.local_context import LocalContext, connect_data, load_strategy_source

# ============ 1. 连接本地 miniQMT ============
t0 = time.time()
print("连接 miniQMT...", end=" ", flush=True)
connect_data()
ctx = LocalContext()
print("OK (%.1fs)" % (time.time() - t0))

# ============ 2. 加载部署源码真实函数（处理 GBK头/UTF-8 编码坑） ============
src = os.path.join(PROJ_DIR, "strategy_v2.py")
S = load_strategy_source(src)
print("源码加载 OK: %s (%d 行)" % (os.path.basename(src), len(open(src, "rb").read().splitlines())))

# ============ 3. 真实 _load_pool(C)：QMT实时股票池 ============
pool = S._load_pool(ctx)
print("_load_pool(ctx) -> %d 只 (%.1fs)" % (len(pool), time.time() - t0))
assert len(pool) > 3000, "股票池异常: %d" % len(pool)

# ============ 4. 真实 CSV 加载函数 ============
fin_data = S._load_financial()
bp_hist = S._load_bp_history()
ind_map = S._load_industry_map()
print("CSV加载: financial=%d 只, bp_hist=%d 只, industry=%d 只 (%.1fs)"
      % (len(fin_data), len(bp_hist), len(ind_map), time.time() - t0))

# ============ 5. 真实 _score_stocks 评分 ============
today = time.strftime("%Y-%m-%d")
scores = S._score_stocks(ctx, pool, fin_data, bp_hist, ind_map, today)
print("_score_stocks -> 有评分 %d 只 (%.1fs)" % (len(scores), time.time() - t0))
assert len(scores) > 100, "评分候选异常: %d" % len(scores)

# ============ 6. 输出 Top20 ============
print("\n=== V2 真实源码本地验证 (耗时 %.1fs) ===" % (time.time() - t0))
print("全市场 %d 只 -> 有评分 %d 只 -> Top 20:" % (len(pool), len(scores)))
print("-" * 60)
ranked = sorted(scores.items(), key=lambda x: -x[1])
for code, sc in ranked[:20]:
    fd = fin_data.get(code, {})
    print("%-12s score=%.4f  PB=%.2f  行业=%s"
          % (code, sc, fd.get("pb", 0), ind_map.get(code, "?")))
print("-" * 60)
print("换仓取前 %d 只（等权，每只≈资金池/%d）" % (S.N_STOCKS, S.N_STOCKS))
print("\n=== 验证完成 ===")
