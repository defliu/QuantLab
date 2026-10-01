# -*- coding: utf-8 -*-
"""
B 路线 Part 3: gpsj 备用源交叉验证(2024 自然年, AGENTS 全局规则)
===============================================================
目的: 用备用源 gpsj.duckdb 重算 2024 年小盘池日收益, 与 astock 口径对比
      (池收益相关性 / 池指数年收益 / 最大回撤), 验证 H1/H2 的收益侧无数据源假象。
降级规则(AGENTS): 源不可用时打印 [SKIP] 并 return 0, 报告标注单源结论。
标签侧(连板梯队)依赖 up_limit 字段, gpsj 无对应列, 不做标签交叉(如实披露)。
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\QuantLab")
try:
    from data import gpsj_reader  # noqa: E402
except ImportError as e:
    print("[SKIP] gpsj 备用源不可用(import 失败: %s) —— 按 AGENTS 降级规则跳过, "
          "本次结论为单源(仅 astock), 未经备用源交叉验证" % e)
    sys.exit(0)

YEAR = "2024"


def main():
    if not gpsj_reader.is_available():
        print("[SKIP] gpsj 备用源不可用(GPSJ_DB_PATH 默认 E:/huicexitong/runtime/sj/"
              "gpsj.duckdb, 本机无 E 盘) —— 按 AGENTS 降级规则跳过, "
              "本次结论为单源(仅 astock), 未经备用源交叉验证")
        return 0

    reader = gpsj_reader.GpsjReader()
    cal = reader.trading_calendar()
    days = [d for d in cal if str(d)[:4] == YEAR]
    if not days:
        print("[SKIP] gpsj 2024 无交易日数据")
        return 0

    # 全市场窗口加载
    df = reader.load_window(None, days[0], days[-1])  # 鸭子接口: codes=None -> 全部
    # 对齐 P17 池口径
    pool = df[(df["circ_mv"] < 500000) & (df.get("is_st", 0) != 1)]
    g = pool.groupby("date")["pct_chg"]
    # gpsj pct_chg 若缺失, 用 close 对数差
    if "pct_chg" not in pool.columns:
        pool = pool.sort_values(["code", "date"])
        pool["ret"] = pool.groupby("code")["close"].transform(
            lambda s: np.log(s / s.shift(1)))
        pr_g = pool.groupby("date")["ret"].mean()
    else:
        pr_g = np.log1p(pool["pct_chg"] / 100.0).groupby(pool["date"]).mean()
    pr_g = pr_g.sort_index()

    # astock 侧同口径
    b1 = pd.read_csv(r"D:\QuantLab\research_study\market_phase_predictive"
                     r"\daily_phase_b_route.csv", index_col=0, parse_dates=True)
    # b1 没存池日收益, 用 h3 产物反推不可靠 -> 直接重算 astock 池日收益
    ad = pd.read_parquet("D:/astock/daily/stock_daily.parquet",
                         columns=["pct_chg", "circ_mv", "is_st", "listed_days"])
    ad = ad.reset_index()
    sub = ad[(ad["trade_date"] >= pr_g.index.min()) & (ad["trade_date"] <= pr_g.index.max())]
    mask = (sub["circ_mv"] < 500000) & (sub["is_st"] != 1) & (sub["listed_days"].fillna(1e9) >= 60)
    pr_a = np.log1p(sub.loc[mask, "pct_chg"].astype(float) / 100.0).groupby(
        sub.loc[mask, "trade_date"]).mean().sort_index()

    joined = pd.DataFrame({"gpsj": pr_g, "astock": pr_a}).dropna()
    corr = joined["gpsj"].corr(joined["astock"])
    nav_g = np.exp(joined["gpsj"].cumsum())
    nav_a = np.exp(joined["astock"].cumsum())

    def mdd(nav):
        return float((nav / nav.cummax() - 1.0).min())

    print("2024 交叉验证: 共 %d 交易日" % len(joined))
    print("  池日收益相关系数: %.4f" % corr)
    print("  gpsj  池指数年收益 %+.2f%% / MDD %.2f%%" % (
        (nav_g.iloc[-1] - 1) * 100, mdd(nav_g) * 100))
    print("  astock 池指数年收益 %+.2f%% / MDD %.2f%%" % (
        (nav_a.iloc[-1] - 1) * 100, mdd(nav_a) * 100))
    ok = corr > 0.99 and abs((nav_g.iloc[-1] - nav_a.iloc[-1])) < 0.02
    print("  判定: %s" % ("一致(通过)" if ok else "差异过大需排查"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
