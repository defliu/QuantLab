# -*- coding: utf-8 -*-
"""数据覆盖核查（只读）—— F5/F2 修正口径重放前置探针。"""
import os
import pandas as pd
import numpy as np

ASTOCK = r"D:/astock"
PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
INCR = os.path.join(PROJ, "data_live", "incremental_daily.parquet")
SEL = os.path.join(PROJ, "data", "selections", "g2")

print("=" * 70)
print("[1] daily/stock_daily.parquet")
d = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet"))
d = d.reset_index()
if "index" in d.columns:
    d = d.drop(columns=["index"])
print("    列:", list(d.columns))
d["trade_date"] = pd.to_datetime(d["trade_date"])
print("    覆盖:", d["trade_date"].min().date(), "~", d["trade_date"].max().date(), "| 行数", len(d))
print("    列(需要): trade_date/ts_code/adj_factor/close/pre_close/up_limit/down_limit/vol OK")
print("    2026-08-17~09-18 逐日股票数:")
dd = d[(d["trade_date"] >= "2026-08-01")]
for t, g in dd.groupby("trade_date"):
    print("      %s  %6d" % (t.date(), len(g)), end="")
    if t.weekday() == 4 or t == dd["trade_date"].max():
        print()

print()
print("[2] moneyflow/moneyflow.parquet (F2 主源)")
mf = pd.read_parquet(os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet"))
print("    index:", list(mf.index.names), "| 覆盖:", mf.index.get_level_values("trade_date").min().date(),
      "~", mf.index.get_level_values("trade_date").max().date(), "| 行数", len(mf))
print("    列:", list(mf.columns))
mfd = mf.reset_index()
mfd["trade_date"] = pd.to_datetime(mfd["trade_date"])
mfd["main_wan"] = mfd["buy_lg_amount"] + mfd["buy_elg_amount"] - mfd["sell_lg_amount"] - mfd["sell_elg_amount"]
sub = mfd[mfd["trade_date"] >= "2026-08-17"]
print("    2026-08-17 起逐日行数 / main_wan 中位数(万元口径核验):")
for t, g in sub.groupby("trade_date"):
    print("      %s  %6d  med=%+.1f万  p95=%+.0f万" % (t.date(), len(g), g["main_wan"].median(), g["main_wan"].quantile(0.95)), end="")
    if t.weekday() == 4 or t == sub["trade_date"].max():
        print()
# 与 net_mf_amount 对比（单位确认）
if "net_mf_amount" in mfd.columns:
    chk = mfd[mfd["trade_date"] == "2026-09-10"]
    print("    2026-09-10 net_mf_amount 中位 %+.1f万 vs lg+elg主净 中位 %+.1f万（口径差：net_mf=全四档净额）"
          % (chk["net_mf_amount"].median(), chk["main_wan"].median()))

print()
print("[3] board/ths_daily.parquet (F5 回退)")
td = pd.read_parquet(os.path.join(ASTOCK, "board", "ths_daily.parquet"))
tdx = td.reset_index() if "trade_date" not in td.columns else td
tdx["trade_date"] = pd.to_datetime(tdx["trade_date"])
tdx["ts_code"] = tdx["ts_code"].astype(str)
t881 = tdx[tdx["ts_code"].str.startswith("881")]
print("    覆盖:", tdx["trade_date"].min().date(), "~", tdx["trade_date"].max().date(), "| 行数", len(tdx), "| 881 行数", len(t881))
print("    881 最新几个交易日:")
for t, g in t881[t881["trade_date"] >= "2026-08-01"].groupby("trade_date"):
    print("      %s  %d 板块" % (t.date(), len(g)))

print()
print("[4] data_live/incremental_daily.parquet (F5 自算 + 前向行情增量)")
if os.path.isfile(INCR):
    inc = pd.read_parquet(INCR)
    inc["trade_date"] = pd.to_datetime(inc["trade_date"])
    print("    覆盖:", inc["trade_date"].min().date(), "~", inc["trade_date"].max().date(), "| 行数", len(inc))
    print("    列:", list(inc.columns))
    print("    2026-08-14 起逐日行数:")
    for t, g in inc[inc["trade_date"] >= "2026-08-14"].groupby("trade_date"):
        susp = int((g["suspendFlag"] != 0).sum()) if "suspendFlag" in g.columns else -1
        print("      %s  %6d (停牌%d)" % (t.date(), len(g), susp), end="")
        if t.weekday() == 4 or t == inc["trade_date"].max():
            print()
else:
    print("    !! 不存在:", INCR)

print()
print("[5] 板块成分表（同花顺 881，静态 8/14）")
comp_path = os.path.join(ASTOCK, "board", "source", "parquet行业概念板块全量更新到20260814",
                         "parquet", "行业概念板块", "行业板块成分汇总_同花顺.parquet")
comp = pd.read_parquet(comp_path)
comp["股票代码"] = comp["股票代码"].astype(str)
comp["指数代码"] = comp["指数代码"].astype(str)
n_ind = comp["指数代码"].nunique()
n_stk = comp["股票代码"].nunique()
print("    板块数(881):", n_ind, "| 成分股票数:", n_stk, "| 行数", len(comp))
print("    指数代码样例:", sorted(comp["指数代码"].unique())[:5])

print()
print("[6] 实盘 selections 清单")
import glob
top2 = sorted(glob.glob(os.path.join(SEL, "*_g2_top2.csv")))
top10 = sorted(glob.glob(os.path.join(SEL, "*_g2_top10.csv")))
print("    top2 文件:", len(top2), ":", [os.path.basename(f)[0:8] for f in top2])
print("    top10 文件:", len(top10), ":", [os.path.basename(f)[0:8] for f in top10])
# 每个文件行数
print("    top10 各文件行数:")
for f in top10:
    n = len(pd.read_csv(f))
    print("      %s: %d" % (os.path.basename(f), n))
