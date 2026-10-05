# coding: utf-8
"""adj_factor 尺度连续性门禁（只读）。

「只追加 E 独有行」方案的最后一道致命风险：
前复权价 = close * (adj_factor / 最新adj_factor)。
一旦追加新行，每只股票的「最新 adj_factor」都会变化 —— 这是正常的除权
行为。但如果 E 的 adj_factor 与 D 不同尺度（例如 E 用了不同的基期或
除以了常数），全历史前复权价会被静默改写，且不报错。

判定方法（不用未来的 D 数据，只用 E 内部 + D 已有数据）：
  1. 同票相邻交易日连续性：D 末日 vs E 末日(20260924) 的 adj_factor
     在无除权日应几乎相等。
  2. 变更幅度分布：若 E 尺度错，变更幅度会是「整齐的常数倍」或
     大量整倍数跳变；正常除权则是零星的小概率跳变。
  3. close 与 adj_factor 的联合一致性：反算不复权价，看是否落在
     D 同票当日价格的合理邻域。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

d = pd.read_parquet(D_PATH, columns=["adj_factor", "close", "vol"])
d = d.reset_index()
e = pd.read_parquet(E_PATH, columns=["trade_date", "ts_code", "adj_factor",
                                    "close", "vol"])
for df in (d, e):
    df["ts_code"] = df["ts_code"].astype(str)
d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
e["trade_date"] = e["trade_date"].astype(str).str.replace("-", "", regex=False)

dmax = d["trade_date"].max()
emax = e["trade_date"].max()
print("D 最新交易日=%s  E 最新交易日=%s" % (dmax, emax))

dl = d[d["trade_date"] == dmax][["ts_code", "adj_factor", "close"]]
el = e[e["trade_date"] == emax][["ts_code", "adj_factor", "close"]]
dl = dl.rename(columns={"adj_factor": "af_d", "close": "cl_d"})
el = el.rename(columns={"adj_factor": "af_e", "close": "cl_e"})
m = dl.merge(el, on="ts_code", how="inner")
print("两末日共同股票=%d" % len(m))

ok = m["af_d"].notna() & m["af_e"].notna() & (m["af_d"] > 0)
r = (m.loc[ok, "af_e"] / m.loc[ok, "af_d"])
same = (r - 1).abs() < 1e-9
diff = (r - 1).abs() >= 1e-9
print("\n-- 1. adj_factor 跨末日比值 af_e/af_d --")
print("  完全相等(无除权) 占比=%.2f%%" % (100.0 * float(same.mean())))
print("  变化行数=%d (%.2f%%)  <- 除权日应是很小的比例"
      % (int(diff.sum()), 100.0 * float(diff.mean())))
if bool(diff.any()):
    rd = r[diff]
    print("  变化行的比值分位 p1/p50/p99=%.4f/%.4f/%.4f"
          % tuple(rd.quantile([0.01, 0.5, 0.99]).tolist()))
    # 尺度错乱会表现为大量「整齐常数倍」
    for k in (2.0, 10.0, 100.0, 0.5, 0.1, 0.01):
        hit = int(np.isclose(r, k, rtol=1e-6).sum())
        if hit:
            print("  !! 有 %d 行比值≈%s —— 疑似尺度/基期不同" % (hit, k))

print("\n-- 2. close 跨末日变化（应温和，涨跌停约±10/20/30%）--")
cr = (m.loc[ok, "cl_e"] / m.loc[ok, "cl_d"])
print("  |涨跌| 中位=%.4f  p95=%.4f  max=%.4f"
      % (float(cr.sub(1).abs().median()), float(cr.sub(1).abs().quantile(0.95)),
         float(cr.sub(1).abs().max())))
bad = int(cr.sub(1).abs().gt(0.35).sum())
print("  单日涨跌>35%% 的行数=%d （过多说明 close 口径也变了）" % bad)

print("\n-- 3. 不复权价一致性反算（E 侧 vol/close 与 D 对照）--")
# 用各自的数据自洽性检验：E 的 close 若已是复权价，除权日会出现跳空
emx = e.sort_values(["ts_code", "trade_date"], kind="stable")
g = emx.groupby("ts_code", sort=False)["close"]
emx["chg"] = g.pct_change()
jump = emx["chg"].abs()
print("  E 全表单日涨跌幅 |chg|>35%% 的行数=%d / %d (%.4f%%)"
      % (int(jump.gt(0.35).sum()), len(emx), 100.0 * float(jump.gt(0.35).mean())))
print("  -> 若该比例极低，说明 E 的 close 是不复权价，未被复权污染")

print("\n-- 4. 新区间(20260914~24)内部自洽 --")
nw = e[e["trade_date"] >= "20260914"].sort_values(["ts_code", "trade_date"],
                                                kind="stable")
nw["chg"] = nw.groupby("ts_code", sort=False)["close"].pct_change()
print("  新区间 |chg|>35%% 行数=%d / %d"
      % (int(nw["chg"].abs().gt(0.35).sum()), len(nw)))
print("  新区间 adj_factor 每票 max/min 比值 中位=%.6f"
      % float((nw.groupby("ts_code", sort=False)["adj_factor"].max()
               / nw.groupby("ts_code", sort=False)["adj_factor"].min()
               ).replace([np.inf, -np.inf], np.nan).median()))
print("ADJ_DONE")