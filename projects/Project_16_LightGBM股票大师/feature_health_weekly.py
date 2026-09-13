# -*- coding: utf-8 -*-
"""特征级 IC/覆盖率周报（2026-09-13 立，T-20260913-001 F1）—— 全自动监控 + 人工裁决去留。

对 V1.3 实盘面板（feature_panel_v3.parquet，27 特征）逐特征算滚动 IC 与缺失率：
  - 近 4 周（约 20 个交易日）逐日横截面 Spearman IC
  - 近 4 周缺失率（NaN 占比）
判定告警：
  - 单特征近 4 周 IC 连续趋零（|IC|均值 < 0.01）→ WATCH
  - 缺失率 > 5% → WATCH（覆盖率退化，特征可能断源）
  - 同时满足近 4 周 |IC|<0.01 且缺失率>5% → ALERT（强烈建议人工核查/剔除）
不自动改特征集——只告警，去留人工裁决（防误伤 + 符合"重训不负责找 alpha"红线）。

用法：
  python feature_health_weekly.py                # 生成周报 + 有 ALERT 时飞书告警
  python feature_health_weekly.py --check        # 只读评估不推送（dry-run）
产物：data/feature_health_weekly_<date>.json/.md
退出码：0 正常（含 WATCH）；2 有 ALERT（fail-loud 供调度感知）；1 运行错误。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
META = os.path.join(DC.DATA_DIR, "features_v3.json")
OUT_JSON = os.path.join(DC.DATA_DIR, "feature_health_weekly_%s.json" % pd.Timestamp.now().strftime("%Y%m%d"))
OUT_MD = os.path.join(DC.DATA_DIR, "feature_health_weekly_%s.md" % pd.Timestamp.now().strftime("%Y%m%d"))

IC_FLOOR = 0.01    # 近 4 周 |IC| 均值 < 0.01 = 趋零
MISS_FLOOR = 0.05  # 缺失率 > 5% = 覆盖率退化


def calc_weekly_ic(panel, feat_cols):
    """逐日横截面 IC（Spearman 秩相关）→ 按周聚合（最近 4 周）。返回 (ic_week, coverage_week)。"""
    df = panel.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["week"] = df["trade_date"].dt.to_period("W")
    ic_daily = {}
    for d, g in df.groupby("trade_date"):
        if len(g) < 20:
            continue
        r = g[feat_cols].rank()
        rr = g["fwd_ret"].rank()
        ic = r.corrwith(rr)
        ic_daily[d] = ic.to_dict()
    if not ic_daily:
        return None, None
    ic_df = pd.DataFrame(ic_daily).T  # date x feature
    ic_df["week"] = pd.to_datetime(ic_df.index).to_period("W")
    ic_week = ic_df.groupby("week")[feat_cols].mean()
    cov = 1 - df.groupby("week")[feat_cols].apply(lambda s: s.isna().mean())
    return ic_week.tail(4), cov.tail(4)


def _notify(text):
    try:
        from qmt_bridge_client_ens import notify_feishu
        notify_feishu(text)
    except Exception:
        try:
            from qmt_bridge_client import notify_feishu as nf2
            nf2(text)
        except Exception as e:
            print("[通知] 飞书异常: %s" % e)
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description="特征级 IC/覆盖率周报")
    ap.add_argument("--check", action="store_true", help="只读评估不推送")
    args = ap.parse_args()

    if not os.path.exists(PANEL) or not os.path.exists(META):
        print("!! 面板或 meta 缺失（%s / %s）" % (PANEL, META))
        return 1
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    print("[1/3] 读取面板 %s（%d 特征）..." % (os.path.basename(PANEL), len(feat_cols)))
    panel = pd.read_parquet(PANEL)
    ic_week, cov_week = calc_weekly_ic(panel, feat_cols)
    if ic_week is None:
        print("!! 无足够数据计算 IC")
        return 1

    print("[2/3] 计算近 4 周 IC 与覆盖率 ...")
    summary = {}
    for f in feat_cols:
        ic_mean = float(ic_week[f].abs().mean()) if f in ic_week else np.nan
        miss = float((1 - cov_week[f]).mean()) if f in cov_week else np.nan
        ic_zero = (not np.isnan(ic_mean)) and ic_mean < IC_FLOOR
        cov_bad = (not np.isnan(miss)) and miss > MISS_FLOOR
        if ic_zero and cov_bad:
            verdict = "ALERT"
        elif ic_zero or cov_bad:
            verdict = "WATCH"
        else:
            verdict = "OK"
        summary[f] = {"ic_abs_mean_4w": round(ic_mean, 5) if not np.isnan(ic_mean) else None,
                      "missing_rate_4w": round(miss, 5) if not np.isnan(miss) else None,
                      "verdict": verdict}

    alerts = [f for f, v in summary.items() if v["verdict"] == "ALERT"]
    watches = [f for f, v in summary.items() if v["verdict"] == "WATCH"]

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "panel": os.path.basename(PANEL),
        "n_features": len(feat_cols),
        "rules": {"ALERT": "近4周|IC|<%.2f 且 缺失率>%.0f%%" % (IC_FLOOR, MISS_FLOOR * 100),
                  "WATCH": "近4周|IC|<%.2f 或 缺失率>%.0f%%" % (IC_FLOOR, MISS_FLOOR * 100)},
        "features": summary,
        "alerts": alerts,
        "watches": watches,
    }
    # P2-11 修复：--check 只读评估不落盘（dry-run 语义严格化）；非 check 才写 json/md
    if not args.check:
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print("[3/3] 生成周报 ..." if not args.check else "[3/3] dry-run：不落盘")
    lines = [
        "# 特征级 IC/覆盖率周报",
        "",
        "> 生成：%s" % report["generated_at"],
        "> 面板：%s（%d 特征）| 近 4 周横截面 IC + 缺失率" % (report["panel"], report["n_features"]),
        "",
        "## 判定规则",
        "",
        "- **ALERT**：%s" % report["rules"]["ALERT"],
        "- **WATCH**：%s" % report["rules"]["WATCH"],
        "",
        "## 特征健康表",
        "",
        "| 特征 | 近4周IC(abs均值) | 缺失率 | 判定 |",
        "|---|---|---|---|",
    ]
    for f in feat_cols:
        v = summary[f]
        ic = "%.4f" % v["ic_abs_mean_4w"] if v["ic_abs_mean_4w"] is not None else "—"
        ms = "%.2f%%" % (v["missing_rate_4w"] * 100) if v["missing_rate_4w"] is not None else "—"
        lines.append("| %s | %s | %s | **%s** |" % (f, ic, ms, v["verdict"]))
    lines += [
        "",
        "## 汇总",
        "",
        "- **ALERT（%d）**：%s" % (len(alerts), ", ".join(alerts) if alerts else "无"),
        "- **WATCH（%d）**：%s" % (len(watches), ", ".join(watches) if watches else "无"),
        "",
        "> 仅监控告警，特征去留需人工裁决；告警特征在下一轮重训前建议核查数据源/口径。",
    ]
    if not args.check:
        with open(OUT_MD, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    if not args.check:
        print("    JSON:", OUT_JSON)
        print("    MD  :", OUT_MD)
    else:
        print("    [CHECK] 仅评估：json/md 不落盘，未推送")
    print("    ALERT:", len(alerts), "| WATCH:", len(watches))
    if alerts and not args.check:
        _notify("【特征健康周报】%d 个特征 ALERT（近4周IC趋零且缺失率>5%%）：%s\nWATCH %d 个：%s" % (
            len(alerts), ", ".join(alerts), len(watches), ", ".join(watches) if watches else "无"))
    if args.check:
        print("[CHECK] dry-run：未推送")
    return 2 if (alerts and not args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
