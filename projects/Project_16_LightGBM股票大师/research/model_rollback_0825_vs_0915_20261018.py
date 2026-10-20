# -*- coding: utf-8 -*-
"""独立模型回退实证：0825_1964t vs 0915_1522t（T-20261018-002）。

缘起：T-20261018-001 评分卡层消融中发现「现役 0915 模型在官方口径下 +68.71% 年化，比 0825 的
+110.12% 低 41.4pp」——该结论只来自**单一窗口（2024-07~2026-08-14）+ 单配置（N15/TOP2/deploy0.95）**，
不足以支撑"回退"决策。本脚本做三轴独立实证：

  axis=model    模型层：官方门禁 harness（train_g2.build_panel + TG.eval_metrics）同面板同窗比
                ic / tail_ic / top2_ret（**0825 从未在此 harness 上测过** → 真·独立度量）
  axis=strategy 策略层：官方引擎 scan_rotate_cost_real.py，主口径（N15/TOP2/deploy0.95）跨 5 个窗口
                （全窗 / 题材窗 / 2024H2 / 2025 / 2026）+ 实盘对齐口径（N15/TOP3/deploy0.98）2 窗口；
                附池诊断（两模型每日 Top2(total_new) 的 fwd_ret、Top10(prob) 池质量、Top2 重叠率）
  axis=forward  前向纸面：data/real/paper_forward_exit_live.csv 按模型年代切（entry_day < 2026-09-15 =
                0825 年代 / >= 09-15 = 0915 年代），比 live_trail 口径 per-trade 收益
                （注：不同市场窗口、n 小、候选≠持仓 —— 弱证据轴）

判定规则（预先声明）：
  「0915 相对 0825 为降级」= 主口径下 0825 年化在**全部 5 个窗口**都不低于 0915，且实盘对齐口径
  （TOP3/deploy0.98）同向；否则判「证据不足，须前向 A/B」。
  注意：两模型都在 test 窗（2024-07+）上被周更门禁筛过 → 该窗口对两者都有选择偏差（对称但非零）；
  0825 训练面板 vintage 更早（特征快照滞后），非严格同训练数据对照 —— 已在报告披露。

产物：research/results/model_rollback_0825_vs_0915_20261018.json
用法：python research/model_rollback_0825_vs_0915_20261018.py --axis model|strategy|forward|all
"""
import argparse
import datetime
import gc
import importlib
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402
import jq_metrics as JM  # noqa: E402

D = lambda p: os.path.join(DC.DATA_DIR, p)    # noqa: E731
M = lambda p: os.path.join(DC.MODEL_DIR, p)   # noqa: E731

PANEL = D("feature_panel_v3_enh2_n3_bt.parquet")
OUT_JSON = os.path.join(PROJ, "research", "results", "model_rollback_0825_vs_0915_20261018.json")
PAPER_EXIT = os.path.join(DC.DATA_DIR, "real", "paper_forward_exit_live.csv")
PAPER_LIVE = os.path.join(DC.DATA_DIR, "real", "paper_forward_live.csv")
SWITCH = pd.Timestamp("2026-09-15")   # 0915 模型 promote 生效日

MODELS = {
    "0825_1964t": {"model": M("lgb_model_v3_g2_strong_real_20260825_1964t.txt"),
                   "meta": D("features_v3_g2_strong_real_20260825.json")},
    "0915_1522t": {"model": M("lgb_model_v3_g2_strong_real_20260915_1522t.txt"),
                   "meta": D("features_v3_g2_strong_real_20260915.json")},
}

N_MAIN, TOP_MAIN, SLIP, DEPLOY_MAIN = 15, 2, 0.001, "0.95"
TOP_LIVE, DEPLOY_LIVE = 3, "0.98"

