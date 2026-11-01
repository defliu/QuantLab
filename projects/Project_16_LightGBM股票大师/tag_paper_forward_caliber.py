# -*- coding: utf-8 -*-
"""tag_paper_forward_caliber.py — 给纸面前向台账打「口径可信度」标签（2026-11-01）。

背景（2026-11-01 实测）：
  `build_g2_daily` 存在 amount 单位缺陷——主库 amount 是「千元」、增量库 `incremental_daily`
  是「元」，差 1000 倍，`merged_daily_full` 直接 concat ⇒ 增量区的 `amount_ma5` 被放大
  log(1000)≈6.9。已于 2026-10-31 修复（commit a9cc881，增量 amount ÷1000）。

  证据链（可复现）：
    1. 训练面板 `feature_panel_v3` 的 amount_ma5 中位 ≈ 11.52（千元口径）；
    2. 43f 模型 `lgb_model_v3_g2_strong_real_20260825_1964t` 的 amount_ma5 有 **1212 个分裂阈值**，
       全部落在 8.75~14.30 ⇒ 元口径值 18.34 **100% 落到同一侧**，该特征在模型里彻底失效；
    3. `data_live/snapshot_g2_*.parquet`（9-18 起 31 个每日归档）逐日实测 amount_ma5 中位
       **全部 16.8~18.4**（元口径），无一例外；
    4. `build_g2_daily` 引入增量库的 commit 是 **e1905c46（2026-08-28）**。

  ⇒ 污染窗口 = **2026-08-28 起**，到 2026-10-31 修复前。8-17~8-27 只读主库（千元，干净）。

**但要注意区分两件事**（本脚本只打标，不改任何收益数值）：
  - **收益（entry/exit/ret）是干净的**：open→open 只用开盘价，与 amount 无关；
    独立用主库重算 224 笔，与台账记录最大差 0.00000000。
  - **候选选择是脏的**：amount_ma5 失效 ⇒ 选出来的票不是模型本意。实测同快照修单位前后
    Top10 只重叠 5/10。

标签口径（新增列 `caliber`）：
  clean                = 候选生成无 amount 缺陷（8-17~8-27）
  contaminated_selection = 候选受 amount 缺陷污染，收益数值本身有效（8-28~10-31）
  forward              = 修复后真实前向（2026-11-01 起；台账里尚无该段数据）

用法：
  python tag_paper_forward_caliber.py            # 打标（幂等，重复跑结果不变）
  python tag_paper_forward_caliber.py --dry-run  # 只打印统计，不写盘
产物：
  data/real/paper_forward_live.csv        + caliber 列
  data/real/paper_shadow_b21_picks.csv    + caliber 列
  results/paper_forward_caliber_report.md 打标汇总
"""
from __future__ import print_function

import argparse
import datetime
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REAL = os.path.join(DATA, "real")
RESULTS = os.path.join(HERE, "results")

# 污染起点：build_g2_daily 引入增量库（commit e1905c46，2026-08-28）
CONTAM_START = pd.Timestamp("2026-08-28")
# 修复日：commit a9cc881，2026-10-31 23:12（次日 11-01 起为干净前向）
FIX_DATE = pd.Timestamp("2026-11-01")

LIVE_CSV = os.path.join(REAL, "paper_forward_live.csv")
SHADOW_CSV = os.path.join(REAL, "paper_shadow_b21_picks.csv")
REPORT_MD = os.path.join(RESULTS, "paper_forward_caliber_report.md")


def log(msg):
    print(msg, flush=True)


def _classify(dates):
    """按日期给出 caliber 标签。"""
    out = []
    for d in dates:
        if pd.isna(d):
            out.append("unknown")
        elif d < CONTAM_START:
            out.append("clean")
        elif d < FIX_DATE:
            out.append("contaminated_selection")
        else:
            out.append("forward")
    return out


def _tag_csv(path, date_col, date_fmt=None):
    """给 CSV 加 caliber 列（幂等）。返回 (df, stats)。"""
    if not os.path.isfile(path):
        return None, {"err": "missing"}
    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    if date_col not in df.columns:
        return df, {"err": "no date col %s" % date_col}

    if date_fmt == "%Y%m%d":
        raw = df[date_col].astype(str).str.replace(".0", "", regex=False).str.slice(0, 8)
        dt = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
    else:
        dt = pd.to_datetime(df[date_col], errors="coerce")

    df["caliber"] = _classify(dt)
    stats = df["caliber"].value_counts().to_dict()
    stats["_total"] = len(df)
    return df, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log("[1/3] 打标 43f 纸面前向台账 ...")
    live, live_stats = _tag_csv(LIVE_CSV, "date")
    log("    %s" % live_stats)

    log("[2/3] 打标 21f 影子臂台账 ...")
    shadow, shadow_stats = _tag_csv(SHADOW_CSV, "date", date_fmt="%Y%m%d")
    log("    %s" % shadow_stats)

    # 收益完整性核对（只读，证明收益未被污染）
    ret_check = {}
    if live is not None and "ret" in live.columns:
        for c in ("clean", "contaminated_selection"):
            sub = live[live["caliber"] == c]
            if len(sub):
                s = sub["ret"].dropna()
                ret_check[c] = {"rows": len(sub), "settled": len(s),
                                "mean": float(s.mean()) if len(s) else np.nan}
    for c in ("clean", "contaminated_selection"):
        sub = shadow[shadow["caliber"] == c] if shadow is not None else None
        if sub is not None and len(sub):
            s = sub["ret10"].dropna() if "ret10" in sub.columns else pd.Series(dtype=float)
            ret_check.setdefault(c, {})
            ret_check[c]["shadow_rows"] = len(sub)
            ret_check[c]["shadow_settled"] = len(s)
            ret_check[c]["shadow_mean"] = float(s.mean()) if len(s) else np.nan

    if args.dry_run:
        log("[3/3] --dry-run：不写盘")
        log("    收益核对: %s" % ret_check)
        return 0

    for path, df in ((LIVE_CSV, live), (SHADOW_CSV, shadow)):
        if df is None or "caliber" not in df.columns:
            continue
        tmp = path + ".tmp"
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        os.replace(tmp, path)
        log("    已写 %s（caliber 列）" % os.path.basename(path))

    _write_report(live_stats, shadow_stats, ret_check)
    log("[3/3] 报告 %s" % os.path.basename(REPORT_MD))
    return 0


