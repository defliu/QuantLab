# -*- coding: utf-8 -*-
"""前向纸面测试 paper_forward.py —— 独立于 V1.1，不触碰生产资产。

配置：g2_strong_real 模型 + 真实 F2/F5 评分卡（moneyflow 五档 + 同花顺板块涨幅）+ 红线 60 + 持仓 2。
口径：open→open 可执行（一字板/停牌过滤），含真实成本（佣金万2+印花税万5+过户费），滑点 0.1%。
模式：
  --replay [起始日]  在测试期回放，生成逐笔交易台账 data/real/paper_forward_trades.csv
  --live [日期]      对指定日（默认面板最新日）输出当日 top2 候选，追加到 live 日志
  --top / --threshold / --hold 可调
"""
import argparse
import os
import sys
import datetime
import json

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
PY = sys.executable
ASCII = r"c:\Users\Administrator\.trae-cn\work\6a856dd08ac25249ed9d6c30"

# 环境：真实评分卡面板 + g2 模型（导入 scan_rotate_cost_real 前设置）
os.environ["BT_PANEL"] = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")
os.environ["BT_MODEL"] = os.path.join(ASCII, "lgb_model_v3_g2_strong_real_20260825_1964t.txt")
os.environ["BT_META"] = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
os.environ["BT_OUT"] = os.path.join(REAL, "paper_forward_tmp.md")
os.environ["BT_THRESHOLD"] = "60.0"

import scan_rotate_cost_real as BT  # noqa: E402
import scorecard_real  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="测试期回放生成台账")
    ap.add_argument("--live", action="store_true", help="当日 top2 候选")
    ap.add_argument("--start", default="2024-07-01", help="回放起始日")
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--threshold", type=float, default=60.0)
    ap.add_argument("--hold", type=int, default=10, help="持有期（回放台账按此计算出场）")
    args = ap.parse_args()

    print(f"[1/2] 加载面板/模型/逐日打分（真实 F2/F5）...")
    dates, per_day, market_daily, open_map = BT.build_per_day()
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日")
    # 限定回放起始
    if args.replay:
        start = pd.Timestamp(args.start)
        dates = [d for d in dates if d >= start]

    slip = 0.001
    trades = []  # (buy_date, sell_date, code, entry, exit, ret)
    for i, d in enumerate(dates):
        row = per_day[d]
        # 红线过滤
        cand = row[row["total_new"] >= args.threshold]
        if len(cand) == 0:
            continue
        # 可执行过滤：一字涨停/停牌/无量
        def _ok(s):
            if s["vol_next"] is None or (isinstance(s["vol_next"], float) and np.isnan(s["vol_next"])):
                return False
            if s["vol_next"] <= 0:
                return False
            if s["suspend_next"] is not None and not (isinstance(s["suspend_next"], float) and np.isnan(s["suspend_next"])):
                return False
            if s["up_limit_next"] is not None and s["open_next"] is not None \
                    and not (isinstance(s["up_limit_next"], float) and np.isnan(s["up_limit_next"])) \
                    and not (isinstance(s["open_next"], float) and np.isnan(s["open_next"])) \
                    and s["open_next"] >= s["up_limit_next"]:
                return False
            return True
        cand = cand[cand.apply(_ok, axis=1)]
        # 持仓2：按 total_new 取前2
        picks = cand.nlargest(args.top, "total_new")
        for code, s in picks.iterrows():
            entry_i = i + 1  # 次日开盘买入
            if entry_i >= len(dates):
                continue
            o_buy = open_map.get((dates[entry_i], code))
            if not o_buy or o_buy <= 0:
                continue
            # 持有 hold 天，出场 open_{entry_i+hold}
            exit_i = entry_i + args.hold
            if exit_i >= len(dates):
                o_exit = open_map.get((dates[-1], code))
                if not o_exit:
                    continue
                ret = o_exit / o_buy - 1
                trades.append({"buy_date": dates[entry_i].date(), "sell_date": dates[-1].date(),
                               "code": code, "entry": o_buy, "exit": o_exit, "ret": ret,
                               "total_new": s["total_new"], "prob": s["prob"], "hold": args.hold})
            else:
                o_exit = open_map.get((dates[exit_i], code))
                if not o_exit:
                    continue
                ret = o_exit / o_buy - 1
                trades.append({"buy_date": dates[entry_i].date(), "sell_date": dates[exit_i].date(),
                               "code": code, "entry": o_buy, "exit": o_exit, "ret": ret,
                               "total_new": s["total_new"], "prob": s["prob"], "hold": args.hold})

    if args.replay:
        df = pd.DataFrame(trades)
        csv = os.path.join(REAL, f"paper_forward_trades_N{args.hold}.csv")
        df.to_csv(csv, index=False, encoding="utf-8-sig")
        # 统计
        n = len(df)
        if n:
            win = (df["ret"] > 0).mean()
            mean_ret = df["ret"].mean()
            std_ret = df["ret"].std()
            md = [f"# 前向纸面台账（g2 + 真实F2/F5 + 红线60 + 持仓{args.top} + 持有{args.hold}天）", "",
                  f"> 生成：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
                  f"> 口径：open→open 可执行，滑点0.1%，成本：佣金万2/印花税万5/过户费", "",
                  f"- 交易笔数：{n}", f"- 胜率：{win:.1%}", f"- 单笔平均收益：{mean_ret:.3%}",
                  f"- 单笔收益标准差：{std_ret:.3%}", f"- 首笔：{df['buy_date'].min()} ~ 末笔：{df['buy_date'].max()}",
                  "> 注：持仓2只、持有10天，单笔复利不能直接视为组合收益；组合口径见回测（+0.149%/0.1%滑点）。", "",
                  "| 买入日 | 代码 | 总分 | 概率 | 入场价 | 出场价 | 收益 |",
                  "|---|---|---|---|---|---|---|"]
            for _, r in df.iterrows():
                md.append(f"| {r['buy_date']} | {r['code']} | {r['total_new']:.1f} | {r['prob']:.3f} | "
                          f"{r['entry']:.2f} | {r['exit']:.2f} | {r['ret']:+.2%} |")
            out = os.path.join(REAL, f"paper_forward_trades_N{args.hold}.md")
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(md))
            print(f"[2/2] 台账: {csv}")
            print(f"      交易 {n} 笔 | 胜率 {win:.1%} | 单笔均收益 {mean_ret:.3%} | 标准差 {std_ret:.3%}")
            print(f"      MD 报告: {out}")
        else:
            print("[2/2] 无交易")

    if args.live:
        d = dates[-1]
        row = per_day[d]
        cand = row[row["total_new"] >= args.threshold]
        print(f"[2/2] 最新打分日 {d.date()}，红线{args.threshold} 过滤后 {len(cand)} 只")
        if len(cand):
            top = cand.nlargest(args.top, "total_new")
            print("      Top%d 候选:" % args.top)
            for code, s in top.iterrows():
                print(f"        {code}  total_new={s['total_new']:.1f}  prob={s['prob']:.3f}")
            # 追加 live 日志
            log_csv = os.path.join(REAL, "paper_forward_live.csv")
            rows = [{"date": d.date(), "code": code, "total_new": s["total_new"], "prob": s["prob"]}
                    for code, s in top.iterrows()]
            pd.DataFrame(rows).to_csv(log_csv, mode="a", header=not os.path.exists(log_csv),
                                      index=False, encoding="utf-8-sig")
            print(f"      已追加 {log_csv}")
        else:
            print("      无满足红线的候选（空仓）")


if __name__ == "__main__":
    main()