WINDOWS = [
    ("W_flag_2407_2608", "2024-07-01", "2026-08-14"),
    ("W_full_2407_2609", "2024-07-01", "2026-09-30"),
    ("W_2024H2", "2024-07-01", "2024-12-31"),
    ("W_2025", "2025-01-01", "2025-12-31"),
    ("W_2026", "2026-01-01", "2026-09-30"),
]
CONFIGS = [
    ("main_N15_TOP2_d95", N_MAIN, TOP_MAIN, DEPLOY_MAIN, [w[0] for w in WINDOWS]),
    ("live_N15_TOP3_d98", N_MAIN, TOP_LIVE, DEPLOY_LIVE, ["W_flag_2407_2608", "W_full_2407_2609"]),
]


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def load_out():
    if os.path.exists(OUT_JSON):
        with open(OUT_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {"meta": {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}


def save_out(out):
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


# ---------------- axis=model：官方门禁 harness ----------------
def axis_model(out):
    import train_g2 as TG
    import lightgbm as lgb

    log("axis=model: train_g2.build_panel() ...")
    panel, _ = TG.build_panel()
    log("  panel 行=%d | 日期 %s ~ %s" % (len(panel), panel["trade_date"].min().date(),
                                          panel["trade_date"].max().date()))
    res = {}
    for mkey, cfg in MODELS.items():
        with open(cfg["meta"], encoding="utf-8") as f:
            feats = json.load(f)["feature_cols"]
        bst = lgb.Booster(model_file=cfg["model"])
        m = TG.eval_metrics(panel, feats, bst, thr=0.60)
        m["n_features"] = len(feats)
        res[mkey] = m
        log("  %-11s ic=%.5f tail_ic=%s top2_ret=%+.5f (n=%d) feats=%d"
            % (mkey, m["ic"], ("%.5f" % m["tail_ic"]) if m["tail_ic"] else "None",
               m["top2_ret"], m["top2_n"], len(feats)))
    out.setdefault("model_level", {})["harness"] = "train_g2.build_panel + TG.eval_metrics（官方门禁口径，test 窗 2024-07+）"
    out["model_level"]["results"] = res
    del panel
    gc.collect()
    save_out(out)


# ---------------- axis=strategy：官方引擎跨窗口 ----------------
def _build(mkey, end):
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER"):
        os.environ.pop(k, None)
    os.environ.update({
        "BT_PANEL": PANEL, "BT_MODEL": MODELS[mkey]["model"], "BT_META": MODELS[mkey]["meta"],
        "BT_THRESHOLD": "60.0", "BT_EXIT": "live_trail",
        "BT_START": "2024-07-01", "BT_END": end, "BT_DEPLOY_PCT": DEPLOY_MAIN,
    })
    import scan_rotate_cost_real as SC
    importlib.reload(SC)
    log("  build_per_day model=%s ..." % mkey)
    dates, per_day, market_avgs, open_map = SC.build_per_day()
    return SC, dates, per_day, [float(x) for x in market_avgs.values], open_map


def _sim(SC, per_day, dates, bench_map, win_dates, N, slip, open_map):
    idx = pd.to_datetime(win_dates)
    trades, daily_ret, n_skip = SC.simulate(win_dates, per_day, N, slip, open_map, exec_ok=True)
    strat = pd.Series(daily_ret.values, index=idx).fillna(0.0)
    bench = pd.Series([bench_map[d] for d in win_dates], index=idx).fillna(0.0)
    m = JM.metrics(trades, strat, bench)
    m["n_skip_exec"] = int(n_skip)
    m["exit_reasons"] = dict(SC.EXIT_REASON_CNT)
    for k in ("nav_series", "drawdown_series", "date_labels", "bench_nav", "monthly"):
        m.pop(k, None)
    return m


def _pool_diag(per_day, dates):
    """池诊断：每日 Top2(total_new) 与 Top10(prob) 的 fwd_ret 均值 + 过线候选数。"""
    top2, top10, npass = [], [], []
    for d in dates:
        row = per_day[d]
        if row is None or len(row) == 0:
            continue
        c = row[row["total_new"] >= 60.0]
        if len(c):
            top2.extend(c.nlargest(2, "total_new")["fwd_ret"].dropna().tolist())
            npass.append(int(len(c)))
        top10.extend(row.nlargest(10, "prob")["fwd_ret"].dropna().tolist())
    f = lambda v: (float(np.nanmean(v)) if len(v) else float("nan"), len(v))  # noqa: E731
    t2, n2 = f(top2)
    t10, n10 = f(top10)
    return {"top2_by_totalnew_fwd_ret": t2, "top2_n": n2,
            "top10_by_prob_fwd_ret": t10, "top10_n": n10,
            "days_with_candidate": len(npass),
            "avg_pass_per_day": float(np.mean(npass)) if npass else float("nan")}


def axis_strategy(out):
    res = {}
    for cname, N, TOP, deploy, wnames in CONFIGS:
        for mkey in MODELS:
            # 逐 (配置×模型) 重建：reload 会清空引擎模块级 maps，故 build → 同配置所有窗口一起仿
            os.environ["BT_TOP"] = str(TOP)
            SC, dates, per_day, bench_vals, open_map = _build(mkey, "2026-09-30")
            bench_map = dict(zip(dates, bench_vals))
            log("  %s / %s：面板日=%d (%s ~ %s)" % (cname, mkey, len(dates),
                                                  dates[0].date(), dates[-1].date()))
            for wname, ws, we in WINDOWS:
                if wname not in wnames:
                    continue
                ws, we = pd.Timestamp(ws), pd.Timestamp(we)
                wd = [d for d in dates if ws <= d <= we]
                if len(wd) < 40:
                    continue
                SC.EXIT_REASON_CNT.clear()
                SC.SELL_SKIP_DOWN[0] = 0
                SC.SELL_DELIST[0] = 0
                m = _sim(SC, per_day, dates, bench_map, wd, N, SLIP, open_map)
                m["window"] = "%s ~ %s" % (wd[0].date(), wd[-1].date())
                key = "%s|%s|%s" % (cname, mkey, wname)
                res[key] = m
                log("    %-22s %-11s 年化%8.2f%% 夏普%5.2f 回撤%6.1f%% 交易%4d 日均超额%7.4f%%"
                    % (wname, mkey, m["annual_return"] * 100, m["sharpe"],
                       m["max_drawdown"] * 100, m["n_trades"], m["excess_annual"] / 252 * 100))
            if cname == "main_N15_TOP2_d95":
                res["POOLDIAG|%s" % mkey] = _pool_diag(per_day, dates)
                log("    池诊断 %s: Top2(total_new) fwd_ret=%+.4f (n=%d) | Top10(prob) fwd_ret=%+.4f | 过线均 %.2f 只/日"
                    % (mkey, res["POOLDIAG|%s" % mkey]["top2_by_totalnew_fwd_ret"],
                       res["POOLDIAG|%s" % mkey]["top2_n"],
                       res["POOLDIAG|%s" % mkey]["top10_by_prob_fwd_ret"],
                       res["POOLDIAG|%s" % mkey]["avg_pass_per_day"]))
            del per_day, bench_map
            gc.collect()
    # Top2 集合重叠率（用 main 配置两次 build 的 per_day 不可共享 → 由 POOLDIAG 之外单列：跳过，报告以池质量替代）
    out["strategy_level"] = res
    save_out(out)


# ---------------- axis=forward：纸面前向按模型年代切 ----------------
def axis_forward(out):
    res = {}
    if os.path.exists(PAPER_EXIT):
        df = pd.read_csv(PAPER_EXIT)
        df["entry_day"] = pd.to_datetime(df["entry_day"])
        df["era"] = np.where(df["entry_day"] < SWITCH, "0825_era", "0915_era")
        for era, sub in df.groupby("era"):
            closed = sub.dropna(subset=["live_trail_ret"])
            none_closed = sub.dropna(subset=["none_ret"])
            res[era] = {
                "n_rows": int(len(sub)),
                "n_closed_live_trail": int(len(closed)),
                "mean_live_trail_ret": float(closed["live_trail_ret"].mean()) if len(closed) else None,
                "median_live_trail_ret": float(closed["live_trail_ret"].median()) if len(closed) else None,
                "win_rate_live_trail": float((closed["live_trail_ret"] > 0).mean()) if len(closed) else None,
                "mean_none_ret": float(none_closed["none_ret"].mean()) if len(none_closed) else None,
                "entry_day_min": str(sub["entry_day"].min().date()),
                "entry_day_max": str(sub["entry_day"].max().date()),
            }
    out["forward_level"] = {
        "source": "data/real/paper_forward_exit_live.csv（纸面前向候选，entry_day 切 2026-09-15）",
        "caveat": "不同市场窗口 + n 小 + 候选≠持仓（仅 rank1/2 候选的持有到期模拟），弱证据轴",
        "results": res,
    }
    save_out(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", default="all", choices=["model", "strategy", "forward", "all"])
    args = ap.parse_args()
    out = load_out()
    if args.axis in ("model", "all"):
        axis_model(out)
    if args.axis in ("strategy", "all"):
        axis_strategy(out)
    if args.axis in ("forward", "all"):
        axis_forward(out)
    log("完成 → %s" % OUT_JSON)


if __name__ == "__main__":
    main()