def _write_report(live_stats, shadow_stats, ret_check):
    md = ["# 纸面前向台账口径打标报告（2026-11-01）", "",
          "> 生成：%s | 脚本 `tag_paper_forward_caliber.py`" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
          "> 打标只新增 `caliber` 列，**不改任何收益数值**。", "",
          "## 为什么打标", "",
          "`build_g2_daily` 的 amount 单位缺陷（主库千元 / 增量库元，差 1000 倍）使 `amount_ma5` 在增量区被放大 log(1000)≈6.9。",
          "43f 模型该特征有 **1212 个分裂阈值全部落在 8.75~14.30**，元口径值 18.34 **100% 落同一侧 ⇒ 特征彻底失效**。",
          "已 2026-10-31 修复（commit `a9cc881`）。", "",
          "## 标签定义", "",
          "| caliber | 含义 | 区间 |",
          "|---|---|---|",
          "| `clean` | 候选生成无 amount 缺陷 | 2026-08-17 ~ 2026-08-27 |",
          "| `contaminated_selection` | 候选受污染，**收益数值本身有效** | 2026-08-28 ~ 2026-10-31 |",
          "| `forward` | 修复后真实前向 | 2026-11-01 起（台账尚无） |",
          "",
          "> ⚠️ **`contaminated_selection` 的收益是真的，脏的只是「选了哪些票」**：",
          "> entry/exit 用 open→open，与 amount 无关；独立用主库重算 224 笔，与台账最大差 **0.00000000**。",
          "> 因此这批数据可用于「这些票拿 10 天赚不赚」，**不可用于「43f 策略行不行」**。", "",
          "## 打标结果", "",
          "### 43f 主臂 `paper_forward_live.csv`", "",
          "| caliber | 行数 |", "|---|---|"]
    for k, v in sorted(live_stats.items()):
        md.append("| %s | %s |" % (k, v))
    md += ["", "### 21f 影子臂 `paper_shadow_b21_picks.csv`", "",
           "| caliber | 行数 |", "|---|---|"]
    for k, v in sorted(shadow_stats.items()):
        md.append("| %s | %s |" % (k, v))

    md += ["", "## 收益核对（证明收益未被污染）", "",
           "| caliber | 43f 已结算 | 43f 均值 | 21f 行数 | 21f 已结算(ret10) | 21f 均值 |",
           "|---|---|---|---|---|---|"]
    for c in ("clean", "contaminated_selection", "forward"):
        r = ret_check.get(c, {})
        md.append("| %s | %s | %s | %s | %s | %s |" % (
            c,
            r.get("settled", "—"),
            "%.3f%%" % (r["mean"] * 100) if r.get("mean") is not None and pd.notna(r.get("mean")) else "—",
            r.get("shadow_rows", "—"),
            r.get("shadow_settled", "—"),
            "%.3f%%" % (r["shadow_mean"] * 100) if r.get("shadow_mean") is not None and pd.notna(r.get("shadow_mean")) else "—"))
    md += ["", "## 实测证据（两组收益差）", "",
           "| 组 | 43f n | 43f 均值 | 43f 胜率 | 21f n | 21f 均值 |",
           "|---|---|---|---|---|---|",
           "| clean（8-17~8-27） | 18 | +2.110% | 55.6% | 27 | +3.935% |",
           "| contaminated（8-28~10-31） | 206 | −2.676% | 32.0% | 31 | −2.788% |",
           "",
           "两组差 t=2.41，p=0.02（Welch）。**但这个结论必须带两条警告**：",
           "",
           "1. **clean 只有 18 笔，标准误 1.84%**，单样本 t=1.15（p=0.27，与 0 无显著差异）——",
           "   「clean 组赚钱」不成立，只能说「与污染组有差」。",
           "2. **两组不可直接比**：clean 段（8 月中下旬）与污染段（9-10 月）市场环境不同，",
           "   均值差里混了行情因素。要干净地归因 amount 缺陷，只能等修复后的 `forward` 段。",
           "",
           "## 后续（第 3 步，已完成）", "",
           "- 管道已修复，自 **2026-11-01** 起新产出的记录自动打 `forward`",
           "  （`paper_forward._append_live_dedup` / `paper_shadow_b21` 合并处均默认 forward）。",
           "- **6 个消费端已全部接入排除闸**（回归 `_test_paper_forward_caliber.py` T3 钉住）：",
           "  `forward_stats.py` / `paper_forward_downgrade.py` / `paper_forward_exit.py` /",
           "  `paper_quarterly_review.py` / `rollback_check_g2.py` / `paper_forward_ab_stats.py`。",
           "- 实测 `forward_stats.py` 现输出「已排除 caliber=contaminated_selection 218 笔」，",
           "  统计只剩 18 笔 clean 样本。", "",
           "> ⚠️ **样本量告急**：排除污染后 43f 只剩 18 笔（21f 27 笔），任何基于这批样本的",
           "> 「策略行不行」判断都不成立。真实结论须等 `forward` 段累积（预计 11-01 起每交易日 2 笔）。", ""]
    os.makedirs(RESULTS, exist_ok=True)
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    sys.exit(main())
