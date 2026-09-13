# -*- coding: utf-8 -*-
"""配置漂移复查（2026-09-13 立，T-20260913-001 P1）—— promote 新模型后自动复查「最优配置是否仍最优」。

背景：strategy_cfg_lib.json 是 09-09 一次网格寻优的静态快照。模型每周围更，最优 N/红线/TOP/出场
可能漂移。本脚本在候选模型（或 live 模型）下扫「最优配置邻域」小网格（S1 粗扫的邻域版），
与配置库历史最优对比——**只登记漂移候选，不改实盘配置**（改实盘必须纸面前向 30 笔 + 人工拍板）。

用法（项目根目录）：
  python config_drift_recheck.py --strategy G2 --model <模型> --meta <meta>
  python config_drift_recheck.py --strategy G2 --model <模型> --meta <meta> --dry-run

退出码：0 正常（含漂移登记）；1 运行错误。漂移结论落 data/config_drift_<strategy>_<date>.md/.json。
"""
import argparse
import datetime
import importlib
import json
import os
import sys

import numpy as np

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

CFG_LIB = os.path.join(DC.DATA_DIR, "strategy_cfg_lib.json")

# 邻域扫描：各策略历史最优 N 的关键邻域点（N-5/N/N+5，保持 EXIT），仅 3 点控制耗时（每点约8-10分钟）
# 与 DE 提案的"小网格邻域"一致但进一步收窄，避免周一 retrain 任务超时（retrain 已约2h + 策略层门禁）
NEIGHBORHOOD = {
    "G2": {"N": [10, 15, 20], "EXIT": ["live_trail"]},
    "融合": {"N": [5, 10, 15], "EXIT": ["live_trail"]},
    "V1.4": {"N": [5, 10, 15], "EXIT": ["fixed"]},
}


def log(m):
    print(m, flush=True)


def run_backtest(model_path, meta_path, cfg, n, exit_mode, top, threshold):
    """在指定配置下跑官方引擎回测，返回 daily_excess 等。复用 gate_strategy_layer 口径。"""
    import scan_rotate_cost_real as SC

    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER", "BT_TOP", "BT_START", "BT_END", "BT_PKOUT"):
        os.environ.pop(k, None)
    c = dict(cfg["config"])
    os.environ["BT_PANEL"] = os.path.join(PROJ, c["BT_PANEL"].replace("/", os.sep).replace("\\", os.sep))
    os.environ["BT_MODEL"] = model_path
    os.environ["BT_META"] = meta_path
    os.environ["BT_THRESHOLD"] = str(threshold)
    os.environ["BT_EXIT"] = exit_mode
    os.environ["BT_SLIPS"] = "0.001"
    os.environ["BT_TOP"] = str(top)
    if c.get("BT_POSFILTER"):
        os.environ["BT_POSFILTER"] = c["BT_POSFILTER"]
    if c.get("BT_ENSEMBLE"):
        os.environ["BT_ENSEMBLE"] = c["BT_ENSEMBLE"]
        os.environ["BT_MODEL2"] = c["BT_MODEL2"]
        os.environ["BT_META2"] = c["BT_META2"]
    importlib.reload(SC)
    dates, per_day, market_daily, open_map = SC.build_per_day()
    trades, daily_ret, n_skip = SC.simulate(dates, per_day, int(n), 0.001, open_map, exec_ok=True)
    rets = [t[2] for t in trades]
    fwd = np.array(rets)
    nav = (1 + daily_ret.fillna(0.0)).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    excess = float(daily_ret.fillna(0.0).mean() - market_daily.fillna(0.0).mean())
    return {"excess": excess, "n": len(trades), "mdd": mdd, "win": float((fwd > 0).mean()) if len(fwd) else np.nan}


