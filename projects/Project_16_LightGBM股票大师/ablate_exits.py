# -*- coding: utf-8 -*-
"""P16 追盈止损（出场规则）横向对照回测 ablate_exits.py —— 只读回测，不碰生产代码。

背景（T-20260904-002 追盈止损方案选型的证据）：
1. 实盘真跑的出场规则是 **-7% 硬止损 + 峰值回撤 8% 移动止盈 + 15% 止盈**（V1.3 qmt_config.py、
   G2 strategy_p16_g2_bridge_src.py 同源），但回测 `scan_rotate_cost_real.py` 里**只有**
   「-7% 止损 / +15% 止盈 / 持有期满」三条，**从来没有移动止盈（追盈）**——
   即「追盈」是 live-only、零回测验证的第四条规则，与已证实的 PK_OUT 属同一类 train-serving skew。
2. 两侧都用**固定百分比**阈值。A 股小市值个股波动率离散度极大，同一条 -7%/
   8% 线对低波票过松（利润全吐）、对高波票过紧（被噪声洗出）。业界替代方案主要是
   波动率自适应（ATR / Chandelier Exit，Wilder 1978；Le Beau 3×ATR）。
3. 但 P16 是 5~10 日的横截面 alpha（偏向均值回复），而非趋势跟踪——ATR 类规则在趋势
   跟踪上的优势不能外推。故必须用本引擎自测，不能直接采信外部结论。

设计：一次 build_per_day（最耗时）→ 多次 simulate（快），全部模式在同一份面板上比较。
EXIT_MODE=fixed 组应复现既有基线（红线58/滑点0.1%：N=1 -0.255% / N=3 -0.168% /
N=5 -0.039% / N=10 -0.027%），用于自检口径未被改坏。

用法：
    python ablate_exits.py                      # 默认红线58、滑点0.1%、N∈{5,10}
    python ablate_exits.py --thr 60             # g2 口径稳健性检查
    python ablate_exits.py --nlist 3,5,10       # 指定持有期
"""
import argparse
import datetime
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
sys.path.insert(0, PROJ)
os.chdir(PROJ)

os.environ["BT_SLIPS"] = "0.001"
os.environ["BT_N_LIST"] = "5,10"

import scan_rotate_cost_real as BT  # noqa: E402

# 出场模式 → (展示名, EXIT_MODE, ATR_K1, ATR_K2)
MODES = [
    ("none",       "无止损(纯alpha上界)", "none",       0.0, 0.0),
    ("fixed",      "固定-7%/+15%(回测原口径)", "fixed",  0.0, 0.0),
    ("live_trail", "实盘口径:-7%+8%追盈+15%", "live_trail", 0.0, 0.0),
    ("atr25",      "ATR自适应 2.0/2.5",   "atr",       2.0, 2.5),
    ("atr20",      "ATR自适应 2.0/2.0",   "atr",       2.0, 2.0),
    ("atr30",      "ATR自适应 3.0/3.0",   "atr",       3.0, 3.0),
    ("time",       "时间止损(过半不盈换股)", "time",     0.0, 0.0),
    ("ma5",        "跌破MA5 + -7%止损",   "ma",        0.0, 0.0),
]


