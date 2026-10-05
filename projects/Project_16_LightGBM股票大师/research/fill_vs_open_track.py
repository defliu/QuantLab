# -*- coding: utf-8 -*-
"""fill-vs-open 每日观测（全量重算）——ENS 执行时点实验的持续数据链。

配套预注册：specs/ENS执行时点实验预注册_20261001.md（判定口径以预注册为准，本脚本只做观测）。
设计：每次运行全量重算两桥全部 fills（幂等，无追加漂移），输出累计 CSV + 最新报告。
只读保证：仅读 state/fills_*.json、guard 锁文件、行情面板（主库+data_live 增量，增量优先）；
不触碰任何交易/账本/plan 文件。无参数，直接运行。

调度：Quant_P16_FillVsOpen_Track（每日 20:45；16:30 增量刷新后当日开盘价已可用）。
产物：
  results/fill_vs_open_track.csv          逐笔累计台账
  results/fill_vs_open_track_latest.md    最新分组报告（基线 vs 实验期）
"""
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402

BRIDGES = [
    ("G2", r"D:\QMT_POOL\g2_bridge", "rebalance_g2_guard_%s.lock"),
    ("ENS", r"D:\QMT_POOL\p16_ensemble_bridge", "rebalance_ens_guard_%s.lock"),
]
SCHED_DIR = os.path.join(PROJ, "data", "schedules")
OUT_CSV = os.path.join(PROJ, "results", "fill_vs_open_track.csv")
OUT_MD = os.path.join(PROJ, "results", "fill_vs_open_track_latest.md")

BASELINE_END = "20260930"     # 基线样本窗口（预注册 §四.5 固定）
EXPERIMENT_START = "20261009"  # 实验期起点（国庆后首个交易日）
REVIEW_BPS = 500.0
ENS_BASELINE_MEAN_ABS = 154.9  # 预注册 §一 基线锚（n=14）
G2_BASELINE_MEAN_ABS = 167.6   # 预注册 §一 基线锚（n=17）


def load_open_lookup(need_keys):
    """主库 + data_live 增量（增量优先）构建 {(yyyymmdd, code): open}。复刻 paper_forward.load_open_panel。"""
    daily = DC.read_main_daily(columns=["open"])
    inc_path = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
    inc_last = None
    if os.path.isfile(inc_path):
        try:
            inc = pd.read_parquet(inc_path)
            if {"trade_date", "ts_code", "open"}.issubset(set(inc.columns)):
                inc = inc[["trade_date", "ts_code", "open"]].copy()
                inc["trade_date"] = pd.to_datetime(inc["trade_date"])
                inc = inc.dropna(subset=["open"])
                inc = inc[inc["open"] > 0]
                inc = inc.set_index(["trade_date", "ts_code"])
                inc = inc[~inc.index.duplicated(keep="last")]
                inc_last = str(sorted(set(inc.index.get_level_values(0)))[-1])[:10]
                daily = pd.concat([daily, inc])
                daily = daily[~daily.index.duplicated(keep="last")]
        except Exception as e:
            print("[WARN] 增量库合并失败，退回主库: %s" % e)
    main_last = str(sorted(set(daily.index.get_level_values(0)))[-1])[:10]

    lookup = {}
    for d, c in need_keys:
        try:
            v = daily.loc[(pd.Timestamp(d), c), "open"]
            v = float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
            lookup[(d.replace("-", ""), c)] = v
        except Exception:
            pass
    return lookup, main_last, inc_last


