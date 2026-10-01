# -*- coding: utf-8 -*-
"""V1.3 盘中重排 vs 回测口径的重合度实证研究（可复现脚本）

研究问题（2026-09-29 调研，详见 specs/V13盘中重排裁决与执行架构研究_20260929.md）：
  实盘 09:45 review_full 重排（含 T 日盘中 F5 / 新闻 F3）相对回测口径
  （scorecard_real 全 T-1 数据 + 面板代理 F1/F3/F4）到底换掉多少最终名单？

数据配对规则（文件命名即口径）：
  - data/selections/<T-1>_model_top10.csv ：T-1 16:30 产出，列含 combo（回测口径的
    LightGBM combo 排名，T-1 面板分），trade_date 列 = T-1。
  - data/selections/<T>_selection_full.csv ：T 日 09:45 review_full 重排产物，
    列含 total（F1*25%+F2*20%+F3*20%+F4*15%+F5*10%+F6*10%，x10 百分制）。
  - 每个 selection_full(T) 与「日期严格小于 T 的最近一份 model_top10」配对。

指标：
  - subset_ok : selection_full 的 ts_code 是否为 model_top10 的子集（不成立=当日
    model_top10 被 20:12 复跑覆写等历史口径污染，重合度仅供参考）。
  - combo_top5 vs full_top5 重合数（并列时按 total/combo 降序 + ts_code 稳定排序）。
  - n58 : selection_full 中 total >= 58（rebalance_daily REDLINE）的行数，
    n58==0 即当日红线空仓。

用法（研究环境 Python 3.11，stdlib-only）：
  python research/v13_rerank_overlap_study.py [--dir data/selections] [--topn 5]
"""
import argparse
import csv
import glob
import os
import re
import sys
from datetime import datetime

MODEL_RE = re.compile(r"^(\d{8})_model_top10\.csv$")
FULL_RE = re.compile(r"^(\d{8})_selection_full\.csv$")


def read_csv_rows(path):
    """读 CSV 为 list[dict]，跳过坏行。"""
    rows = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for rec in csv.DictReader(f):
            if rec.get("ts_code"):
                rows.append(rec)
    return rows


def to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def top_n_codes(rows, key, topn):
    """按 key 列降序取 topn 个 ts_code（并列时 ts_code 升序稳定）。"""
    scored = []
    for r in rows:
        v = to_float(r.get(key))
        if v is not None:
            scored.append((v, r["ts_code"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scored[:topn]], len(scored)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join("data", "selections"),
                    help="selections 目录（默认 data/selections）")
    ap.add_argument("--topn", type=int, default=5, help="对比 top-N（默认 5）")
    ap.add_argument("--redline", type=float, default=58.0,
                    help="rebalance_daily REDLINE（默认 58.0）")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print("[SKIP] selections dir not found: %s" % args.dir)
        return 0

    model_files = {}   # date -> path
    full_files = {}    # date -> path
    for p in glob.glob(os.path.join(args.dir, "*.csv")):
        name = os.path.basename(p)
        m = MODEL_RE.match(name)
        if m:
            model_files[m.group(1)] = p
            continue
        m = FULL_RE.match(name)
        if m:
            full_files[m.group(1)] = p

    if not full_files:
        print("[SKIP] no selection_full files")
        return 0

    model_dates = sorted(model_files)
    print("== V1.3 rerank vs backtest-aligned (combo) overlap study ==")
    print("selections dir : %s" % os.path.abspath(args.dir))
    print("model_top10 files: %d, selection_full files: %d, topN=%d, redline=%.1f"
          % (len(model_files), len(full_files), args.topn, args.redline))
    print()

    header = "%-10s %-10s %-6s %-8s %-8s %-9s %s" % (
        "full(T)", "model(T-1)", "rows", "n58", "overlap", "churn%", "subset_ok")
    print(header)
    print("-" * len(header))

    overlaps, churns, n58_list, bad_days = [], [], [], []
    for t_date in sorted(full_files):
        # 配对：日期严格小于 T 的最近一份 model_top10
        prior = [d for d in model_dates if d < t_date]
        if not prior:
            print("%-10s %-10s %-6s %-8s %-8s %-9s %s" % (
                t_date, "-", "-", "-", "-", "-", "(no model file)"))
            continue
        m_date = prior[-1]

        m_rows = read_csv_rows(model_files[m_date])
        f_rows = read_csv_rows(full_files[t_date])

        combo_top, m_n = top_n_codes(m_rows, "combo", args.topn)
        full_top, f_n = top_n_codes(f_rows, "total", args.topn)

        m_codes = set(r["ts_code"] for r in m_rows)
        f_codes = set(r["ts_code"] for r in f_rows)
        subset_ok = f_codes.issubset(m_codes)

        n58 = sum(1 for r in f_rows
                  if (to_float(r.get("total")) or float("nan")) >= args.redline)

        base = min(len(combo_top), len(full_top))
        overlap = len(set(combo_top) & set(full_top)) if base else 0
        churn = (1.0 - overlap / base) if base else float("nan")

        overlaps.append(overlap)
        churns.append(churn)
        n58_list.append(n58)
        flag = "" if subset_ok else "  <-- 非子集(历史覆写污染)"
        if not subset_ok:
            bad_days.append(t_date)
        print("%-10s %-10s %-6d %-8d %-8d %-9s %s%s" % (
            t_date, m_date, f_n, n58, overlap,
            ("%.0f%%" % (churn * 100)) if churn == churn else "-",
            "Y" if subset_ok else "N", flag))

    print("-" * len(header))
    n = len(overlaps)
    if n == 0:
        print("[SKIP] no valid pairs")
        return 0
    mean_ov = sum(overlaps) / n
    mean_churn = sum(churns) / n
    n58_zero = sum(1 for x in n58_list if x == 0)
    print()
    print("== SUMMARY ==")
    print("valid pairs            : %d (bad/non-subset days: %s)"
          % (n, ", ".join(bad_days) if bad_days else "none"))
    print("mean overlap (top%d)   : %.2f / %d" % (args.topn, mean_ov, args.topn))
    print("mean churn             : %.1f%%  (rerank 每日置换最终名单比例)"
          % (mean_churn * 100))
    print("redline-empty days     : %d / %d  (n58==0, 当日红线空仓)"
          % (n58_zero, n))
    print("n58 distribution       : min=%d max=%d mean=%.1f"
          % (min(n58_list), max(n58_list), sum(n58_list) / n))
    print()
    print("口径提示: combo_top 是回测可验证口径（T-1 面板，scan_rotate_cost_real"
          " 同源）；full_top 含 T 日盘中 F5 / 新闻 F3（回测从未验证）。")
    print("generated: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
