# coding: utf-8
"""B 方案：旧模型 vs 生产模型，分段窗口对比（找模型退化发生在哪段）。

背景：A 对照（`g2_same_harness_compare_20261022.py`）显示同窗口同参数下：
  旧模型 20260825_1964t → 年化 113.56% / 回撤 -16.95%
  生产模型 20261019_1327t → 年化  44.04% / 回撤 -38.93%
本脚本按月分段回测，定位差异集中在哪些时段（是否只是历史段，近期是否反而更好）。

产出：results/G2对照_分段窗口_20261022.md / .json
用法：python research/g2_segment_compare_20261022.py
"""
import importlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)
os.chdir(PROJ)

import numpy as np
import pandas as pd

import data_config as DC

DATA = DC.DATA_DIR
MODEL_DIR = DC.MODEL_DIR
OUT_MD = os.path.join(PROJ, "results", "G2对照_分段窗口_20261022.md")
OUT_JSON = os.path.join(PROJ, "results", "G2对照_分段窗口_20261022.json")

with open(os.path.join(DATA, "g2_live_model.json"), encoding="utf-8") as f:
    LIVE = json.load(f)

OLD_MODEL = os.path.join(MODEL_DIR, "lgb_model_v3_g2_strong_real_20260825_1964t.txt")
OLD_META = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
NEW_MODEL = LIVE["model_path"]
NEW_META = LIVE["meta_path"]
PANEL = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")

# 分段：按半年切，覆盖近两年 + 最近 3 个月单独看
SEGMENTS = [
    ("2024H2", "2024-07-01", "2024-12-31"),
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2", "2025-07-01", "2025-12-31"),
    ("2026H1", "2026-01-01", "2026-06-30"),
    ("2026Q3", "2026-07-01", "2026-09-30"),
]


def _clear_env():
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER", "BT_TOP",
              "BT_START", "BT_END", "BT_PANEL", "BT_MODEL", "BT_META",
              "BT_THRESHOLD", "BT_EXIT", "BT_SLIPS", "BT_N_LIST", "BT_DEPLOY_PCT"):
        os.environ.pop(k, None)


def run_seg(model, meta, start, end):
    _clear_env()
    os.environ.update({
        "BT_PANEL": PANEL, "BT_MODEL": model, "BT_META": meta,
        "BT_EXIT": "live_trail", "BT_SLIPS": "0.001", "BT_N_LIST": "1",
        "BT_START": start, "BT_END": end, "BT_TOP": "2",
        "BT_DEPLOY_PCT": "0.98", "BT_THRESHOLD": "60.0",
    })
    import scan_rotate_cost_real as SC
    importlib.reload(SC)

    dates, per_day, market_daily, open_map = SC.build_per_day()
    if not dates:
        return None
    trades, daily_ret, _ = SC.simulate(dates, per_day, 15, 0.001, open_map, exec_ok=True)
    strat = pd.Series(daily_ret.values, index=pd.to_datetime(dates)).fillna(0.0)
    n = len(strat)
    if n == 0:
        return None
    nav = (1 + strat).cumprod()
    total = float(nav.iloc[-1] - 1)
    # 段内不年化（段短，年化会失真），只报段内总收益
    mdd = float((nav / nav.cummax() - 1).min())
    rets = [t[2] for t in trades]
    return {
        "days": n, "trades": len(trades),
        "total_return": round(total, 4),
        "max_drawdown": round(mdd, 4),
        "win_rate": round(float((pd.Series(rets) > 0).mean()), 4) if rets else None,
    }


def main():
    print("=" * 76)
    print("G2 分段窗口对照：旧模型 20260825 vs 生产模型 20261019")
    print("=" * 76)
    rows = []
    for seg, start, end in SEGMENTS:
        print("\n>>> %s (%s ~ %s)" % (seg, start, end))
        r_old = r_new = None
        try:
            print("    旧模型...", end="", flush=True)
            r_old = run_seg(OLD_MODEL, OLD_META, start, end)
            print(" 段内 %s" % (("%+.2f%%" % (r_old["total_return"] * 100)) if r_old else "无数据"))
        except Exception as e:
            print(" 异常 %r" % (e,))
        try:
            print("    生产模型...", end="", flush=True)
            r_new = run_seg(NEW_MODEL, NEW_META, start, end)
            print(" 段内 %s" % (("%+.2f%%" % (r_new["total_return"] * 100)) if r_new else "无数据"))
        except Exception as e:
            print(" 异常 %r" % (e,))
        rows.append({"seg": seg, "start": start, "end": end, "old": r_old, "new": r_new})

    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows},
              open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    lines = [
        "# G2 分段窗口对照：旧模型 vs 生产模型",
        "",
        "> 生成 %s | 同 harness（`scan_rotate_cost_real.py`）、同参数（TOP2/deploy0.98/SC60/N15/live_trail/slip0.1%%）" % time.strftime("%Y-%m-%d %H:%M"),
        "> 旧模型 `lgb_model_v3_g2_strong_real_20260825_1964t.txt` vs 生产 `%s`" % os.path.basename(NEW_MODEL),
        "> 段内为**总收益**（段短不年化），回撤按段内净值计算",
        "",
        "| 分段 | 窗口 | 旧模型总收益 | 旧回撤 | 生产模型总收益 | 生产回撤 | 差（新−旧） |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        o, nw = r["old"], r["new"]
        if o and nw:
            diff = (nw["total_return"] - o["total_return"]) * 100
            lines.append("| %s | %s~%s | %+.2f%% | %.2f%% | %+.2f%% | %.2f%% | **%+.2fpp** |" % (
                r["seg"], r["start"][:7], r["end"][:7],
                o["total_return"] * 100, o["max_drawdown"] * 100,
                nw["total_return"] * 100, nw["max_drawdown"] * 100, diff))
        else:
            lines.append("| %s | %s~%s | %s | | %s | | |" % (
                r["seg"], r["start"][:7], r["end"][:7],
                ("%+.2f%%" % (o["total_return"] * 100)) if o else "无数据",
                ("%+.2f%%" % (nw["total_return"] * 100)) if nw else "无数据"))
    lines += ["", "## 判读", "",
              "- 若差异集中在历史段（2024H2~2025），说明新模型只是历史拟合弱，近期未必差",
              "- 若差异在近期段（2026H1/Q3）也显著为负，说明新模型确属退化，应考虑回退",
              ""]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("\n[输出] %s" % OUT_MD)
    print("[输出] %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
