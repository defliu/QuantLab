# coding: utf-8
"""阶段5：因子 IC 月度监控 + 失效因子识别 + 权重回灌建议。

对特征面板全部特征计算滚动 IC（与未来收益 fwd_ret 的 Spearman 秩相关）：
  - 每日横截面 IC（当天全市场 rank-corr）
  - 按月度聚合：IC 均值 / 标准差 / ICIR / 正IC月度占比
  - 全期 + 最近12月 两个视角，识别衰减与失效

输出：
  data/factor_ic_report.json    结构化报告（每特征 IC/ICIR/判定 + 建议特征集）
  data/factor_ic_report.md      月度因子健康报告（markdown）

判定规则（短线日频，IC 量级本身较低）：
  - 有效：|ICIR| >= 0.15 且 IC 方向稳定
  - 观察：0.05 <= |ICIR| < 0.15
  - 失效：|ICIR| < 0.05 或 最近12月 IC 持续为负

用法：
  python factor_ic_monitor.py --panel data/feature_panel_v2.parquet --meta data/features_v2.json
"""
import argparse
import json
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "data", "factor_ic_report.json")
OUT_MD = os.path.join(HERE, "data", "factor_ic_report.md")

ICIR_STRONG = 0.15
ICIR_WEAK = 0.05


def calc_daily_ic(panel, feat_cols, ret_col="fwd_ret"):
    """逐日计算每个特征的横截面 IC（秩相关）。返回 DataFrame[date x feature] 的每日 IC。"""
    rows = []
    for d, g in panel.groupby("trade_date"):
        if len(g) < 20:
            continue
        r = g[feat_cols].rank()
        rr = g[ret_col].rank()
        ic = r.corrwith(rr)
        rows.append({"date": d, **ic.to_dict()})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=os.path.join(HERE, "data", "feature_panel_v2.parquet"))
    ap.add_argument("--meta", default=os.path.join(HERE, "data", "features_v2.json"))
    args = ap.parse_args()

    print("[1/4] 读取特征面板 ...")
    panel = pd.read_parquet(args.panel)
    meta = json.load(open(args.meta, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    print(f"    行数 {len(panel):,} | 特征 {len(feat_cols)} | 区间 {panel['trade_date'].min().date()} ~ {panel['trade_date'].max().date()}")

    print("[2/4] 计算逐日横截面 IC ...")
    ic_daily = calc_daily_ic(panel, feat_cols)
    ic_daily["ym"] = ic_daily["date"].dt.strftime("%Y-%m")
    print(f"    交易日 {len(ic_daily):,} 个")

    print("[3/4] 月度聚合与因子评估 ...")
    # 月度 IC
    monthly = ic_daily.groupby("ym")[feat_cols].mean()
    # 全期指标
    summary = {}
    for f in feat_cols:
        s = ic_daily[f].dropna()
        if len(s) < 20:
            summary[f] = {"ic_mean": None, "ic_std": None, "icir": None, "positive_ratio": None, "last12_mean": None}
            continue
        last12 = s.tail(12 * 21)  # 最近约12个月
        ic_mean = float(s.mean())
        ic_std = float(s.std())
        summary[f] = {
            "ic_mean": round(ic_mean, 5),
            "ic_std": round(ic_std, 5),
            "icir": round(ic_mean / ic_std, 4) if ic_std > 0 else None,
            "positive_ratio": round(float((s > 0).mean()), 3),
            "last12_mean": round(float(last12.mean()), 5),
        }

    # 判定
    for f, v in summary.items():
        if v["icir"] is None:
            v["verdict"] = "数据不足"
            continue
        a = abs(v["icir"])
        if v["last12_mean"] is not None and v["last12_mean"] < 0 and v["ic_mean"] > 0:
            v["verdict"] = "衰减(近期转负)"
        elif a >= ICIR_STRONG:
            v["verdict"] = "有效"
        elif a >= ICIR_WEAK:
            v["verdict"] = "观察"
        else:
            v["verdict"] = "失效"

    # 回灌建议：按 ICIR 绝对值排序，给出建议特征集
    ranked = sorted(summary.items(), key=lambda kv: -(abs(kv[1]["icir"]) if kv[1]["icir"] else 0))
    keep = [f for f, v in ranked if v["verdict"] in ("有效", "观察")]
    drop = [f for f, v in ranked if v["verdict"] == "失效"]

    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "panel": os.path.basename(args.panel),
        "n_features": len(feat_cols),
        "ic_daily_days": int(len(ic_daily)),
        "rules": {"有效": f"|ICIR|>={ICIR_STRONG}", "观察": f"{ICIR_WEAK}<=|ICIR|<{ICIR_STRONG}", "失效": f"|ICIR|<{ICIR_WEAK} 或近期转负"},
        "feature_summary": summary,
        "ranked_features": [f for f, _ in ranked],
        "monthly_ic_mean": {ym: round(float(monthly.loc[ym].mean()), 5) for ym in monthly.index},
        "suggestion": {
            "keep": keep,
            "drop": drop,
            "note": "下一轮重训建议：保留 keep 特征，剔除 drop 特征；可将失效特征从 build_features_v2 特征列表移除后重跑阶段0-2",
        },
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("[4/4] 生成月度因子健康报告 ...")
    lines = [
        "# 月度因子健康报告（阶段5 · IC 监控）",
        "",
        f"> 生成时间：{report['generated_at']}",
        f"> 数据：{report['panel']}（{report['n_features']} 特征，{report['ic_daily_days']} 个交易日的横截面 IC）",
        "",
        "## 一、因子 IC / ICIR 排名",
        "",
        "| 排名 | 特征 | IC均值 | ICIR | 正IC占比 | 近12月IC | 判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, (f, v) in enumerate(ranked, 1):
        ic = f"{v['ic_mean']:.5f}" if v["ic_mean"] is not None else "—"
        ir = f"{v['icir']:.3f}" if v["icir"] is not None else "—"
        pr = f"{v['positive_ratio']:.2f}" if v["positive_ratio"] is not None else "—"
        l12 = f"{v['last12_mean']:.5f}" if v["last12_mean"] is not None else "—"
        lines.append(f"| {i} | {f} | {ic} | {ir} | {pr} | {l12} | {v['verdict']} |")

    lines += [
        "",
        "## 二、月度整体 IC 趋势",
        "",
        "| 月份 | 全特征平均IC |",
        "|---|---|",
    ]
    for ym, v in report["monthly_ic_mean"].items():
        lines.append(f"| {ym} | {v:.5f} |")

    lines += [
        "",
        "## 三、回灌建议",
        "",
        f"- **保留（{len(keep)} 个）**：{', '.join(keep)}",
        f"- **剔除（{len(drop)} 个）**：{', '.join(drop) if drop else '无'}",
        "",
        "> 下一轮重训：剔除失效特征后重跑 build_features_v2 → train_optuna，对比 IC/ICIR 是否提升。",
        "> ⚠️ 免责声明：仅供研究，不构成投资建议。",
    ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    JSON:", OUT_JSON)
    print("    MD  :", OUT_MD)
    print("    有效:", len(keep), "| 观察:", sum(1 for v in summary.values() if v["verdict"] == "观察"),
          "| 失效:", len(drop))


if __name__ == "__main__":
    main()
