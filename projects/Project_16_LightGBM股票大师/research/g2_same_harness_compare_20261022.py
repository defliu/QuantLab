# coding: utf-8
"""A 方案：同窗口 + 池子规则 100→10 + 生产评分卡 SC60 的 G2 对照回测。

回答的问题
----------
两份历史报告差异巨大（修复复核 +110% vs B21 干净对比 +12%），需要同 harness 对照厘清：

  ① 旧回测模型 + 旧窗口（2024-07-01~2026-08-14）：能否复现 +110%（锚点验证）
  ② 同模型 + B21 窗口（~2026-09-30）：窗口延长的影响
  ③ 生产模型（20261019_1327t）+ B21 窗口：模型换代的真实影响 ← 最关键
  ④ 生产模型 + 简化红线 prob≥0.60（对照 ③ 的评分卡 SC60）

全部走 `scan_rotate_cost_real.py`（池子规则 nlargest(100)→nlargest(10)，与实盘
`deploy_predict_g2.py --top 100` 一致），**不使用** `backtest_b21_strategy.py`
（其简化红线模式把候选池从 10 放开到全市场，口径失真）。

产出：results/G2对照_同harness生产模型_20261022.md / .json
用法：python research/g2_same_harness_compare_20261022.py
"""
import importlib
import io
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

OUT_MD = os.path.join(PROJ, "results", "G2对照_同harness生产模型_20261022.md")
OUT_JSON = os.path.join(PROJ, "results", "G2对照_同harness生产模型_20261022.json")

# 生产 live 指针
with open(os.path.join(DATA, "g2_live_model.json"), encoding="utf-8") as f:
    LIVE = json.load(f)

OLD_MODEL = os.path.join(MODEL_DIR, "lgb_model_v3_g2_strong_real_20260825_1964t.txt")
OLD_META = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
NEW_MODEL = LIVE["model_path"]
NEW_META = LIVE["meta_path"]

PANEL = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")

WIN_OLD = ("2024-07-01", "2026-08-14")   # 修复复核官方 OOS 窗
WIN_NEW = ("2024-07-01", "2026-09-30")   # B21 报告窗

CONFIGS = [
    # (标签, 模型, meta, 窗口, 红线, 说明)
    ("① 旧模型 + 旧窗口 + TOP2 + 0.95（官方锚点）", OLD_MODEL, OLD_META, WIN_OLD, "60.0", "对齐修复复核 +110.12%"),
    ("①b 旧模型 + 旧窗口 + TOP2 + 0.98（实盘口径）", OLD_MODEL, OLD_META, WIN_OLD, "60.0", "对齐 +113.56%"),
    ("② 生产模型 + 旧窗口 + TOP2 + 0.98", NEW_MODEL, NEW_META, WIN_OLD, "60.0", "★ 同窗口换模型的净影响"),
    ("③ 生产模型 + B21窗口 + TOP2 + 0.98（SC60）", NEW_MODEL, NEW_META, WIN_NEW, "60.0", "★ 当前实盘真实口径"),
    ("④ 生产模型 + B21窗口 + TOP2 + 0.98（prob≥0.60）", NEW_MODEL, NEW_META, WIN_NEW, "-1", "简化红线对照"),
]


def _clear_env():
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER", "BT_TOP",
              "BT_START", "BT_END", "BT_PANEL", "BT_MODEL", "BT_META",
              "BT_THRESHOLD", "BT_EXIT", "BT_SLIPS", "BT_N_LIST", "BT_DEPLOY_PCT"):
        os.environ.pop(k, None)


def run_one(label, model, meta, window, threshold, thr_is_prob=False, top=2, deploy_pct=0.98):
    _clear_env()
    os.environ["BT_PANEL"] = PANEL
    os.environ["BT_MODEL"] = model
    os.environ["BT_META"] = meta
    os.environ["BT_EXIT"] = "live_trail"     # 对齐实盘风控
    os.environ["BT_SLIPS"] = "0.001"
    os.environ["BT_N_LIST"] = "1"
    os.environ["BT_START"] = window[0]
    os.environ["BT_END"] = window[1]
    os.environ["BT_TOP"] = str(top)          # 官方锚点/实盘历史用 TOP=2
    os.environ["BT_DEPLOY_PCT"] = str(deploy_pct)
    os.environ["BT_THRESHOLD"] = threshold

    import scan_rotate_cost_real as SC
    importlib.reload(SC)

    t0 = time.time()
    dates, per_day, market_daily, open_map = SC.build_per_day()

    if thr_is_prob:
        # 简化红线：劫持 per_day，把 total_new 替换为 prob*100（prob>=0.60 ⇔ total_new>=60）
        # 这样 simulate 内的 `total_new >= THRESHOLD` 语义等价于 prob 红线，池子规则不变。
        for d in per_day:
            df = per_day[d]
            if "prob" in df.columns:
                df["total_new"] = df["prob"] * 100.0
            per_day[d] = df

    N = 15                                   # g2_config.HOLD_DAYS
    trades, daily_ret, n_skip = SC.simulate(dates, per_day, N, 0.001, open_map, exec_ok=True)
    strat = pd.Series(daily_ret.values, index=pd.to_datetime(dates)).fillna(0.0)
    bench = pd.Series(market_daily.values, index=pd.to_datetime(dates)).fillna(0.0)

    n = len(strat)
    nav = (1 + strat).cumprod()
    total = float(nav.iloc[-1] - 1)
    ann = float((1 + total) ** (252 / n) - 1) if total > -1 else -1.0
    vol = float(strat.std(ddof=1) * np.sqrt(252)) if n > 1 else np.nan
    sharpe = float(strat.mean() / strat.std(ddof=1) * np.sqrt(252)) if n > 1 and strat.std() > 0 else np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    rets = [t[2] for t in trades]
    win_rate = float((pd.Series(rets) > 0).mean()) if rets else np.nan

    return {
        "label": label,
        "model": os.path.basename(model),
        "window": "%s ~ %s" % window,
        "threshold": "prob>=0.60" if thr_is_prob else ("total_new>=%s" % threshold),
        "n_days": n, "n_trades": len(trades),
        "total_return": round(total, 4),
        "annual_return": round(ann, 4),
        "sharpe": round(sharpe, 3) if sharpe == sharpe else None,
        "max_drawdown": round(mdd, 4),
        "win_rate": round(win_rate, 4) if win_rate == win_rate else None,
        "skips": dict(n_skip) if isinstance(n_skip, dict) else n_skip,
        "elapsed": round(time.time() - t0, 1),
    }