def run_one(dates, per_day, market_daily, open_map, N, slip, mode, k1, k2):
    BT.EXIT_MODE = mode
    BT.ATR_K1, BT.ATR_K2 = k1, k2
    BT.SELL_SKIP_DOWN[0] = 0
    BT.SELL_DELIST[0] = 0
    BT.EXIT_REASON_CNT.clear()
    trades, daily_ret, n_skip = BT.simulate(dates, per_day, N, slip, open_map, exec_ok=True)
    s = BT.stats(trades, daily_ret, market_daily)
    hold_days = [t[1] - t[0] - 1 for t in trades]
    fwd = pd.Series([t[2] for t in trades]) if trades else pd.Series(dtype=float)
    mdd = abs(float(s["max_drawdown"])) or 1e-9
    reasons = dict(BT.EXIT_REASON_CNT)
    return {
        "N": N, "mode": mode, "n_trades": len(trades),
        "daily_excess": s["daily_excess"], "win_rate": s["win_rate"],
        "pl_ratio": s["profit_loss_ratio"], "mdd": s["max_drawdown"],
        "calmar": float(s["daily_excess"]) * 252 / mdd,
        "avg_hold": float(np.mean(hold_days)) if hold_days else np.nan,
        "mean_ret": float(fwd.mean()) if len(fwd) else np.nan,
        "std_ret": float(fwd.std()) if len(fwd) > 1 else np.nan,
        "pct_lt_stop": float((fwd <= -0.07).mean()) if len(fwd) else np.nan,
        "turnover": len(trades) / max(1, len(dates)),
        "skip_down": BT.SELL_SKIP_DOWN[0], "delist": BT.SELL_DELIST[0],
        "reasons": "; ".join("%s:%d" % (k, v) for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
        "_daily_ret": pd.Series(daily_ret).reset_index(drop=True) if daily_ret is not None else None,
    }


def paired_t(a, b):
    """配对 t 检验：a/b 为同长度日收益序列，返回 (日均值差pp, t值)。

    日超额的共同基准（market_daily）在两组相减时抵消，故直接对日收益序列配对即可。
    """
    if a is None or b is None or len(a) != len(b) or len(a) < 10:
        return np.nan, np.nan
    d = (a - b).dropna()
    if len(d) < 10 or d.std() == 0:
        return np.nan, np.nan
    return float(d.mean() * 100), float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=58.0, help="红线阈值（58=V1.3口径，60=g2口径）")
    ap.add_argument("--thrs", default="", help="多阈值，逗号分隔（优先于 --thr，用于稳健性）")
    ap.add_argument("--slip", type=float, default=0.001, help="单边滑点")
    ap.add_argument("--nlist", default="5,10", help="持有期列表，逗号分隔")
    args = ap.parse_args()

    thrs = [float(x) for x in args.thrs.split(",") if x.strip()] or [args.thr]
    ns = [int(x) for x in args.nlist.split(",") if x.strip()]
    print("[1/3] 加载面板/模型/逐日打分（真实 F2/F5）+ 计算 ATR14/MA5 ...")
    dates, per_day, market_daily, open_map = BT.build_per_day()
    print("    测试期 %s ~ %s | %d 日 | 滑点 %.1f%% | 持仓 %d | ATR样本 %d"
          % (dates[0].date(), dates[-1].date(), len(dates), args.slip * 100,
             BT.TOP, len(BT._ATR_MAP)))

    print("[2/3] 跑 %d 组（%d 阈值 × %d 模式 × %d 持有期）..."
          % (len(thrs) * len(MODES) * len(ns), len(thrs), len(MODES), len(ns)))
    rows, series = [], {}
    for thr in thrs:
        BT.THRESHOLD = thr
        for N in ns:
            series[(thr, N)] = {}
            for mode, label, em, k1, k2 in MODES:
                r = run_one(dates, per_day, market_daily, open_map, N, args.slip, em, k1, k2)
                r["label"], r["thr"] = label, thr
                rows.append(r)
                series[(thr, N)][mode] = r.pop("_daily_ret")
                print("    thr%.0f N=%-2d %-26s 交易%4d 日超额%+.3f%% 胜率%.1f%% 盈亏比%.2f 回撤%.1f%% Calmar%.2f 均持有%.2f日"
                      % (thr, N, label, r["n_trades"], r["daily_excess"] * 100, r["win_rate"] * 100,
                         r["pl_ratio"], r["mdd"] * 100, r["calmar"], r["avg_hold"]))
    df = pd.DataFrame(rows)

    # ---------- 显著性：各模式 vs fixed 的配对 t 检验（日收益序列逐日相减）----------
    print("[3/3] 显著性检验（各模式 vs fixed，配对 t）...")
    sig_rows = []
    for thr in thrs:
        for N in ns:
            base = series[(thr, N)].get("fixed")
            for mode, label, em, k1, k2 in MODES:
                if mode == "fixed":
                    continue
                d_pp, t = paired_t(series[(thr, N)].get(mode), base)
                sig_rows.append({"thr": thr, "N": N, "mode": mode, "label": label,
                                 "delta_pp": d_pp, "t": t})
    sig = pd.DataFrame(sig_rows)
    sig["mark"] = sig["t"].apply(
        lambda x: "**" if (not np.isnan(x) and abs(x) >= 2.6) else
                  ("*" if (not np.isnan(x) and abs(x) >= 1.96) else ""))
    for _, r in sig.iterrows():
        print("    thr%.0f N=%-2d %-26s 差%+7.3fpp/日 t=%+6.2f %s"
              % (r["thr"], r["N"], r["label"], r["delta_pp"], r["t"], r["mark"]))
    n_cmp = len(sig)
    # 先落盘中间结果：报告环节若再出 bug 不会白跑一次（build_per_day 最耗时）
    _stamp = datetime.datetime.now().strftime("%Y%m%d")
    _pre = os.path.join(REAL, "exit_ablation_%s_thr%s.md" % (_stamp, "-".join(str(int(t)) for t in thrs)))
    df.to_csv(_pre.replace(".md", ".csv"), index=False, encoding="utf-8-sig")
    sig.to_csv(_pre.replace(".md", "_significance.csv"), index=False, encoding="utf-8-sig")

    # ---------- Markdown 报告 ----------
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [
        "# P16 追盈止损（出场规则）横向对照回测", "",
        f"> 生成：{now} | 红线 {args.thr:.0f} | 滑点 {args.slip:.1%} | 持仓 {BT.TOP} | 口径 open→open 可执行（含成本、一字跌停过滤）",
        f"> 测试期 {dates[0].date()} ~ {dates[-1].date()}（{len(dates)} 交易日）| PK_OUT 全程关闭（V1.3 已删该规则）", "",
        "## 一、结论先行", "",
    ]
    for thr in thrs:
        for N in ns:
            sub = df[(df["N"] == N) & (df["thr"] == thr)].copy()
            base = sub[sub["mode"] == "fixed"].iloc[0]
            best = sub.loc[sub["daily_excess"].idxmax()]
            worst = sub.loc[sub["daily_excess"].idxmin()]
            lt = sub[sub["mode"] == "live_trail"].iloc[0]
            md.append(
                f"- **红线{thr:.0f} / N={N}**：相对回测原口径 fixed（{base['daily_excess']*100:+.3f}%/日），"
                f"最优是 **{best['label']}**（{best['daily_excess']*100:+.3f}%/日，"
                f"差 {(best['daily_excess']-base['daily_excess'])*100:+.3f}pp）；"
                f"最差是 {worst['label']}（{worst['daily_excess']*100:+.3f}%/日）。"
                f"实盘现跑的 live_trail = {lt['daily_excess']*100:+.3f}%/日"
                f"（差 {(lt['daily_excess']-base['daily_excess'])*100:+.3f}pp，"
                f"回撤 {base['mdd']*100:.1f}% → {lt['mdd']*100:.1f}%）。")
    md += [
        "", "## 二、显著性检验（各模式 vs fixed，日收益序列配对 t）", "",
        f"共 {n_cmp} 次比较。配对 t 对日收益序列逐日相减（共同基准 market_daily 相减时抵消）。",
        "`*`= |t|≥1.96（单次 5%% 显著）；`**`= |t|≥2.6（对 %d 次比较做 Bonferroni 校正后约 5%% 显著）。"
        % max(1, n_cmp),
        "**未标星的差值一律视为噪声，不得据此改参数。**", "",
        "| 红线 | 持有期 | 出场规则 | 日超额差(pp) | t 值 | 显著性 |", "|---|---|---|---|---|---|",
    ]
    for _, r in sig.iterrows():
        md.append("| %.0f | N=%d | %s | %+.3f | %+.2f | %s |" % (
            r["thr"], r["N"], r["label"], r["delta_pp"], r["t"], r["mark"] or "—"))
    md += [
        "", "## 三、逐模式明细", "",
        "| 红线 | 持有期 | 出场规则 | 交易数 | 日均超额 | 年化超额 | 胜率 | 盈亏比 | 最大回撤 | Calmar | 平均持有(日) | 单笔均值 | 单笔标准差 | 亏超7%占比 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        md.append("| %.0f | N=%d | %s | %d | %+.3f%% | %+.1f%% | %.1f%% | %.2f | %.1f%% | %.2f | %.2f | %+.2f%% | %.2f%% | %.1f%% |" % (
            r["thr"], r["N"], r["label"], r["n_trades"], r["daily_excess"] * 100, r["daily_excess"] * 252 * 100,
            r["win_rate"] * 100, r["pl_ratio"], r["mdd"] * 100, r["calmar"], r["avg_hold"],
            r["mean_ret"] * 100, (r["std_ret"] or 0) * 100, (r["pct_lt_stop"] or 0) * 100))
    md += ["", "## 四、出场原因构成（诊断：到底靠哪条规则在卖）", "",
           "| 红线 | 持有期 | 模式 | 出场原因计数（降序） |", "|---|---|---|---|"]
    for _, r in df.iterrows():
        md.append("| %.0f | N=%d | %s | %s |" % (r["thr"], r["N"], r["label"], r["reasons"] or "-"))
    md += [
        "", "## 五、口径自检", "",
        "fixed 组应复现既有基线（红线58 / 滑点0.1% / 真实评分卡版，来源 data/real/scan_rotate_cost_real_report.md）：",
        "N=1 -0.255% / N=3 -0.168% / N=5 -0.039% / N=10 -0.027%。若偏差 >0.05pp 说明本次改动影响了原口径，需回退排查。",
        "", "| 红线 | 持有期 | 本次 fixed | 既有基线 | 偏差 |", "|---|---|---|---|---|",
    ]
    base_map = {(58, 1): -0.255, (58, 3): -0.168, (58, 5): -0.039, (58, 10): -0.027}
    for thr in thrs:
        for N in ns:
            v = df[(df["N"] == N) & (df["thr"] == thr) & (df["mode"] == "fixed")]["daily_excess"].iloc[0] * 100
            b = base_map.get((int(thr), N))
            md.append("| %.0f | N=%d | %+.3f%% | %s | %s |" % (
                thr, N, v, ("%+.3f%%" % b) if b is not None else "NA（无同口径基线）",
                ("%+.3fpp" % (v - b)) if b is not None else "NA"))
    md += [
        "", "## 六、方法说明与边界", "",
        "1. 所有模式共用同一份面板、同一批候选、同一套成本与一字跌停过滤，唯一变量是出场规则，因此差值可归因。",
        "2. 移动止盈的峰值用「截至昨日最高价」判定，今日 high 在判定后才并入——避免用尚未走完的当日高点触发（未来函数）。",
        "3. 触发价为**次日/当日开盘价**（open→open 可执行口径），不假设盘中理想成交价；"
        "一字跌停日卖不掉则顺延，与实盘一致。",
        "4. ATR 用建仓日快照的 ATR14/close，持有期内不再重算（与实盘「建仓时定线」一致，避免参数漂移）。",
        "5. 本回测只覆盖 2024-07 ~ 2026-08 共约 516 个交易日，样本期偏短且只含一个完整风格周期；"
        "止损类规则的收益差异通常远小于其回撤差异，应优先看 Calmar / 最大回撤列。",
    ]
    out = os.path.join(REAL, "exit_ablation_%s_thr%s.md" % (
        datetime.datetime.now().strftime("%Y%m%d"),
        "-".join(str(int(t)) for t in thrs)))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n报告:", out)
    df.to_csv(out.replace(".md", ".csv"), index=False, encoding="utf-8-sig")
    sig.to_csv(out.replace(".md", "_significance.csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