def collect_fills():
    rows = []
    for tag, bdir, lockpat in BRIDGES:
        for fp in sorted(glob.glob(os.path.join(bdir, "state", "fills_*.json"))):
            date = os.path.basename(fp)[len("fills_"):][:8]
            try:
                with open(fp, encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception as e:
                print("[WARN] 读取失败 %s: %s" % (fp, e))
                continue
            fallback = os.path.exists(os.path.join(SCHED_DIR, lockpat % date))
            for fill in obj.get("fills", []):
                if str(fill.get("status", "")).upper() != "FILLED":
                    continue
                rows.append({
                    "bridge": tag, "date": date,
                    "code": fill.get("code", "?"),
                    "action": str(fill.get("action", "?")).upper(),
                    "vol": int(fill.get("vol", 0)),
                    "price": float(fill.get("price", 0.0)),
                    "ts": fill.get("ts", ""),
                    "fallback": fallback,
                })
    return rows


def period_of(date):
    if date <= BASELINE_END:
        return "baseline"
    if date >= EXPERIMENT_START:
        return "experiment"
    return "gap"


def main():
    rows = collect_fills()
    if not rows:
        print("[WARN] 两桥均无 FILLED 成交记录，报告跳过（首日或数据缺失）")
        return 1
    df = pd.DataFrame(rows)
    df["period"] = df["date"].map(period_of)
    df["date_str"] = df["date"].str.slice(0, 4) + "-" + df["date"].str.slice(4, 6) + "-" + df["date"].str.slice(6, 8)

    lookup, main_last, inc_last = load_open_lookup(set(zip(df["date_str"], df["code"])))
    df["open"] = [lookup.get((d.replace("-", ""), c), np.nan) for d, c in zip(df["date_str"], df["code"])]
    n_missing = int(df["open"].isna().sum())
    dfo = df.dropna(subset=["open"]).copy()
    dfo["dev_bps"] = (dfo["price"] / dfo["open"] - 1.0) * 1e4
    dfo["abs_dev"] = dfo["dev_bps"].abs()
    dfo["mins"] = [max(0.0, (datetime.strptime(t, "%Y-%m-%d %H:%M:%S") - datetime.strptime(d, "%Y-%m-%d")).seconds / 60.0
                   - 9 * 60 - 30.0) if t else np.nan
                   for t, d in zip(dfo["ts"], dfo["date_str"])]
    dfo["review"] = dfo["abs_dev"] > REVIEW_BPS

    # ---- CSV 台账（全量重算覆写，幂等） ----
    out = dfo[["bridge", "date", "code", "action", "vol", "price", "open",
               "dev_bps", "abs_dev", "mins", "ts", "fallback", "period", "review"]].copy()
    out = out.sort_values(["date", "bridge", "code"])
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # ---- 分组报告 ----
    def agg(sub):
        if not len(sub):
            return None
        return dict(n=len(sub),
                    mean_dev=float(sub["dev_bps"].mean()),
                    mean_abs=float(sub["abs_dev"].mean()),
                    med_abs=float(sub["abs_dev"].median()),
                    max_abs=float(sub["abs_dev"].max()),
                    mean_mins=float(sub["mins"].mean()) if sub["mins"].notna().any() else float("nan"))

    groups = []
    for tag, _, _ in BRIDGES:
        for per in ("baseline", "experiment"):
            sub = dfo[(dfo["bridge"] == tag) & (dfo["period"] == per) & (~dfo["fallback"])]
            s = agg(sub)
            if s:
                groups.append(("%s %s" % (tag, per), s))
    n_fb = int(dfo["fallback"].sum())

    L = []
    L.append("# fill-vs-open 观测报告（自动生成，全量重算）\n")
    L.append("> 生成: %s | 面板: 主库 %s / 增量 %s | 判定口径见 specs/ENS执行时点实验预注册_20261001.md\n"
             % (datetime.now().strftime("%Y-%m-%d %H:%M"), main_last, inc_last or "NA"))
    L.append("| 组 | n | mean_dev | mean_abs | med_abs | max_abs | 平均距开盘(分) |")
    L.append("|---|---|---|---|---|---|---|")
    for k, s in groups:
        L.append("| %s | %d | %+.1f | %.1f | %.1f | %.1f | %.0f |"
                 % (k, s["n"], s["mean_dev"], s["mean_abs"], s["med_abs"], s["max_abs"], s["mean_mins"]))
    ens_exp = next((s for k, s in groups if k == "ENS experiment"), None)
    if ens_exp:
        delta = (ens_exp["mean_abs"] / ENS_BASELINE_MEAN_ABS - 1.0) * 100.0
        L.append("\n**实验进度**：ENS 实验期 n=%d，mean_abs=%.1fbps vs 基线 %.1fbps（%+.1f%%，判定阈值 -25%%，见预注册 §三）。"
                 % (ens_exp["n"], ens_exp["mean_abs"], ENS_BASELINE_MEAN_ABS, delta))
    L.append("\n- 兜底成交（guard lock 标记，实验臂主样本排除项）：累计 %d 笔" % n_fb)
    L.append("- open 缺失剔除：%d 笔 | REVIEW 标记（|dev|>%.0fbps，判定前人工裁决）：%d 笔"
             % (n_missing, REVIEW_BPS, int(dfo["review"].sum())))
    latest = dfo["date"].max() if len(dfo) else "NA"
    L.append("- 最新成交日：%s" % latest)
    report = "\n".join(L) + "\n"
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print("[OK] %s\n[OK] %s" % (OUT_CSV, OUT_MD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