def main():
    print("=" * 78)
    print("G2 同 harness 对照（池子规则 nlargest(100)→nlargest(10)，对齐实盘 --top 100）")
    print("=" * 78)
    print("生产模型: %s" % os.path.basename(NEW_MODEL))
    print("面板: %s" % os.path.basename(PANEL))

    rows = []
    for label, model, meta, window, thr, _note in CONFIGS:
        is_prob = (thr == "-1")
        # ① 用 0.95（官方口径），其余用 0.98（实盘口径）
        dp = 0.95 if label.startswith("① 旧模型") else 0.98
        print("\n>>> %s" % label)
        print("    模型=%s 窗口=%s 红线=%s TOP=2 deploy=%.2f" % (
            os.path.basename(model), "%s~%s" % window,
            "prob>=0.60" if is_prob else thr, dp))
        try:
            r = run_one(label, model, meta, window, thr, thr_is_prob=is_prob,
                        top=2, deploy_pct=dp)
        except Exception as e:
            import traceback
            traceback.print_exc()
            r = {"label": label, "error": repr(e)}
        rows.append(r)
        if "error" not in r:
            print("    年化 %.2f%% | 总收益 %.2f%% | 夏普 %.2f | 回撤 %.2f%% | 笔数 %d | 耗时 %.0fs" % (
                r["annual_return"] * 100, r["total_return"] * 100,
                r["sharpe"] or 0, r["max_drawdown"] * 100,
                r["n_trades"], r["elapsed"]))

    # 落盘
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "panel": os.path.basename(PANEL),
                   "live_model": os.path.basename(NEW_MODEL),
                   "rows": rows}, f, ensure_ascii=False, indent=2)

    lines = [
        "# G2 同 harness 对照（生产模型 / 生产池子规则）",
        "",
        "> 生成 %s | 面板 `%s` | 生产模型 `%s`" % (
            time.strftime("%Y-%m-%d %H:%M"), os.path.basename(PANEL), os.path.basename(NEW_MODEL)),
        "> **全部走 `scan_rotate_cost_real.py`**：候选池 = `nlargest(100, prob)` → `nlargest(10, prob)`，",
        "> 与实盘 `deploy_predict_g2.py --top 100` 一致；不使用 `backtest_b21_strategy.py`（其简化红线模式池子失真）。",
        "> 成本：佣金万2(最低5元) + 印花万5(卖出) + 过户万0.1(沪) + slip 0.1%/边；出场 live_trail(-7%/+15%/峰值回撤8%)；N=15；TOP=3。",
        "",
        "## 结果",
        "",
        "| 配置 | 模型 | 窗口 | 红线 | 年化 | 总收益 | 夏普 | 回撤 | 笔数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            lines.append("| %s | - | - | - | ERROR: %s | | | | |" % (r["label"], r["error"]))
            continue
        lines.append("| %s | %s | %s | %s | **%+.2f%%** | %+.2f%% | %.2f | %.2f%% | %d |" % (
            r["label"], r["model"], r["window"], r["threshold"],
            r["annual_return"] * 100, r["total_return"] * 100,
            r["sharpe"] or 0, r["max_drawdown"] * 100, r["n_trades"]))

    lines += ["", "## 判读要点", "",
              "- ① → ② 隔离「窗口延长 1.5 月」影响",
              "- ② → ③ 隔离「模型换代（20260825→生产周更）」影响 ← 最关键",
              "- ③ → ④ 隔离「评分卡 SC60 vs 简化红线 prob≥0.60」影响",
              ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n[输出] %s" % OUT_MD)
    print("[输出] %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
