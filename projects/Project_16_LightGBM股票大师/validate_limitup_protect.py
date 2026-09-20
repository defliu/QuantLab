# -*- coding: utf-8 -*-
"""涨停动量保护验证脚本（T-20260916-002 研究，2026-09-16）。

目标：验证「持有期内出现过涨停的持仓，到期卖出是否系统性卖飞」。
数据：paper_forward_trades.csv（V1.3 回测台账 810 笔，2024-07~2026-07，open→open，hold=5）。
方法：
  1) 对每笔交易取买入日~卖出日前一日的日线（卖出决策时可用的信息，无 look-ahead），
     标记「持有期内收盘涨停」（close/prev_close-1 >= 9.8%）或「盘中触板」（high/prev_close-1 >= 9.9%）。
  2) 计算卖出后 T+1/T+3/T+5 收益（卖出价 open 为基准 → 后续收盘价）。
  3) 分组对比：涨停组 vs 非涨停组的卖出后收益（均值/中位数/胜率/N）。
结论判据：若涨停组卖出后收益显著为正且明显高于非涨停组 → 到期卖涨停票系统性卖飞 →
「涨停动量保护」值得落地；若接近 0 或为负 → 601579 属单例运气，不动规则。
"""
import datetime
import os

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
TRADES = os.path.join(PROJ, "data", "real", "paper_forward_trades.csv")
MAIN = r"D:/astock/daily/stock_daily.parquet"
LIMIT_CLOSE = 0.098  # 收盘涨停阈值（近似，容忍四舍五入/ST差异）
LIMIT_HIGH = 0.099   # 盘中触板阈值

OUT = os.path.join(PROJ, "data", "schedules", "limitup_protect_validation_20260916.md")


def load_daily():
    """主库日线：close/high/prev_close（prev 由 shift 计算）。返回 {code: DataFrame(date, close, high, chg)}"""
    d = pd.read_parquet(MAIN, columns=["close", "high"])
    d = d.reset_index()
    d["trade_date"] = d["trade_date"].astype(str).str[:10]
    d["ts_code"] = d["ts_code"].astype(str)
    d = d.sort_values(["ts_code", "trade_date"])
    d["prev_close"] = d.groupby("ts_code")["close"].shift(1)
    d["chg"] = d["close"] / d["prev_close"] - 1.0
    d["chg_high"] = d["high"] / d["prev_close"] - 1.0
    out = {}
    for code, g in d.groupby("ts_code"):
        out[code] = g.set_index("trade_date")
    return out


