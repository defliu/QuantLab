# -*- coding: utf-8 -*-
"""评分卡层消融：拆解「内产版 18.8% vs 官方口径 113.6%」的差距来源。

背景（2026-10-17 B21 信号内产版策略级回测报告）：
  内产版（沙箱内特征 B21 + 简化红线 prob>=0.60）策略级年化 +18.8%；
  官方口径 G2（43 特征模型 + 评分卡 total_new>=60）冷启动年化 110.1%(deploy 0.95)/113.6%(0.98)。
  两者差 6 倍，但差在哪一直没拆——本脚本做 2×2 消融：
    变量1 模型档：0825_1964t（官方锚点所用） / 0915_1522t（现役）
    变量2 评分卡层：ON（total_new = 评分卡 F1~F6，官方链） / OFF（total_new = prob*100，只有模型分）

  单跑官方引擎 scan_rotate_cost_real.py 的可执行 open→open 口径（N15 / live_trail / 红线60 / TOP2 /
  滑点 0.1% / 2024-07-01~2026-08-14），指标复用研究侧 jq_metrics.metrics（与 113.6% 锚点同函数）。

  评分卡 OFF 的实现：per_day 帧内 total_new 直接换成 prob*100（帧内已含 prob 列，无需改引擎）——
  等价于「候选池仍由 prob 决定，但门槛与排序都不用评分卡」，即沙箱内产版的简化红线语义。

校验档：0825+评分卡 ON 应逐项复现 jq_metrics 官方锚点（总收益 357.41% / 年化 110.12% /
        夏普 2.278 / 回撤 -16.71% / 交易 78 笔）；不一致则本脚本结论作废。

产物：research/results/ablation_scorecard_20261018.json
用法：python research/ablation_scorecard_20261018.py
"""
import datetime
import importlib
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402
import jq_metrics as JM  # noqa: E402  复用官方 metrics()

D = lambda p: os.path.join(DC.DATA_DIR, p)    # noqa: E731
M = lambda p: os.path.join(DC.MODEL_DIR, p)   # noqa: E731

PANEL = D("feature_panel_v3_enh2_n3_bt.parquet")
N = 15
SLIP = 0.001
TOP = 2
DEPLOY = "0.95"          # 官方 jq_metrics 锚点口径（0.95 = 引擎原默认）
START = "2024-07-01"
END = "2026-08-14"
OUT_JSON = os.path.join(PROJ, "research", "results", "ablation_scorecard_20261018.json")

MODELS = {
    "0825_1964t": {"model": M("lgb_model_v3_g2_strong_real_20260825_1964t.txt"),
                   "meta": D("features_v3_g2_strong_real_20260825.json")},
    "0915_1522t": {"model": M("lgb_model_v3_g2_strong_real_20260915_1522t.txt"),
                   "meta": D("features_v3_g2_strong_real_20260915.json")},
}

ANCHOR = {"total_return": 3.5741, "annual_return": 1.1012, "sharpe": 2.28,
          "max_drawdown": -0.167, "n_trades": 78}


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def build(mkey):
    """按模型档构建 per_day（含评分卡 total_new）。"""
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER"):
        os.environ.pop(k, None)
    os.environ.update({
        "BT_PANEL": PANEL,
        "BT_MODEL": MODELS[mkey]["model"],
        "BT_META": MODELS[mkey]["meta"],
        "BT_THRESHOLD": "60.0",
        "BT_EXIT": "live_trail",
        "BT_TOP": str(TOP),
        "BT_DEPLOY_PCT": DEPLOY,
        "BT_START": START,
        "BT_END": END,
    })
    import scan_rotate_cost_real as SC
    importlib.reload(SC)
    log("build_per_day: model=%s" % mkey)
    dates, per_day, market_avgs, open_map = SC.build_per_day()
    log("  面板日数=%d" % len(dates))
    return SC, dates, per_day, market_avgs, open_map


def sim(SC, dates, per_day, market_avgs, open_map, scorecard_on):
    """scorecard_on=False 时把 total_new 换成 prob*100（只有模型分，无评分卡）。"""
    if scorecard_on:
        use = per_day
    else:
        use = {}
        for d, row in per_day.items():
            r = row.copy()
            r["total_new"] = r["prob"] * 100.0
            use[d] = r
    SC.EXIT_REASON_CNT.clear()
    SC.SELL_SKIP_DOWN[0] = 0
    SC.SELL_DELIST[0] = 0
    trades, daily_ret, n_skip = SC.simulate(dates, use, N, SLIP, open_map, exec_ok=True)
    idx = pd.to_datetime(dates)
    strat = pd.Series(daily_ret.values, index=idx).fillna(0.0)
    bench = pd.Series(np.asarray(market_avgs.values, dtype=float), index=idx).fillna(0.0)
    m = JM.metrics(trades, strat, bench)
    m["n_skip_exec"] = int(n_skip)
    m["exit_reasons"] = dict(SC.EXIT_REASON_CNT)
    m["sell_skip_down"] = int(SC.SELL_SKIP_DOWN[0])
    m["sell_delist"] = int(SC.SELL_DELIST[0])
    for k in ("nav_series", "drawdown_series", "date_labels", "bench_nav", "monthly"):
        m.pop(k, None)
    return m


def main():
    out = {"meta": {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "engine": "scan_rotate_cost_real.py（官方引擎，可执行 open→open）",
        "cfg": {"panel": os.path.basename(PANEL), "N": N, "top": TOP, "slip": SLIP,
                "deploy": DEPLOY, "exit": "live_trail", "threshold": 60.0,
                "window": "%s ~ %s" % (START, END)},
        "variants": "scorecard ON = total_new 用评分卡 F1~F6；OFF = total_new = prob*100（沙箱内产版简化红线语义）",
    }, "anchor_official": ANCHOR, "results": {}}

    for mkey in MODELS:
        SC, dates, per_day, market_avgs, open_map = build(mkey)
        for tag, sc_on in (("scorecard_ON", True), ("scorecard_OFF", False)):
            key = "%s|%s" % (mkey, tag)
            m = sim(SC, dates, per_day, market_avgs, open_map, sc_on)
            out["results"][key] = m
            log("%-24s 总收益%8.2f%% 年化%8.2f%% 夏普%5.2f 回撤%6.1f%% 交易%4d 胜率%5.1f%% 日均超额%7.4f%% 跳过%d"
                % (key, m["total_return"] * 100, m["annual_return"] * 100, m["sharpe"],
                   m["max_drawdown"] * 100, m["n_trades"], m["win_rate"] * 100,
                   m["excess_annual"] / 252 * 100, m["n_skip_exec"]))

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("已写入 %s" % OUT_JSON)

    # 校验档自检
    a = out["results"]["0825_1964t|scorecard_ON"]
    ok = (abs(a["total_return"] - ANCHOR["total_return"]) < 0.005
          and abs(a["annual_return"] - ANCHOR["annual_return"]) < 0.005
          and a["n_trades"] == ANCHOR["n_trades"])
    log("校验档 0825+评分卡ON 复现官方锚点: %s" % ("PASS" if ok else "FAIL —— 结论不可用"))


if __name__ == "__main__":
    main()
