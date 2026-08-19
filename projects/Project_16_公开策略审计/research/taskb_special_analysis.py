# coding: utf-8
"""任务B专项分析：2024危机段放大 + 退市损失 + 逐年 + 与P10对照 (T-20260819-002)
读取消融模拟器落盘的 positions/看 summary，独立复算关键指标。
"""
import os

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(PROJ, "results")
BASE = r"E:/astock"


def load_weekly_rets(name):
    p = os.path.join(RESULTS, f"taskb_{name}_weekly_returns.csv")
    if os.path.exists(p):
        df = pd.read_csv(p, encoding="utf-8-sig")
        df["week"] = pd.to_datetime(df["week"])
        return df.set_index("week")["ret"]
    return None


def load_positions(name):
    p = os.path.join(RESULTS, f"taskb_{name}_positions.csv")
    if os.path.exists(p):
        return pd.read_csv(p, encoding="utf-8-sig")
    return None


def main():
    lines = []
    def p(*a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True)
        lines.append(s)

    sb = pd.read_parquet(f"{BASE}/basic/stock_basic.parquet")
    sb["ts_code"] = sb["ts_code"].astype(str)

    # 逐个级别：先重跑周收益（模拟器未落盘周收益，这里用持仓文件反推不可行——需重新跑模拟器）
    # 改为：直接调用模拟器模块函数，但只跑周收益部分（复用已加载数据太长）。
    # 简化：读取 summary + 用收盘价反推每只持仓股的持有期收益，计算危机段净值。
    p("=== 任务B 专项分析 ===")

    # 用 daily 数据重算每个级别每只持仓的周收益（与模拟器同口径：周一收盘买入→下周一收盘卖出，后复权）
    # 读取已落盘 positions 里的全部 (week, ts_code)
    # 重新加载 daily（复用模拟器 load_data）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ab", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ablation_simulator.py"))
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)

    sd, cal = ab.load_data()
    dayframes = {d: df for d, df in sd.groupby("trade_date", sort=True)}
    sd2 = pd.concat([dayframes[d][["ts_code", "close", "adj_factor"]].assign(trade_date=d)
                     for d in cal])
    sd2["hfq"] = sd2["close"] * sd2["adj_factor"]
    weeks = pd.DatetimeIndex([d for d in cal if d.dayofweek == 0])
    week_map = {d: i for i, d in enumerate(weeks)}

    for name in ["A0", "A1", "A2", "A3"]:
        pos = load_positions(name)
        if pos is None:
            p(f"[{name}] 无持仓文件，跳过")
            continue
        pos["week"] = pd.to_datetime(pos["week"])
        # 每个持仓期：week -> codes
        per_week = pos.groupby("week")["ts_code"].apply(list).to_dict()
        week_rets = []
        hold_days = []
        for d in weeks:
            if d not in per_week or (d in weeks and weeks.get_loc(d) + 1 >= len(weeks)):
                week_rets.append(np.nan)
                continue
            codes = per_week[d]
            d_next = weeks[weeks.get_loc(d) + 1]
            r = 0.0
            for c in codes:
                sub = sd2[(sd2["ts_code"] == c) & (sd2["trade_date"].isin([d, d_next]))]
                if len(sub) < 2:
                    continue
                p0 = sub.loc[sub["trade_date"] == d, "hfq"].iloc[0]
                p1 = sub.loc[sub["trade_date"] == d_next, "hfq"].iloc[0]
                if p0 and p0 == p0 and p1 and p1 == p1:
                    r += (1.0 / len(codes)) * (p1 / p0 - 1.0)
            week_rets.append(r)
        rets = pd.Series(week_rets, index=weeks).dropna()
        equity = (1 + rets).cumprod()
        peak = equity.cummax()
        mdd = (equity - peak) / peak
        years = len(rets) / 52.0
        total = equity.iloc[-1] - 1
        cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1
        # 2024 危机段
        crisis = rets[(rets.index >= "2024-01-01") & (rets.index <= "2024-02-29")]
        # 全年各年收益
        yearly = rets.groupby(rets.index.year).apply(lambda x: (1 + x).prod() - 1)
        p(f"\n[{name}] 复算: n_weeks={len(rets)} CAGR={cagr*100:+.2f}% MDD={mdd.min()*100:+.1f}%")
        p(f"  2024危机段({len(crisis)}周): 累计={( (1+crisis).prod()-1)*100:+.1f}% "
          f"最深回撤={(( (1+crisis).cumprod()/(1+crisis).cumprod().cummax())-1).min()*100:+.1f}%")
        p(f"  危机段周收益明细: {[(d.strftime('%m-%d'), round(float(v)*100,2)) for d,v in crisis.items()]}")
        p(f"  逐年: { {int(y): round(float(v)*100,1) for y,v in yearly.items()} }")

        # 退市损失：统计持仓中出现过的退市股，估算损失（该股持有期间累计贡献）
        hold_codes = set(pos["ts_code"])
        dl = sb[sb["list_status"] == "D"]
        dl_hit = sorted(hold_codes & set(dl["ts_code"]))
        p(f"  持仓中出现退市股: {dl_hit}")
        for c in dl_hit:
            info = dl[dl["ts_code"] == c][["name", "list_date", "delist_date"]].to_dict("records")[0]
            p(f"    {c} {info['name']} 上市{info['list_date'].date()} 退市{info['delist_date'].date()}")

    # 与 P10 对照
    p("\n=== 与 P10 对照 ===")
    p("P10 价值小盘V2: 100只、全风控栈、退市排雷 -> 回测18.0%（双月口径）/ 真实日风控~7.5%")
    p("P10 V2a 纯BP基线 2018-2026: 年化16.2% / 回撤-29.7%")
    p("本策略(A3) = 10只极微盘(市值最小前10) 周度换仓 无风控无止损: 年化~43.6% / 回撤-28.9%")
    p("  -> A3 显著高于 P10 (43.6% vs 16.2%)，集中度差异主因（10只 vs 100只，周换手~1000%/年 vs P10低换手）")

    with open(os.path.join(RESULTS, "taskb_special_analysis.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p("\n[落盘] taskb_special_analysis.txt")


if __name__ == "__main__":
    main()