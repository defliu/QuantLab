# coding: utf-8
"""换仓执行时点与回测口径偏差研究（2026-10-23）。

回测口径（scan_rotate_cost_real.simulate）：T 日收盘算信号 → **T+1 开盘价**买入/卖出。
实盘现状：T 日 20:05 生成候选 → T+1 约 09:45~09:53 执行（V1.3 09:45、G2 09:50、兜底 10:07）。

本脚本量化「T+1 各时点买入价 vs 开盘价」的偏离，回答：能否把执行时点提前以贴合回测。
样本 A：G2 实盘候选（paper_forward_live.csv rank<=3，2026-08-17~09-24，受分钟线覆盖所限）
样本 B：全市场随机抽样（3 个自然年 × 400 只），给系统性日内漂移基准
"""
import glob
import os
import random

import numpy as np
import pandas as pd

MIN_DIR = r"D:/astock/min_1min"
P16 = r"D:/QuantLab/projects/Project_16_LightGBM股票大师"
MAIN_DAILY = r"D:/astock/daily/stock_daily.parquet"

BUCKETS = ["09:30", "09:31", "09:35", "09:40", "09:45", "09:50",
           "10:00", "10:30", "11:30", "14:00", "14:50", "15:00"]


def _calendar():
    d = pd.read_parquet(MAIN_DAILY, columns=["trade_date"])
    if "trade_date" not in d.columns:
        d = d.reset_index()
    s = pd.to_datetime(d["trade_date"]).dt.strftime("%Y-%m-%d")
    return sorted(s.unique().tolist()), set(s.unique().tolist())


def _load_min(code, dates):
    p = os.path.join(MIN_DIR, code + ".parquet")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_time", "close"])
    except KeyError:
        # 部分文件 trade_time 是索引而非数据列 → 读全表后 reset（2026-10-23 实测 400 只抽样撞到）
        try:
            df = pd.read_parquet(p)
            if "trade_time" not in df.columns:
                df = df.reset_index()
        except Exception:
            return None
    except Exception:
        return None
    if "trade_time" not in df.columns:
        return None
    df["d"] = df["trade_time"].astype(str).str[:10]
    df["t"] = df["trade_time"].astype(str).str[11:16]
    df = df[df["d"].isin(dates)]
    return df


def _prices_on_date(df_date):
    """单日分钟表 -> {bucket: close}（取 <= bucket 的最后一根）。"""
    if df_date is None or len(df_date) == 0:
        return None
    df_date = df_date.sort_values("t")
    times = df_date["t"].tolist()
    closes = df_date["close"].tolist()
    out = {}
    for b in BUCKETS:
        idx = -1
        for i, t in enumerate(times):
            if t <= b:
                idx = i
            else:
                break
        if idx < 0:
            return None
        out[b] = float(closes[idx])
    return out


def sample_a(cal, cal_set):
    df = pd.read_csv(os.path.join(P16, "data/real/paper_forward_live.csv"), encoding="utf-8-sig")
    df = df[df["rank"] <= 3].copy()
    df["date"] = df["date"].astype(str)
    rows = []
    by_code = {}
    for _, r in df.iterrows():
        if r["date"] not in cal_set:
            continue
        i = cal.index(r["date"])
        if i + 1 >= len(cal):
            continue
        by_code.setdefault(r["code"], []).append((r["date"], cal[i + 1]))
    for code, pairs in by_code.items():
        dfm = _load_min(code, set(x[1] for x in pairs))
        if dfm is None:
            continue
        for sig_date, exec_date in pairs:
            sub = dfm[dfm["d"] == exec_date]
            pr = _prices_on_date(sub)
            if not pr or pr.get("09:30", 0) <= 0:
                continue
            o = pr["09:30"]
            row = {"code": code, "signal_date": sig_date, "exec_date": exec_date}
            for b in BUCKETS:
                row[b] = pr[b] / o - 1.0
            rows.append(row)
    return pd.DataFrame(rows)


def sample_b(cal, cal_set, years=(2024, 2025, 2026), n_codes=400, seed=42):
    codes = [os.path.basename(p)[:-8] for p in glob.glob(os.path.join(MIN_DIR, "*.parquet"))]
    codes = [c for c in codes if c.endswith(".SZ") or c.endswith(".SH")]
    random.seed(seed)
    pick = random.sample(codes, min(n_codes, len(codes)))
    dates = [d for d in cal if int(d[:4]) in years]
    dset = set(dates)
    rows = []
    for code in pick:
        dfm = _load_min(code, dset)
        if dfm is None or len(dfm) == 0:
            continue
        for d, sub in dfm.groupby("d"):
            pr = _prices_on_date(sub)
            if not pr or pr.get("09:30", 0) <= 0:
                continue
            o = pr["09:30"]
            row = {"code": code, "exec_date": d}
            for b in BUCKETS:
                row[b] = pr[b] / o - 1.0
            rows.append(row)
    return pd.DataFrame(rows)


def summarize(df, label):
    print("\n" + "=" * 78)
    print("%s  n=%d" % (label, len(df)))
    print("=" * 78)
    print("%-7s %10s %10s %10s %10s %8s" % ("time", "mean(bp)", "median(bp)", "std(bp)", "p25(bp)", "p75(bp)"))
    for b in BUCKETS:
        s = df[b].dropna() * 1e4
        print("%-7s %10.2f %10.2f %10.2f %10.2f %8.2f" % (
            b, s.mean(), s.median(), s.std(), s.quantile(.25), s.quantile(.75)))
    print("\n— 相对 09:30 的均值偏移（bp，正=更贵=买入成本更高）—")
    for b in BUCKETS:
        if b == "09:30":
            continue
        s = df[b].dropna() * 1e4
        t = s.mean() / (s.std() / np.sqrt(len(s))) if len(s) > 1 and s.std() > 0 else 0
        print("  %-6s %+7.2f bp   t=%+.2f   %s" % (
            b, s.mean(), t, "显著" if abs(t) > 2 else ""))


def main():
    cal, cal_set = _calendar()
    print("交易日历 %d 天，%s ~ %s" % (len(cal), cal[0], cal[-1]))
    a = sample_a(cal, cal_set)
    if len(a):
        summarize(a, "样本A · G2 实盘候选 rank<=3（信号日 + 次日执行）")
        a.to_csv(r"D:/QuantLab/projects/Project_16_LightGBM股票大师/results/timing_sample_a_candidates.csv",
                 index=False, encoding="utf-8-sig")
    b = sample_b(cal, cal_set)
    if len(b):
        summarize(b, "样本B · 全市场随机 400 只（2024~2026 全部交易日）")
        b.to_csv(r"D:/QuantLab/projects/Project_16_LightGBM股票大师/results/timing_sample_b_universe.csv",
                 index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
