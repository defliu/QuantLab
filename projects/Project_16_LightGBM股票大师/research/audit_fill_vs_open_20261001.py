# -*- coding: utf-8 -*-
"""基线审计：G2/ENS 桥成交价 vs 同日开盘价偏差（fill-vs-open）——ENS 执行时点实验前置。

背景（2026-10-01 立项）：实验「动 ENS（09:52→09:35）留 G2（09:50）」需要实验前基线，
量化当前 ~09:50-10:00 时段成交相对同日开盘价的偏差分布，作为 15 交易日对照的基线臂。

只读保证：仅读两桥 state/fills_*.json 与行情面板（主库+data_live 增量，增量优先，
复刻 paper_forward.load_open_panel 逻辑但不 import paper_forward——避免拉起模型链），
不写任何交易/账本文件。唯一产物为 results/ 下的审计报告。

口径：
  - 只统计 status=FILLED 的成交（废单/撤单不入样本）
  - dev_bps = (fill_price / open - 1) * 1e4（open 为未复权开盘价，与 QMT 成交价同口径）
  - BUY:  dev>0 = 高于开盘买入（买贵，成本）；SELL: dev>0 = 高于开盘卖出（卖高，有利）
  - abs_dev 为实验目标量：执行时点越贴近开盘、且避开集合竞价最宽价差，abs_dev 应收窄
  - 分钟数 = 成交时间戳距 09:30:00 的分钟数（刻画当前成交时点分布）
  - |dev|>500bps 单笔标记 [REVIEW]（可能除权口径错位/极端滑点，人工核对）
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
    ("G2", r"D:\QMT_POOL\g2_bridge"),
    ("ENS", r"D:\QMT_POOL\p16_ensemble_bridge"),
]
OUT_MD = os.path.join(PROJ, "results", "fill_vs_open_baseline_20261001.md")
REVIEW_BPS = 500.0


def load_open_lookup(need_keys):
    """主库 + data_live 增量（增量优先）构建 {(date, code): open} 查找表，只取需要的键。"""
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
    print("数据源: 主库最新 %s | 增量最新 %s" % (main_last, inc_last or "NA"))

    keys = [(pd.Timestamp(d), c) for (d, c) in need_keys]
    lookup = {}
    for k in keys:
        try:
            v = daily.loc[k, "open"]
            v = float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
            lookup[(str(k[0])[:10].replace("-", ""), k[1])] = v
        except Exception:
            pass
    return lookup, main_last, inc_last


def main():
    # ---------- 1) 收集两桥全部 FILLED 成交 ----------
    rows = []
    for tag, bdir in BRIDGES:
        for fp in sorted(glob.glob(os.path.join(bdir, "state", "fills_*.json"))):
            date = os.path.basename(fp)[len("fills_"):][:8]
            try:
                with open(fp, encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception as e:
                print("[WARN] 读取失败 %s: %s" % (fp, e))
                continue
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
                })
    if not rows:
        print("[ABORT] 两桥均无 FILLED 成交记录")
        return 1
    df = pd.DataFrame(rows)
    df["date_str"] = df["date"].str.slice(0, 4) + "-" + df["date"].str.slice(4, 6) + "-" + df["date"].str.slice(6, 8)

    # ---------- 2) 对齐开盘价 ----------
    need = set(zip(df["date_str"], df["code"]))
    lookup, main_last, inc_last = load_open_lookup(need)
    df["open"] = [lookup.get((d.replace("-", ""), c), np.nan) for d, c in zip(df["date_str"], df["code"])]
    n_missing = int(df["open"].isna().sum())
    dfo = df.dropna(subset=["open"]).copy()
    dfo["dev_bps"] = (dfo["price"] / dfo["open"] - 1.0) * 1e4
    dfo["abs_dev"] = dfo["dev_bps"].abs()
    dfo["mins"] = [max(0.0, (datetime.strptime(t, "%Y-%m-%d %H:%M:%S") - datetime.strptime(d, "%Y-%m-%d")).seconds / 60.0
                   - 9 * 60 - 30.0) if t else np.nan
                   for t, d in zip(dfo["ts"], dfo["date_str"])]

    # ---------- 3) 聚合 ----------
    def agg(sub):
        return {
            "n": len(sub),
            "mean_dev": float(sub["dev_bps"].mean()),
            "mean_abs": float(sub["abs_dev"].mean()),
            "med_abs": float(sub["abs_dev"].median()),
            "max_abs": float(sub["abs_dev"].max()),
            "mean_mins": float(sub["mins"].mean()) if sub["mins"].notna().any() else float("nan"),
        }

    stats = {}
    for tag, _ in BRIDGES:
        for act in ("BUY", "SELL"):
            sub = dfo[(dfo["bridge"] == tag) & (dfo["action"] == act)]
            if len(sub):
                stats["%s %s" % (tag, act)] = agg(sub)
        sub_all = dfo[dfo["bridge"] == tag]
        if len(sub_all):
            stats["%s 合计" % tag] = agg(sub_all)
    stats["全部"] = agg(dfo)

    # ---------- 4) 输出 ----------
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    L = []
    L.append("# G2/ENS fill-vs-open 基线审计（2026-10-01）\n")
    L.append("> 用途：ENS 执行时点实验（09:52→09:35）实验前基线臂。只读审计，不触碰交易文件。")
    L.append("> 数据源：主库最新 %s | 增量最新 %s | 口径：FILLED 成交价 vs 同日未复权开盘价，bps。\n"
             % (main_last, inc_last or "NA"))
    L.append("## 逐笔明细（n=%d，open 缺失跳过 %d 笔）\n" % (len(dfo), n_missing))
    L.append("| 桥 | 日期 | 代码 | 方向 | 股数 | 成交价 | 开盘价 | dev(bps) | 成交时刻 | 距开盘(分) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, r in dfo.sort_values(["date", "bridge", "code"]).iterrows():
        flag = " **[REVIEW]**" if r["abs_dev"] > REVIEW_BPS else ""
        L.append("| %s | %s | %s | %s | %d | %.3f | %.3f | %+.1f%s | %s | %.0f |"
                 % (r["bridge"], r["date_str"], r["code"], r["action"], r["vol"],
                    r["price"], r["open"], r["dev_bps"], flag, r["ts"], r["mins"]))
    L.append("\n## 聚合统计（bps）\n")
    L.append("| 组 | n | mean_dev | mean_abs | med_abs | max_abs | 平均距开盘(分) |")
    L.append("|---|---|---|---|---|---|---|")
    for k, s in stats.items():
        L.append("| %s | %d | %+.1f | %.1f | %.1f | %.1f | %.0f |"
                 % (k, s["n"], s["mean_dev"], s["mean_abs"], s["med_abs"], s["max_abs"], s["mean_mins"]))
    L.append("\n## 读法与实验对照口径\n")
    L.append("- BUY mean_dev>0 = 系统性买贵（执行成本）；SELL mean_dev>0 = 卖得比开盘高（有利）。")
    L.append("- **实验主指标 = ENS mean_abs（|dev| 均值）**：09:35 执行 15 个交易日后对比本基线，收窄=实验成功。")
    L.append("- G2 保持 09:50 不动，其后续 15 日 abs_dev 作为**控制臂**（同期市场环境参照，剔除大盘风格漂移干扰）。")
    L.append("- |dev|>%.0fbps 标记 [REVIEW]（疑似除权口径错位或极端滑点，人工核对后决定是否剔除）。\n" % REVIEW_BPS)
    report = "\n".join(L)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print("\n[OK] 报告已写入 %s" % OUT_MD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
