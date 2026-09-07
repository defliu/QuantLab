# -*- coding: utf-8 -*-
"""前向纸面验证统计 forward_stats.py —— 只读，不写 live 日志。

功能：对 paper_forward_live.csv 中已回填的样本，按「同窗口全市场等权」配对计算超额收益，
      并输出 TOP2（实盘口径，g2_config.TOP_N=2）与 Top10 全集两个维度的统计。

用法：
    python forward_stats.py              # 输出到 stdout + data/real/forward_stats_<date>.md
    python forward_stats.py --hold 10    # 指定持有期（默认取 live 里的 hold 列）

设计要点：
1. 基准 = 与样本完全同窗口（同一 entry 日 open → 同一 exit 日 open）的全市场等权收益，
   与回测 scan_rotate_cost_real.stats() 中「全市场 fwd_ret 横截面均值」口径同源，且逐笔配对更干净。
2. 样本量小时（N<30）自动标注"样本不足，不作判定"，避免过早下结论。
3. 只读：不修改 paper_forward_live.csv。
"""
import argparse
import datetime
import math
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
os.chdir(PROJ)
REAL = os.path.join(PROJ, "data", "real")

import paper_forward as PF  # noqa: E402  （复用其数据源合并逻辑，保证与 backfill 同口径）


def build_wide():
    daily, main_last, inc_last = PF.load_open_panel()
    wide = daily["open"].unstack("ts_code")
    trades = sorted(wide.index)
    return wide, trades, main_last, inc_last


def market_ret(wide, d0, d1):
    """同窗口全市场等权收益（entry open → exit open），返回 (收益, 样本股数)。"""
    if d0 not in wide.index or d1 not in wide.index:
        return np.nan, 0
    s0 = wide.loc[d0]
    s1 = wide.loc[d1]
    ok = s0.notna() & s1.notna() & (s0 > 0) & (s1 > 0)
    if ok.sum() == 0:
        return np.nan, 0
    r = (s1[ok] / s0[ok] - 1)
    return float(r.mean()), int(ok.sum())


