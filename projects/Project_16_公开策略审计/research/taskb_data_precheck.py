# coding: utf-8
"""任务B数据预检：字段盘点 + 复权口径验证 + TTM股息率对照 + 退市识别 (T-20260819-002)

任务书 §2.3/2.4 要求的数据事实核查。
只读，不改任何既有文件。
"""
import os

import numpy as np
import pandas as pd

BASE = r"E:/astock"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
os.makedirs(OUT, exist_ok=True)

lines = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines.append(s)


def main():
    p("==== 任务B 数据预检 ====")
    p("时间:", pd.Timestamp.now())

    # ---- 1. 字段盘点 ----
    sb = pd.read_parquet(f"{BASE}/basic/stock_basic.parquet")
    p("\n[1] stock_basic rows=%d" % len(sb))
    p("  list_status 分布:", sb["list_status"].value_counts(dropna=False).to_dict())
    p("  list_date 非空:", sb["list_date"].notna().mean().round(4),
      " delist_date 非空:", sb["delist_date"].notna().mean().round(4))
    p("  sample:", sb[["ts_code", "name", "list_date", "delist_date"]].dropna(subset=["delist_date"]).head(5).to_dict("records"))

    dv = pd.read_parquet(f"{BASE}/finance/dividend.parquet")
    p("\n[2] dividend rows=%d" % len(dv))
    p("  div_proc 分布:", dv["div_proc"].value_counts(dropna=False).to_dict())
    p("  ex_date 非空:", dv["ex_date"].notna().mean().round(4))
    p("  cash_div_tax 非空:", dv["cash_div_tax"].notna().mean().round(4))

    inc = pd.read_parquet(f"{BASE}/finance/income.parquet")
    p("\n[3] income rows=%d" % len(inc))
    p("  report_type 分布:", inc["report_type"].value_counts().to_dict())
    p("  f_ann_date 非空:", inc["f_ann_date"].notna().mean().round(4),
      " ann_date 非空:", inc["ann_date"].notna().mean().round(4))
    # 单季度 or 累计？end_type
    p("  end_type 分布:", inc["end_type"].value_counts(dropna=False).to_dict())
    # 检查累计 vs 单季：600816 2019Q1 vs Q2 的 n_income_attr_p
    sub = inc[inc["ts_code"] == "600816.SH"].sort_values("end_date")
    p("  600816.SH 报告期/净利润:", sub[["end_date", "end_type", "n_income_attr_p", "f_ann_date"]].head(8).to_dict("records"))

    sd = pd.read_parquet(f"{BASE}/daily/stock_daily.parquet",
                         columns=["open", "high", "low", "close", "pre_close",
                                  "up_limit", "down_limit", "adj_factor",
                                  "turnover_rate", "suspend_type", "is_st",
                                  "pe_ttm", "total_mv", "dv_ttm", "listed_days"])
    p("\n[4] stock_daily rows=%d 区间=%s ~ %s" % (
        len(sd), sd.index.get_level_values(0).min().date(), sd.index.get_level_values(0).max().date()))
    n_codes = sd.index.get_level_values(1).nunique()
    p("  历史出现过的 ts_code 数=%d" % n_codes)
    p("  is_st 取值:", sd["is_st"].dropna().unique().tolist())
    p("  suspend_type 取值:", sd["suspend_type"].dropna().unique()[:10].tolist())
    p("  listed_days 非空:", sd["listed_days"].notna().mean().round(4))

    # ---- 2. 复权口径验证（除权股）----
    p("\n[2] 复权口径验证：找一只除权日")
    codes = sd.index.get_level_values(1).unique()
    rng = np.random.RandomState(0)
    pick = rng.choice(codes, 200, replace=False)
    cand = []
    for c in pick:
        sub = sd.xs(c, level=1).sort_index()
        # 除权日 = adj_factor 明显跳变的日子
        diff = sub["adj_factor"].diff()
        jump = diff[diff > diff.median() * 5]
        if len(jump) >= 1 and len(sub) > 30:
            cand.append((c, sub.index[0].date(), sub["adj_factor"].iloc[0], sub["adj_factor"].iloc[-1]))
    p("  抽样200只中带除权跳变:", cand[:5])
    if cand:
        c, d0, f0, f1 = cand[0]
        sub = sd.xs(c, level=1).sort_index()
        p("  验证 %s (首日 %s, adj_factor %.3f -> %.3f):" % (c, d0, f0, f1))
        # 找最大跳变日
        diff = sub["adj_factor"].diff()
        jd = diff.idxmax()
        p("    最大跳变日=%s 前一日adj=%.4f 当日adj=%.4f" % (
            jd.date(), sub["adj_factor"].loc[jd - pd.Timedelta(days=1)] if jd - pd.Timedelta(days=1) in sub.index else float('nan'),
            sub["adj_factor"].loc[jd]))
        around = sub.loc[jd - pd.Timedelta(days=3): jd + pd.Timedelta(days=3),
                         ["close", "adj_factor"]]
        p("    除权日前3~后3日 close/adj_factor:")
        p(around.to_string())

    # ---- 3. TTM 股息率：自建 vs 快照 dv_ttm ----
    p("\n[3] TTM 股息率对照（抽样某日，自建 vs dv_ttm 快照）")
    div = dv[dv["div_proc"].astype(str).str.strip() == "实施"].copy()
    div = div[div["ex_date"].notna()].copy()
    div["ex_date"] = pd.to_datetime(div["ex_date"])
    div = div[div["cash_div_tax"].notna() & (div["cash_div_tax"] > 0)].copy()
    div_wide = div.pivot_table(index="ex_date", columns="ts_code", values="cash_div_tax", aggfunc="sum").sort_index().fillna(0.0)
    cum = div_wide.cumsum()
    check_dates = ["2019-06-28", "2022-06-30", "2025-06-30"]
    sample_codes = ["000001.SZ", "600519.SH", "000002.SZ", "600036.SH"]
    for d in check_dates:
        ts = pd.Timestamp(d)
        # 自建 TTM
        cum_now = cum.reindex(pd.DatetimeIndex([ts])).ffill().iloc[0]
        cum_365 = cum.reindex(pd.DatetimeIndex([ts - pd.Timedelta(days=365)])).ffill().iloc[0]
        ttm = (cum_now - cum_365)
        rows = []
        for c in sample_codes:
            close = sd.xs(c, level=1).sort_index()
            c0 = close["close"].asof(ts) if ts >= close.index[0] else np.nan
            dv_snap = close["dv_ttm"].asof(ts) if ts >= close.index[0] else np.nan
            own = float(ttm.get(c, np.nan)) / c0 if c0 and c0 == c0 else np.nan
            rows.append({"code": c, "close": round(c0, 3) if c0 == c0 else None,
                         "self_ttm": round(own, 6) if own == own else None,
                         "dv_ttm_snap": round(dv_snap, 6) if dv_snap == dv_snap else None})
        p("  %s 对照:" % d)
        for r in rows:
            p("   ", r)

    # ---- 4. 净利润同比构造测试 ----
    p("\n[4] 净利润同比（累计口径）构造测试（600816.SH 用报告期对齐，非PIT）")
    incs = inc[inc["report_type"] == "1"].copy()  # 合并报表
    incs["end_date"] = incs["end_date"].astype(str)
    incs["f_ann_date"] = incs["f_ann_date"].astype(str)
    sub = incs[incs["ts_code"] == "600816.SH"].sort_values("end_date")
    sub = sub[sub["n_income_attr_p"].notna()]
    p("  end_date 序列:", sub["end_date"].tail(10).tolist())
    # 同比: 2020Q1 vs 2019Q1
    q = sub[sub["end_date"] == "20200331"]
    qy = sub[sub["end_date"] == "20190331"]
    if len(q) and len(qy):
        yoy = q["n_income_attr_p"].iloc[0] / qy["n_income_attr_p"].iloc[0] - 1
        p("  20200331 n_income=%.2f vs 20190331 %.2f -> yoy=%.4f" % (
            q["n_income_attr_p"].iloc[0], qy["n_income_attr_p"].iloc[0], yoy))

    with open(os.path.join(OUT, "taskB_data_precheck.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p("\n预检完成 -> results/taskB_data_precheck.txt")


if __name__ == "__main__":
    main()