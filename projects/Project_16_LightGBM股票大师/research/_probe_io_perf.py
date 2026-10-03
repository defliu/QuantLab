# -*- coding: utf-8 -*-
"""临时探针：核验 market_last_date 的低成本读法 + parquet 索引列名。"""
import io
import os
import sys
import time

import pandas as pd

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "io_perf_probe.txt")
MAIN = "D:/astock/daily/stock_daily.parquet"
IW = "D:/astock/index_weight"
TARGET = "000905.SH"

lines = []


def w(s):
    lines.append(str(s))


try:
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(MAIN)
    w("arrow schema names: %s" % (pf.schema_arrow.names,))
    md = pf.metadata
    w("num_rows=%d num_row_groups=%d" % (md.num_rows, md.num_row_groups))
    # 找 trade_date 列的统计信息，直接取 max，避免读整列
    names = pf.schema_arrow.names
    if "trade_date" in names:
        ci = names.index("trade_date")
        best = None
        t0 = time.time()
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(ci).statistics
            if st is not None and st.has_min_max:
                mx = st.max
                best = mx if best is None or str(mx) > str(best) else best
        w("rowgroup stats max trade_date=%s (%.2fs, %d groups)"
          % (best, time.time() - t0, md.num_row_groups))
except Exception as e:
    w("pyarrow probe failed: %r" % (e,))

for cols in (["trade_date"], ["ts_code", "trade_date"]):
    t0 = time.time()
    try:
        d = pd.read_parquet(MAIN, columns=cols)
        w("read_parquet columns=%s -> shape=%s idx_names=%s (%.2fs)"
          % (cols, d.shape, list(d.index.names), time.time() - t0))
        if "trade_date" in d.columns:
            w("   trade_date max=%s" % d["trade_date"].max())
        else:
            w("   index level0 max=%s" % d.index.get_level_values(0).max())
    except Exception as e:
        w("read_parquet columns=%s FAILED: %r (%.2fs)"
          % (cols, e, time.time() - t0))

# 指数成分：命中文件的轻量探测成本
t0 = time.time()
import glob
files = sorted(glob.glob(os.path.join(IW, "index_weight_*.parquet")))
w("glob files=%d (%.2fs)" % (len(files), time.time() - t0))

t0 = time.time()
hit = 0
for p in files:
    try:
        one = pd.read_parquet(p, columns=["index_code"])
    except Exception:
        continue
    if one is None or one.empty:
        continue
    if TARGET in set(one["index_code"].astype(str)):
        hit += 1
w("probe-all-files(%d) hits=%d (%.2fs)" % (len(files), hit, time.time() - t0))

# ts_code 过滤下推在 22 只票上的耗时
codes = ["000920.SZ", "688155.SH", "300750.SZ", "600519.SH", "002594.SZ",
         "000001.SZ", "601318.SH", "000333.SZ", "600036.SH", "002415.SZ",
         "300059.SZ", "688111.SH", "601899.SH", "000651.SZ", "600276.SH",
         "002304.SZ", "300760.SZ", "688012.SH", "600030.SH", "601166.SH",
         "000858.SZ", "600887.SH"]
t0 = time.time()
d = pd.read_parquet(MAIN, columns=["pct_chg"],
                    filters=[("ts_code", "in", codes)])
w("returns 22 codes -> %d rows (%.2fs) idx_names=%s"
  % (len(d), time.time() - t0, list(d.index.names)))

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("WROTE", OUT)
