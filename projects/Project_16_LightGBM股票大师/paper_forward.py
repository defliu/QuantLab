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
ASCII = r"D:\QuantLab\models"  # 2026-09-01 修正：g2 模型已迁至 D:/QuantLab/models（原 trae-cn/work ASCII 路径过期）

# 环境：真实评分卡面板 + g2 模型（导入 scan_rotate_cost_real 前设置）
os.environ["BT_PANEL"] = os.path.join(DATA, "feature_panel_v3_enh2_n3_bt.parquet")
os.environ["BT_MODEL"] = os.path.join(ASCII, "lgb_model_v3_g2_strong_real_20260825_1964t.txt")
os.environ["BT_META"] = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
os.environ["BT_OUT"] = os.path.join(REAL, "paper_forward_tmp.md")
os.environ["BT_THRESHOLD"] = "60.0"

import scan_rotate_cost_real as BT  # noqa: E402
import scorecard_real  # noqa: E402
import data_config as DC  # noqa: E402


def _append_live_dedup(log_csv, rows):
    """幂等追加 paper_forward_live.csv：读现有 → concat → 按 (date, code) 去重 → 原子写回。

    修复审计 T-20260831 指出的重复追加（8/28 重复 4 次）：定时任务重入/重跑不再产生重复行。
    """
    new = pd.DataFrame(rows)
    if os.path.exists(log_csv):
        old = pd.read_csv(log_csv, encoding="utf-8-sig")
        df = pd.concat([old, new], ignore_index=True)
    else:
        df = new
    df = df.drop_duplicates(subset=["date", "code"], keep="last")
    tmp = log_csv + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, log_csv)


def load_open_panel():
    """主库 + data_live 增量合并（增量优先）的 open 面板。

    背景（2026-09-04 定位）：backfill 原只依赖 DC.read_main_daily（MAIN_DAILY + Updatedata
    周更目录，需人工维护），而 g2 选股侧用的是 data_live/incremental_daily.parquet（每日自动
    更新）。两套数据源分裂 → 候选能每天产生、收益永远算不出（主库停更 8/28 时，63 笔候选
    回填 0 笔）。此处合并二者，增量优先，保证 backfill 与选股侧同口径。
    """
    daily = DC.read_main_daily(columns=["open"])
    main_last = str(sorted(set(daily.index.get_level_values(0)))[-1])[:10]
    inc_path = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
    inc_last = None
    if os.path.isfile(inc_path):
        try:
            inc = pd.read_parquet(inc_path)
            need = {"trade_date", "ts_code", "open"}
            if need.issubset(set(inc.columns)):
                inc = inc[["trade_date", "ts_code", "open"]].copy()
                inc["trade_date"] = pd.to_datetime(inc["trade_date"])
                inc = inc.dropna(subset=["open"])
                inc = inc[inc["open"] > 0]
                inc = inc.set_index(["trade_date", "ts_code"])
                inc.index.names = ["trade_date", "ts_code"]
                inc = inc[~inc.index.duplicated(keep="last")]
                inc_last = str(sorted(set(inc.index.get_level_values(0)))[-1])[:10]
                daily = pd.concat([daily, inc])
                daily = daily[~daily.index.duplicated(keep="last")]  # 增量覆盖主库
                daily = daily.sort_index()
        except Exception as e:  # 增量不可用则退回主库，不静默
            print("      [WARN] 增量库合并失败，退回主库: %s" % e)
    else:
        print("      [WARN] 未找到增量库 %s，仅用主库" % inc_path)
    trades = sorted(set(daily.index.get_level_values(0)))
    print(f"      数据源: 主库最新 {main_last} | 增量最新 {inc_last or 'NA'} | 合并后 {str(trades[-1])[:10]}")
    return daily, main_last, inc_last


