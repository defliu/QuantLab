# -*- coding: utf-8 -*-
"""PK_OUT 开关对照回测 ablate_pkout.py（T-20260904-001 的前置证据，只读回测，不碰生产代码）。

背景：实盘 V1.x（V1.0 起，git 2634f5d 已存在；非 V1.3 引入，VERSIONS.md 登记 V1.3 仅模型换版）
的卖出条件比回测多一条「掉出当日 top2 即卖（PK_OUT）」，
`scan_rotate_cost_real.py` 原本只有「止损 -7% / 止盈 +15% / 持有期满 N」三条 →
实盘跑的是回测没测过的策略（train-serving skew）。本脚本在同一份面板上做开关对照，
量化 PK_OUT 到底损失了多少日超额，为「是否删除 PK_OUT 改 N=5 到期制」提供决策依据。

设计：一次 build_per_day（最耗时）→ 多次 simulate（快），全部组合在同一份数据上比较，
避免面板版本差异干扰。PK_OUT 关闭组应复现既有基线（红线58/滑点0.1%，真实评分卡版：
N=1 -0.255% / N=3 -0.168% / N=5 -0.039% / N=10 -0.027%，来源 data/real/scan_rotate_cost_real_report.md），
用于自检口径未被改坏。注意：既有基线必须取与引擎同源的真实版报告（scan_rotate_cost_real），
代理版（scan_rotate_cost_report.md，N=1 -0.348%）因面板/模型不同（feature_panel_v3 vs _v3_sc）不能作自检基准。

用法：
    python ablate_pkout.py                 # 默认红线58、滑点0.1%、N∈{1,3,5,10}
    python ablate_pkout.py --thr 60        # 红线60（g2 口径）稳健性检查
    python ablate_pkout.py --nlist 5,10    # 只跑部分持有期
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

# 环境变量需在 import 之前设置（BT_SLIPS/BT_N_LIST 仅影响 main()，本脚本直接调 simulate，仍设以防歧义）
os.environ["BT_SLIPS"] = "0.001"
os.environ["BT_N_LIST"] = "1,3,5,10"

import scan_rotate_cost_real as BT  # noqa: E402


def run_one(dates, per_day, market_daily, open_map, N, slip, pk_out):
    """跑单组配置，返回统计字典。"""
    BT.PK_OUT = pk_out
    BT.SELL_SKIP_DOWN[0] = 0
    BT.SELL_DELIST[0] = 0
    trades, daily_ret, n_skip = BT.simulate(dates, per_day, N, slip, open_map, exec_ok=True)
    s = BT.stats(trades, daily_ret, market_daily)
    hold_days = [t[1] - t[0] - 1 for t in trades]  # buy_i 决策 → buy_i+1 开盘买入 → sell_i 开盘卖出
    fwd = pd.Series([t[2] for t in trades]) if trades else pd.Series(dtype=float)
    return {
        "N": N, "PK_OUT": pk_out, "n_trades": len(trades),
        "daily_excess": s["daily_excess"], "win_rate": s["win_rate"],
        "pl_ratio": s["profit_loss_ratio"], "mdd": s["max_drawdown"],
        "avg_hold": float(np.mean(hold_days)) if hold_days else np.nan,
        "med_hold": float(np.median(hold_days)) if hold_days else np.nan,
        "mean_ret": float(fwd.mean()) if len(fwd) else np.nan,
        "turnover": len(trades) / max(1, len(dates)),
        "n_skip": n_skip, "skip_down": BT.SELL_SKIP_DOWN[0], "delist": BT.SELL_DELIST[0],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=58.0, help="红线阈值（58=V1.3口径，60=g2口径）")
    ap.add_argument("--slip", type=float, default=0.001, help="单边滑点")
    ap.add_argument("--nlist", default="1,3,5,10", help="持有期列表，逗号分隔")
    args = ap.parse_args()

    ns = [int(x) for x in args.nlist.split(",") if x.strip()]
    print("[1/3] 加载面板/模型/逐日打分（真实 F2/F5）...")
    dates, per_day, market_daily, open_map = BT.build_per_day()
    BT.THRESHOLD = args.thr
    print("    测试期 %s ~ %s | %d 日 | 红线 %.0f | 滑点 %.1f%% | 持仓 %d"
          % (dates[0].date(), dates[-1].date(), len(dates), args.thr, args.slip * 100, BT.TOP))

    print("[2/3] 跑开关对照（每组 %d 次 simulate）..." % (len(ns) * 2))
    rows = []
    for N in ns:
        for pk in (False, True):
            r = run_one(dates, per_day, market_daily, open_map, N, args.slip, pk)
            rows.append(r)
            print("    N=%-2d PK_OUT=%-5s 交易%4d 日超额%+.3f%% 胜率%.1f%% 盈亏比%.2f 回撤%.1f%% 均持有%.2f日"
                  % (N, "ON" if pk else "OFF", r["n_trades"], r["daily_excess"] * 100,
                     r["win_rate"] * 100, r["pl_ratio"], r["mdd"] * 100, r["avg_hold"]))
    df = pd.DataFrame(rows)

    print("[3/3] 汇总 PK_OUT 的影响（ON - OFF）...")
    cmp_rows = []
    for N in ns:
        off = df[(df["N"] == N) & (~df["PK_OUT"])].iloc[0]
        on = df[(df["N"] == N) & (df["PK_OUT"])].iloc[0]
        cmp_rows.append({
            "N": N,
            "exc_off": off["daily_excess"], "exc_on": on["daily_excess"],
            "delta_pp": (on["daily_excess"] - off["daily_excess"]) * 100,
            "hold_off": off["avg_hold"], "hold_on": on["avg_hold"],
            "trade_off": off["n_trades"], "trade_on": on["n_trades"],
            "mdd_off": off["mdd"], "mdd_on": on["mdd"],
            "win_off": off["win_rate"], "win_on": on["win_rate"],
        })
    cmp = pd.DataFrame(cmp_rows)
    print()
    print("    持有期 | OFF日超额 | ON日超额 | 差(pp) | 均持有OFF | 均持有ON | 交易数OFF→ON")
    print("    " + "-" * 78)
    for _, r in cmp.iterrows():
        print("    N=%-4d| %+8.3f%% | %+8.3f%% | %+6.3f | %8.2f | %8.2f | %4d → %4d"
              % (r["N"], r["exc_off"] * 100, r["exc_on"] * 100, r["delta_pp"],
                 r["hold_off"], r["hold_on"], r["trade_off"], r["trade_on"]))

    # ---- Markdown 报告 ----
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [
        "# PK_OUT 开关对照回测（T-20260904-001 前置证据）", "",
        f"> 生成：{now} | 红线 {args.thr:.0f} | 滑点 {args.slip:.1%} | 持仓 {BT.TOP} | 口径 open→open 可执行（含成本）",
        f"> 测试期 {dates[0].date()} ~ {dates[-1].date()}（{len(dates)} 交易日）", "",
        "## 结论先行", "",
    ]
    worst = cmp.loc[cmp["delta_pp"].idxmin()]
    best = cmp.loc[cmp["delta_pp"].idxmax()]
    md.append(f"- **PK_OUT 在所有持有期上均为负优化**：最差 N={worst['N']:.0f} **{worst['delta_pp']:+.3f}pp** ~ "
              f"最好 N={best['N']:.0f} **{best['delta_pp']:+.3f}pp**，日超额差全部 ≤ 0。"
              if (cmp["delta_pp"] <= 0).all() else
              f"- PK_OUT 影响不一：最好 N={best['N']:.0f} {best['delta_pp']:+.3f}pp，最差 N={worst['N']:.0f} {worst['delta_pp']:+.3f}pp。")
    md += [
        f"- **机制**：PK_OUT 把平均持有期从 {cmp['hold_off'].min():.2f}~{cmp['hold_off'].max():.2f} 日"
        f"压缩到 {cmp['hold_on'].min():.2f}~{cmp['hold_on'].max():.2f} 日，"
        f"交易数放大 {cmp['trade_on'].sum() / max(1, cmp['trade_off'].sum()):.1f} 倍，"
        f"而回测已证明「持有期↑ → 超额↑」的单调关系，提前卖出即吃掉这段 alpha。",
        "", "## 逐持有期对照", "",
        "| 持有期 | PK_OUT | 交易数 | 日均超额 | 胜率 | 盈亏比 | 最大回撤 | 平均持有(日) | 中位持有(日) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        md.append("| N=%d | %s | %d | %+.3f%% | %.1f%% | %.2f | %.1f%% | %.2f | %.1f |" % (
            r["N"], "ON" if r["PK_OUT"] else "OFF", r["n_trades"], r["daily_excess"] * 100,
            r["win_rate"] * 100, r["pl_ratio"], r["mdd"] * 100, r["avg_hold"], r["med_hold"]))
    md += ["", "## PK_OUT 的影响（ON − OFF）", "",
           "| 持有期 | OFF 日超额 | ON 日超额 | 差(pp) | 均持有 OFF | 均持有 ON | 交易数 OFF→ON | 回撤 OFF→ON |",
           "|---|---|---|---|---|---|---|---|"]
    for _, r in cmp.iterrows():
        md.append("| N=%d | %+.3f%% | %+.3f%% | **%+.3f** | %.2f | %.2f | %d → %d | %.1f%% → %.1f%% |" % (
            r["N"], r["exc_off"] * 100, r["exc_on"] * 100, r["delta_pp"],
            r["hold_off"], r["hold_on"], r["trade_off"], r["trade_on"],
            r["mdd_off"] * 100, r["mdd_on"] * 100))
    md += ["", "## 口径自检", "",
           "PK_OUT=OFF 组应复现既有基线（红线58 / 滑点0.1% / 真实评分卡版，来源 data/real/scan_rotate_cost_real_report.md）：",
           "N=1 -0.255% / N=3 -0.168% / N=5 -0.039% / N=10 -0.027%。",
           "若偏差 >0.05pp 说明本次改动影响了原口径，需回退排查。",
           "（注：早期代理版基线 N=1 -0.348% 来自 feature_panel_v3 + _v3 模型，与本引擎面板/模型不同源，不作自检基准。）",
           "", "| 持有期 | 本次 OFF | 既有基线 | 偏差 |", "|---|---|---|---|"]
    base = {1: -0.255, 3: -0.168, 5: -0.039, 10: -0.027}
    for N in ns:
        v = df[(df["N"] == N) & (~df["PK_OUT"])]["daily_excess"].iloc[0] * 100
        b = base.get(N)
        md.append("| N=%d | %+.3f%% | %s | %s |" % (
            N, v, ("%+.3f%%" % b) if b is not None else "NA",
            ("%+.3fpp" % (v - b)) if b is not None else "NA"))
    out = os.path.join(REAL, "pkout_ablation_%s_thr%d.md" % (
        datetime.datetime.now().strftime("%Y%m%d"), int(args.thr)))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n报告:", out)
    df.to_csv(out.replace(".md", ".csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
