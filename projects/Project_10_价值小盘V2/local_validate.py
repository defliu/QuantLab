# coding=utf-8
"""Project 10 V2 本地验证脚本
连本地 miniQMT，用实时行情 + CSV 财务数据跑 V2 评分管线。
用法: C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe local_validate.py"""
import sys
import os
import time

# 路径设置
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
QUANTLAB = os.path.dirname(os.path.dirname(PROJ_DIR))  # QuantLab 根
sys.path.insert(0, PROJ_DIR)
sys.path.insert(0, QUANTLAB)

from broker.local_context import LocalContext, connect_data, load_strategy_source
from strategy.scoring import V2Scorer

# ============ 配置 ============
POOL_DIR = r"D:\QMT_POOL"
DATA_DIR = r"E:/astock"
N_SHOW = 20  # 显示前N只


def load_csv_dict(filename, key_col, val_col):
    """加载CSV为 {key: val} 字典"""
    import csv
    path = os.path.join(POOL_DIR, filename)
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            k = row.get(key_col, "")
            v = row.get(val_col, "")
            if k:
                try:
                    result[k] = float(v) if val_col != "industry" else v
                except (ValueError, TypeError):
                    pass
    return result


def main():
    t0 = time.time()

    # 1. 连接 miniQMT
    print("连接 miniQMT...", end=" ", flush=True)
    connect_data()
    ctx = LocalContext()
    print("OK (%.1fs)" % (time.time() - t0))

    # 2. 获取全市场股票列表
    print("获取股票列表...", end=" ", flush=True)
    pool = ctx.get_stock_list_in_sector("沪深A股")
    print("%d 只 (%.1fs)" % (len(pool), time.time() - t0))

    # 3. 加载 CSV 财务数据
    print("加载 CSV 财务数据...", end=" ", flush=True)
    pb_dict = load_csv_dict("financial_pb.csv", "ts_code", "value")
    pe_dict = load_csv_dict("financial_pe_ttm.csv", "ts_code", "value")
    mv_dict = load_csv_dict("financial_circ_mv.csv", "ts_code", "value")
    ind_dict = load_csv_dict("industry_map.csv", "ts_code", "industry")
    print("PB=%d PE=%d MV=%d IND=%d (%.1fs)" % (
        len(pb_dict), len(pe_dict), len(mv_dict), len(ind_dict), time.time() - t0))

    # 4. 从 xtdata 拉最新行情（验证数据可达性）
    print("拉取最新行情...", end=" ", flush=True)
    sample = pool[:500]  # 先拉500只验证
    try:
        quotes = ctx.get_market_data_ex(stock_code=sample, period="1d", count=1)
        n_ok = len([k for k, v in (quotes or {}).items() if v is not None and len(v) > 0])
        print("%d/%d 有行情 (%.1fs)" % (n_ok, len(sample), time.time() - t0))
    except Exception as e:
        print("FAIL: %s" % e)
        quotes = {}

    # 5. V2 评分
    print("运行 V2 评分...", end=" ", flush=True)

    # 行业映射
    import pandas as pd
    ind_map = {k: v if v else "其他" for k, v in ind_dict.items()}

    # 评分器
    scorer = V2Scorer(ind_map=ind_map, z_weight=0.8, hp_weight=0.2)

    # 构造候选股评分（用CSV的PB值）
    scores = {}
    for code in pool:
        pb = pb_dict.get(code, 0)
        pe = pe_dict.get(code, 0)
        mv = mv_dict.get(code, 0)
        # 过滤：PB>0, PE>0, 市值<30亿
        if pb <= 0 or pe <= 0 or mv <= 0 or mv >= 300000:
            continue
        bp = 1.0 / pb
        ind = ind_map.get(code, "其他")
        scores[code] = {"bp": bp, "pb": pb, "pe": pe, "mv": mv, "ind": ind}

    print("候选 %d 只 (%.1fs)" % (len(scores), time.time() - t0))

    if not scores:
        print("无候选股，退出")
        return

    # 行业中性 z-score
    import numpy as np
    df = pd.DataFrame(scores).T
    df["bp"] = df["bp"].astype(float)
    df["ind"] = df["ind"].astype(str)
    df["z"] = df.groupby("ind")["bp"].transform(
        lambda x: (x - x.mean()) / (x.std() + 1e-9)
    )
    # 简化：用 z-score 作为评分（hp 需要历史数据，本地验证先跳过）
    df["score"] = df["z"] * 0.8  # 只用 z 分量
    df = df.sort_values("score", ascending=False)

    # 6. 输出结果
    dt = time.time() - t0
    print("\n=== V2 本地验证结果 (耗时 %.1fs) ===" % dt)
    print("全市场 %d 只 → 候选 %d 只 → Top %d:" % (len(pool), len(scores), N_SHOW))
    print("-" * 70)
    print("%-12s %-8s %-8s %-10s %-10s %-6s %s" % (
        "代码", "BP", "PB", "市值(万)", "z-score", "行业", "评分"))
    print("-" * 70)

    for code, row in df.head(N_SHOW).iterrows():
        mv_display = row["mv"] if row["mv"] < 10000 else "%.1f亿" % (row["mv"] / 10000)
        print("%-12s %-8.3f %-8.2f %-10s %-10.4f %-6s %.4f" % (
            code, row["bp"], row["pb"], mv_display, row["z"], row["ind"], row["score"]))

    # 7. 验证 xtdata 行情是否能取到
    print("\n--- xtdata 行情验证 ---")
    top_codes = list(df.head(5).index)
    try:
        q = ctx.get_market_data_ex(stock_code=top_codes, period="1d", count=3)
        for code in top_codes:
            if q and code in q and q[code] is not None and len(q[code]) > 0:
                last = q[code].iloc[-1]
                print("  %s close=%.2f vol=%.0f" % (code, last.get("close", 0), last.get("volume", 0)))
            else:
                print("  %s 无行情" % code)
    except Exception as e:
        print("  行情拉取失败:", e)

    print("\n=== 验证完成 ===")


if __name__ == "__main__":
    main()
