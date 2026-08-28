# coding: utf-8
"""构建 v3_sc 面板：v3_enh 37列 + main_net（真实主力净额） + industry_pct（行业当日涨幅）。

数据源：
  - main_net：v3.2 面板 main_net_v32（= data/real/fund_flow_real.parquet 新浪资金流，已验证同一来源、同网格、同覆盖）
  - industry_pct：stock_basic.industry + 主库 stock_daily.pct_chg 按 (trade_date, industry) 等权均值
输出：data/feature_panel_v3_sc.parquet（保留原 37 列顺序，末尾追加 main_net、industry_pct）
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

PANEL_ENH = os.path.join(DATA, "feature_panel_v3_enh.parquet")
PANEL_V32 = os.path.join(DATA, "feature_panel_v3.2.parquet")
FLOW_REAL = os.path.join(DATA, "real", "fund_flow_real.parquet")
STOCK_BASIC = r"E:\astock\basic\stock_basic.parquet"
MAIN_DAILY = r"E:\astock\daily\stock_daily.parquet"
OUT = os.path.join(DATA, "feature_panel_v3_sc.parquet")


def main():
    print("[1/5] 读取 v3_enh 面板 ...")
    p = pd.read_parquet(PANEL_ENH)
    orig_cols = list(p.columns)
    p["trade_date"] = pd.to_datetime(p["trade_date"])
    p["ts_code"] = p["ts_code"].astype(str)
    print("    shape:", p.shape, "| 原列数:", len(orig_cols), "| 日期:", p["trade_date"].min().date(), "~", p["trade_date"].max().date())

    print("[2/5] 对齐 main_net（v3.2 main_net_v32，与 fund_flow_real 同源已验证）...")
    p32 = pd.read_parquet(PANEL_V32, columns=["trade_date", "ts_code", "main_net_v32"])
    p32["trade_date"] = pd.to_datetime(p32["trade_date"])
    p32["ts_code"] = p32["ts_code"].astype(str)
    # 网格一致性校验
    g1 = set(zip(p["trade_date"], p["ts_code"]))
    g2 = set(zip(p32["trade_date"], p32["ts_code"]))
    assert g1 == g2, "v3_enh 与 v3.2 面板 (trade_date,ts_code) 网格不一致！"
    p = p.merge(p32, on=["trade_date", "ts_code"], how="left", validate="one_to_one")
    p.rename(columns={"main_net_v32": "main_net"}, inplace=True)

    # 交叉验证 fund_flow_real（抽样核对）
    fr = pd.read_parquet(FLOW_REAL, columns=["date", "ts_code", "main_net"])
    fr["date"] = pd.to_datetime(fr["date"])
    chk = p[["trade_date", "ts_code", "main_net"]].merge(
        fr.rename(columns={"date": "trade_date", "main_net": "main_net_raw"}),
        on=["trade_date", "ts_code"], how="inner")
    diff = (chk["main_net"] - chk["main_net_raw"]).abs()
    print(f"    main_net 与 fund_flow_real 交叉验证: {len(chk)} 条 | abs diff max={diff.max():.2f} mean={diff.mean():.4f}")

    print("[3/5] 行业归属（stock_basic）...")
    sb = pd.read_parquet(STOCK_BASIC, columns=["ts_code", "industry"])
    sb["ts_code"] = sb["ts_code"].astype(str)
    ind_map = sb.dropna(subset=["industry"]).set_index("ts_code")["industry"].to_dict()
    p["industry"] = p["ts_code"].map(ind_map)
    n_ind = p["industry"].notna().sum()
    print(f"    panel 股票带行业归属: {n_ind} / {len(p)} = {n_ind / len(p):.4f}")

    print("[4/5] 行业当日涨幅（主库 pct_chg 按行业等权均值）...")
    daily = pd.read_parquet(MAIN_DAILY, columns=["pct_chg"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["industry"] = daily["ts_code"].map(ind_map)
    sub = daily.dropna(subset=["industry", "pct_chg"]).copy()
    ind_pct = sub.groupby(["trade_date", "industry"])["pct_chg"].mean().rename("industry_pct")
    ind_pct = ind_pct.reset_index()
    print(f"    (date, industry) 组合数: {len(ind_pct)} | 日期范围: {ind_pct['trade_date'].min().date()} ~ {ind_pct['trade_date'].max().date()}")

    print("[5/5] 合并 industry_pct 并输出 ...")
    p = p.merge(ind_pct, on=["trade_date", "industry"], how="left")
    # 原 37 列顺序保持在前，main_net / industry_pct 追加到末尾
    new_cols = orig_cols + ["main_net", "industry_pct"]
    p = p[new_cols]
    p.to_parquet(OUT, index=False)

    # 覆盖率统计
    print("\n=== 覆盖率统计 ===")
    for c in ["main_net", "industry_pct"]:
        print(f"  {c}: non-null {p[c].notna().sum()} / {len(p)} = {p[c].notna().mean():.4f}")
    bt = (p["trade_date"] >= "2024-07-01")
    for c in ["main_net", "industry_pct"]:
        print(f"  回测窗口(2024-07~2026-08) {c}: non-null {p.loc[bt, c].notna().sum()} / {bt.sum()} = {p.loc[bt, c].notna().mean():.4f}")
    print("\n输出:", OUT, "| shape:", p.shape)


if __name__ == "__main__":
    main()