def main():
    ap = argparse.ArgumentParser(description="配置漂移复查（promote 后邻域小网格）")
    ap.add_argument("--strategy", required=True, choices=list(NEIGHBORHOOD.keys()))
    ap.add_argument("--model", required=True, help="模型路径（候选或 live）")
    ap.add_argument("--meta", required=True, help="模型 meta")
    ap.add_argument("--dry-run", action="store_true", help="只评估不写报告")
    args = ap.parse_args()

    lib = json.load(open(CFG_LIB, encoding="utf-8"))
    if args.strategy not in lib["strategies"]:
        log("!! 策略 %s 不在配置库" % args.strategy)
        return 1
    cfg = lib["strategies"][args.strategy]
    hist = cfg["config"]
    hist_key = "%sN/%s" % (hist["N"], hist["EXIT"])
    hist_excess = None
    # 历史最优超额：从 _说明/历史最优 字段解析（登记为 "live_trail/N15/红线60/TOP2 = +0.213%/日"）
    import re as _re
    m = _re.search(r"= \+?([0-9.]+)%/日", cfg.get("历史最优", ""))
    if m:
        hist_excess = float(m.group(1)) / 100.0

    model = os.path.abspath(args.model)
    meta = os.path.abspath(args.meta)
    if not (os.path.exists(model) and os.path.exists(meta)):
        log("!! 模型或 meta 不存在")
        return 1

    log("=== 配置漂移复查 %s（%s）===" % (args.strategy, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("模型: %s | 历史最优: %s（%s excess=%.4f）" % (os.path.basename(model), hist_key, "登记" if hist_excess else "未解析", hist_excess or 0.0))

    results = []
    top = hist["TOP"]
    thr = hist.get("BT_THRESHOLD", "60.0")
    for n in NEIGHBORHOOD[args.strategy]["N"]:
        for ex in NEIGHBORHOOD[args.strategy]["EXIT"]:
            try:
                r = run_backtest(model, meta, cfg, n, ex, top, thr)
                results.append({"N": n, "EXIT": ex, **r})
                log("  N=%-2d %-10s excess=%+.4f 胜率=%.1f%% n=%d" % (
                    n, ex, r["excess"], (r["win"] or 0) * 100, r["n"]))
            except Exception as e:
                log("  N=%-2d %-10s 失败: %s" % (n, ex, e))
                results.append({"N": n, "EXIT": ex, "excess": None, "n": 0})

    ok = [r for r in results if r.get("excess") is not None]
    if not ok:
        log("!! 无有效结果")
        return 1
    best = max(ok, key=lambda r: r["excess"])
    drift = (best["N"] != hist["N"] or best["EXIT"] != hist["EXIT"])
    log("-" * 60)
    log("扫描最优: %sN/%s excess=%+.4f（n=%d）" % (best["N"], best["EXIT"], best["excess"], best["n"]))
    log("历史最优: %sN/%s" % (hist["N"], hist["EXIT"]))
    if drift:
        log(">> 结论: **配置漂移** —— 新模型下最优配置偏移，仅登记候选，实盘不改（须纸面前向30笔+人工拍板）")
    else:
        log(">> 结论: 配置未漂移 —— 历史最优配置在新模型下仍最优/相近")

    if args.dry_run:
        log("[dry-run] 未写报告")
        return 0

    out_json = os.path.join(DC.DATA_DIR, "config_drift_%s_%s.json" % (args.strategy, datetime.datetime.now().strftime("%Y%m%d")))
    out_md = os.path.join(DC.DATA_DIR, "config_drift_%s_%s.md" % (args.strategy, datetime.datetime.now().strftime("%Y%m%d")))
    report = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": args.strategy, "model": os.path.basename(model),
        "historical_best": hist_key, "historical_excess": hist_excess,
        "scan_best": "%sN/%s" % (best["N"], best["EXIT"]), "scan_excess": best["excess"],
        "drifted": drift,
        "note": "只登记不改实盘；配置切换必须纸面前向30笔+人工拍板（网格寻优方法论）",
        "results": results,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    lines = ["# 配置漂移复查 %s" % args.strategy, "",
             "> 生成：%s | 模型：%s" % (report["generated_at"], report["model"]),
             "> 历史最优：**%s**（登记 excess %s）" % (hist_key, "%.4f" % hist_excess if hist_excess else "N/A"), "",
             "| N | EXIT | excess | 胜率 | n |", "|---|---|---|---|---|"]
    for r in results:
        lines.append("| %s | %s | %s | %s | %d |" % (
            r["N"], r["EXIT"], "%.4f" % r["excess"] if r.get("excess") is not None else "—",
            "%.1f%%" % ((r.get("win") or 0) * 100) if r.get("win") else "—", r["n"]))
    lines += ["", "## 结论", "",
              "- 扫描最优：**%sN/%s**（excess %+.4f）" % (best["N"], best["EXIT"], best["excess"]),
              "- **%s**" % ("配置漂移（仅登记候选，实盘不改）" if drift else "配置未漂移"), "",
              "> 只登记不改实盘；配置切换必须纸面前向 30 笔 + 人工拍板。"]
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("报告: %s" % out_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