def backfill_live(hold):
    """对 paper_forward_live.csv 中已过持有期的候选，回填 N 日 open→open 收益（对齐回测 N=10 alpha 来源）。

    原 --live 只记录候选（date/code/total_new/prob），不跟踪收益——审计 P1-1 指出这与回测
    "N=10 open→open"的 alpha 来源错配。本函数用全市场 open（主库+增量）对已满持有期的行回填
    entry（选股日次日 open）/ exit（买入后第 N 个交易日 open）/ ret。

    2026-09-04 加固：①数据源改 load_open_panel（主库+增量，增量优先）；②写回前按 (date,code)
    去重（此前出现重复行会重复计权）；③新算出的 ret 为空时保留旧值，防数据源抖动导致已回填
    收益回退；④返回统计供 main 做 fail-loud。
    """
    log_csv = os.path.join(REAL, "paper_forward_live.csv")
    if not os.path.exists(log_csv):
        print("      无 live 日志")
        return {"done": 0, "total": 0, "stale": False}
    df = pd.read_csv(log_csv, encoding="utf-8-sig")
    df = df.drop_duplicates(subset=["date", "code"], keep="last").reset_index(drop=True)
    # rank 补全：2026-09-02 起 live 由 deploy_predict_g2.py 写入 Top10，历史行（Top2）无 rank。
    # 实盘 g2_config.TOP_N=2，统计时按 rank<=2 取实盘口径子集。
    if "rank" not in df.columns:
        df["rank"] = np.nan
    if df["rank"].isna().any():
        # 逐日补 rank（该日全部缺 rank 才补，避免覆盖已有值）；不用 groupby.apply 以免 pandas 警告
        for d in df.loc[df["rank"].isna(), "date"].unique():
            sub = df[df["date"] == d].sort_values("total_new", ascending=False)
            df.loc[sub.index, "rank"] = range(1, len(sub) + 1)
        df = df.sort_values(["date", "rank"]).reset_index(drop=True)
    daily, main_last, inc_last = load_open_panel()
    trades = sorted(set(daily.index.get_level_values(0)))
    open_map = daily["open"].to_dict()
    for col in ("entry", "exit", "ret", "hold"):
        if col not in df.columns:
            df[col] = np.nan

    stale = False
    newly = 0
    for i, r in df.iterrows():
        old_ret = r.get("ret")
        df.at[i, "entry"] = np.nan
        df.at[i, "exit"] = np.nan
        df.at[i, "ret"] = np.nan
        df.at[i, "hold"] = np.nan
        d = pd.Timestamp(r["date"])
        idx = next((j for j, x in enumerate(trades) if x > d), None)
        if idx is None:
            continue
        exit_j = idx + hold
        if exit_j >= len(trades):
            continue
        entry = open_map.get((trades[idx], r["code"]))
        exit_p = open_map.get((trades[exit_j], r["code"]))
        if entry and exit_p and entry > 0 and exit_p > 0:
            df.at[i, "entry"] = entry
            df.at[i, "exit"] = exit_p
            df.at[i, "ret"] = exit_p / entry - 1
            df.at[i, "hold"] = hold
            newly += 1
        elif old_ret is not None and not (isinstance(old_ret, float) and np.isnan(old_ret)):
            # 数据源抖动：保留已回填的旧值，不回退
            df.at[i, "ret"] = old_ret
    tmp = log_csv + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, log_csv)
    done = df[~df["ret"].isna()]
    print(f"      backfill: 回填 {len(done)}/{len(df)} 笔（hold={hold}，本次新算 {newly}）")
    if len(done):
        for tag, sub in (("全部", done), ) + ((("TOP2子集", done[done["rank"] <= 2]),) if "rank" in done.columns else ()):
            if len(sub):
                print(f"      [{tag}] n={len(sub)} 均值 {sub['ret'].mean():+.3%} "
                      f"胜率 {(sub['ret'] > 0).mean():.1%} 中位 {sub['ret'].median():+.3%}")
    # 数据源停更检测：合并后最新交易日落后今天 >5 个自然日视为停更
    last_trade = trades[-1]
    gap = (pd.Timestamp.today().normalize() - pd.Timestamp(last_trade)).days
    if gap > 5:
        stale = True
        print(f"      [ALERT] 行情数据源停更：最新交易日 {str(last_trade)[:10]}，距今 {gap} 天 "
              f"（主库 {main_last} / 增量 {inc_last}）→ backfill 无法产出新样本")
    return {"done": len(done), "total": len(df), "stale": stale}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="测试期回放生成台账")
    ap.add_argument("--live", action="store_true", help="当日 top2 候选")
    ap.add_argument("--backfill", action="store_true", help="回填 live 日志未来 N 日 open→open 收益")
    ap.add_argument("--start", default="2024-07-01", help="回放起始日")
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--threshold", type=float, default=60.0)
    ap.add_argument("--hold", type=int, default=10, help="持有期（回放台账按此计算出场）")
    args = ap.parse_args()

    if args.backfill:
        stat = backfill_live(args.hold)
        # fail-loud：数据源停更时以非零码退出，避免"回填 0 笔仍报 OK"长期静默
        # （2026-09-04 实测：连续 4 天 backfill 0 笔，任务 LastTaskResult=0，无人察觉）
        if stat.get("stale"):
            sys.exit(2)
        if stat.get("total", 0) > 0 and stat.get("done", 0) == 0:
            print("      [ALERT] 候选已累积 %d 笔但回填 0 笔，请检查数据源覆盖" % stat["total"])
        return

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
            # 追加 live 日志（幂等去重）
            log_csv = os.path.join(REAL, "paper_forward_live.csv")
            rows = [{"date": d.date(), "code": code, "total_new": s["total_new"], "prob": s["prob"]}
                    for code, s in top.iterrows()]
            _append_live_dedup(log_csv, rows)
            print(f"      已追加 {log_csv}")
        else:
            print("      无满足红线的候选（空仓）")


if __name__ == "__main__":
    main()