def main():
    trades = pd.read_csv(TRADES)
    trades["buy_date"] = trades["buy_date"].astype(str)
    trades["sell_date"] = trades["sell_date"].astype(str)
    daily = load_daily()
    print("日线加载完成，票数:", len(daily))

    rows = []
    for _, t in trades.iterrows():
        code, bd, sd = t["code"], t["buy_date"], t["sell_date"]
        g = daily.get(code)
        if g is None:
            continue
        # 持有期窗口 = 买入日 ~ 卖出日前一日（卖出决策时可见，无 look-ahead）
        window = g.loc[(g.index >= bd) & (g.index < sd)]
        if window.empty:
            continue
        limit_close = bool((window["chg"].dropna() >= LIMIT_CLOSE).any())
        limit_high = bool((window["chg_high"].dropna() >= LIMIT_HIGH).any())
        # 卖出日（open 为基准 exit）
        sell_day = g.loc[g.index == sd]
        if sell_day.empty:
            continue
        exit_price = t["exit"]
        # T+1/T+3/T+5 = 卖出日之后的收盘
        after = g.loc[g.index > sd]
        fwd = {}
        for n in (1, 3, 5):
            if len(after) >= n:
                px = float(after.iloc[n - 1]["close"])
                fwd["fwd%d" % n] = px / exit_price - 1.0
            else:
                fwd["fwd%d" % n] = np.nan
        rows.append({
            "buy_date": bd, "sell_date": sd, "code": code,
            "ret": t["ret"], "limit_close": limit_close, "limit_high": limit_high,
            **fwd,
        })

    df = pd.DataFrame(rows)
    print("有效交易:", len(df))

    def stats(sub):
        if len(sub) == 0:
            return None
        s = {}
        s["N"] = len(sub)
        for c in ("ret", "fwd1", "fwd3", "fwd5"):
            v = sub[c].dropna()
            s[c + "_mean"] = v.mean() if len(v) else np.nan
            s[c + "_med"] = v.median() if len(v) else np.nan
            s[c + "_win"] = (v > 0).mean() if len(v) else np.nan
        return s

    groups = {
        "全部": df,
        "持有期收盘涨停": df[df["limit_close"]],
        "持有期未涨停": df[~df["limit_close"]],
        "持有期盘中触板": df[df["limit_high"]],
        "持有期未触板": df[~df["limit_high"]],
    }
    print("\n===== 分组统计（卖出后收益，基准=卖出价 open）=====")
    for name, sub in groups.items():
        s = stats(sub)
        if s:
            print("\n[%s] N=%d" % (name, s["N"]))
            print("  卖出前已实现 ret: mean=%+.2f%% med=%+.2f%%" % (s["ret_mean"] * 100, s["ret_med"] * 100))
            for c in ("fwd1", "fwd3", "fwd5"):
                print("  %s: mean=%+.2f%% med=%+.2f%% 胜率=%.1f%%" % (
                    c, s[c + "_mean"] * 100, s[c + "_med"] * 100, s[c + "_win"] * 100))

    # 组间差异（收盘涨停 vs 未涨停的 fwd 均值差）
    zt = df[df["limit_close"]]
    nz = df[~df["limit_close"]]
    print("\n===== 组间差异（涨停组 - 未涨停组，卖出后收益均值差）=====")
    for c in ("fwd1", "fwd3", "fwd5"):
        a, b = zt[c].dropna(), nz[c].dropna()
        if len(a) and len(b):
            diff = a.mean() - b.mean()
            print("  %s: 涨停组 %+.2f%% vs 未涨停 %+.2f%% → 差 %+.2f%%（涨停组N=%d 未涨停N=%d）" % (
                c, a.mean() * 100, b.mean() * 100, diff * 100, len(a), len(b)))

    # 写报告
    lines = ["# 涨停动量保护验证报告（T-20260916-002，2026-09-16）", ""]
    lines.append("数据：paper_forward_trades.csv（V1.3 回测台账 %d 笔，2024-07~2026-07，open→open，hold=5）" % len(trades))
    lines.append("涨停判定：持有期[买入日~卖出日前一日]收盘涨幅≥9.8%（收盘涨停）或盘中≥9.9%（触板），无 look-ahead。")
    lines.append("卖出后收益：基准=卖出价(open)，取其后 T+1/T+3/T+5 收盘。")
    lines.append("")
    lines.append("| 分组 | N | 已实现ret均值 | fwd1均值 | fwd3均值 | fwd5均值 | fwd3胜率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, sub in groups.items():
        s = stats(sub)
        if s:
            lines.append("| %s | %d | %+.2f%% | %+.2f%% | %+.2f%% | %+.2f%% | %.0f%% |" % (
                name, s["N"], s["ret_mean"] * 100, s["fwd1_mean"] * 100,
                s["fwd3_mean"] * 100, s["fwd5_mean"] * 100, s["fwd3_win"] * 100))
    lines.append("")
    lines.append("| 组间差（涨停-未涨停） | fwd1 | fwd3 | fwd5 |")
    lines.append("|---|---|---|---|")
    for c in ("fwd1", "fwd3", "fwd5"):
        a, b = zt[c].dropna(), nz[c].dropna()
        if len(a) and len(b):
            lines.append("| %s | %+.2f%% | %+.2f%% | %+.2f%% |" % (c, a.mean() * 100 - b.mean() * 100, a.mean() * 100, b.mean() * 100))
    lines.append("")
    lines.append("## 结论判据")
    lines.append("- 涨停组卖出后 fwd 显著为正且 > 未涨停组 → 到期卖涨停票系统性卖飞 → 涨停动量保护值得落地")
    lines.append("- 涨停组 fwd 接近 0 或为负 → 601579 属单例，不动规则")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n报告已写:", OUT)


if __name__ == "__main__":
    main()