def summarize(sub, label):
    n = len(sub)
    if n == 0:
        return None
    mean = float(sub["ret"].mean())
    bench = float(sub["bench"].mean())
    exc = float(sub["excess"].mean())
    win = float((sub["ret"] > 0).mean())
    win_exc = float((sub["excess"] > 0).mean())
    sd = float(sub["excess"].std(ddof=1)) if n > 1 else np.nan
    t = exc / (sd / math.sqrt(n)) if (n > 1 and sd and not np.isnan(sd) and sd > 0) else np.nan
    return {"label": label, "n": n, "ret": mean, "bench": bench, "excess": exc,
            "win": win, "win_exc": win_exc, "sd": sd, "t": t,
            "median_excess": float(sub["excess"].median())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=0, help="持有期；0=取 live 中的 hold 列")
    args = ap.parse_args()

    live_csv = os.path.join(REAL, "paper_forward_live.csv")
    df = pd.read_csv(live_csv, encoding="utf-8-sig")
    done = df[df["ret"].notna()].copy()
    if len(done) == 0:
        print("尚无已回填样本（ret 为空）。先跑 paper_forward.py --backfill --hold 10")
        return
    wide, trades, main_last, inc_last = build_wide()
    tset = pd.Index(trades)

    rows = []
    for _, r in done.iterrows():
        hold = int(args.hold or r.get("hold") or 10)
        d = pd.Timestamp(r["date"])
        idx = next((j for j, x in enumerate(trades) if x > d), None)
        if idx is None:
            continue
        exit_j = idx + hold
        if exit_j >= len(trades):
            continue
        d0, d1 = trades[idx], trades[exit_j]
        b, nstock = market_ret(wide, d0, d1)
        rows.append({
            "date": r["date"], "code": r["code"], "rank": int(r.get("rank", 0) or 0),
            "entry_day": str(d0)[:10], "exit_day": str(d1)[:10],
            "entry": r["entry"], "exit": r["exit"], "ret": r["ret"],
            "bench": b, "excess": r["ret"] - b if not np.isnan(b) else np.nan,
            "nstock": nstock,
        })
    res = pd.DataFrame(rows)
    res = res.sort_values(["date", "rank"]).reset_index(drop=True)

    pending = df[df["ret"].isna()]
    stats = [summarize(res, "全部"), summarize(res[res["rank"] <= 2], "TOP2(实盘口径)")]
    stats = [s for s in stats if s]

    print("\n=== 前向纸面验证统计（hold=%s）===" % (args.hold or "live"))
    print("数据源: 主库最新 %s | 增量最新 %s" % (main_last, inc_last))
    print("已回填 %d 笔 / 待回填 %d 笔 / 候选累计 %d 笔" % (len(res), len(pending), len(df)))
    for s in stats:
        print("  [%s] n=%d 绝对%+.3f%% 基准%+.3f%% 超额%+.3f%% 胜率%.1f%% 超额胜率%.1f%% t=%.2f"
              % (s["label"], s["n"], s["ret"] * 100, s["bench"] * 100, s["excess"] * 100,
                 s["win"] * 100, s["win_exc"] * 100, s["t"] if not np.isnan(s["t"]) else float("nan")))
    print("\n逐笔明细:")
    print(res[["date", "code", "rank", "entry_day", "exit_day", "ret", "bench", "excess"]]
          .to_string(index=False,
                     formatters={"ret": lambda x: "%+.2f%%" % (x * 100),
                                 "bench": lambda x: "%+.2f%%" % (x * 100) if not np.isnan(x) else "NA",
                                 "excess": lambda x: "%+.2f%%" % (x * 100) if not np.isnan(x) else "NA"}))

    # ---- Markdown 报告 ----
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"# g2 前向纸面验证统计 · {now}", "",
          f"> 数据源：主库最新 {main_last} | 增量最新 {inc_last} | 持有期 {args.hold or 'live'} 日 open→open",
          f"> 口径：基准=同窗口全市场等权（与回测 market_daily 横截面等权同源，逐笔配对）", "",
          f"**已回填 {len(res)} 笔 / 待回填 {len(pending)} 笔 / 候选累计 {len(df)} 笔**", "",
          "| 维度 | 样本 | 绝对收益 | 基准 | 超额 | 胜率 | 超额胜率 | t值 |",
          "|---|---|---|---|---|---|---|---|"]
    for s in stats:
        md.append("| %s | %d | %+.3f%% | %+.3f%% | %+.3f%% | %.1f%% | %.1f%% | %s |" % (
            s["label"], s["n"], s["ret"] * 100, s["bench"] * 100, s["excess"] * 100,
            s["win"] * 100, s["win_exc"] * 100,
            ("%.2f" % s["t"]) if not np.isnan(s["t"]) else "NA"))
    md += ["", "## 逐笔明细", "",
           "| 选股日 | 代码 | rank | 入场日 | 出场日 | 绝对 | 基准 | 超额 |",
           "|---|---|---|---|---|---|---|---|"]
    for _, r in res.iterrows():
        md.append("| %s | %s | %d | %s | %s | %+.2f%% | %s | %s |" % (
            r["date"], r["code"], r["rank"], r["entry_day"], r["exit_day"], r["ret"] * 100,
            "%+.2f%%" % (r["bench"] * 100) if not np.isnan(r["bench"]) else "NA",
            "%+.2f%%" % (r["excess"] * 100) if not np.isnan(r["excess"]) else "NA"))
    N_MIN = 30
    md += ["", "## 判定", ""]
    if len(res) < N_MIN:
        md.append(f"⚠️ 有效样本 {len(res)} 笔 < 判定门槛 {N_MIN} 笔，**不作任何结论**。"
                  f"当前进度 {len(res)}/{N_MIN}（{len(res) / N_MIN:.0%}）。")
        md.append("")
        md.append("按当前速度（约 2 笔/交易日，仅 TOP2 口径）需再约 %d 个交易日；"
                  "若按 Top10 全集口径约 %d 个交易日。"
                  % (math.ceil((N_MIN - len(res)) / 2), math.ceil((N_MIN - len(res)) / 10)))
    else:
        md.append(f"样本已达 {len(res)} 笔，可进入判定：超额 >0 且 t>2 视为正向证据。")
    out = os.path.join(REAL, f"forward_stats_{datetime.datetime.now().strftime('%Y%m%d')}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n报告:", out)


if __name__ == "__main__":
    main()
