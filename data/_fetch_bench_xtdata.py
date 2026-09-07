# coding: utf-8
"""fetch index daily via xtdata (venv 3.11) -> dump JSON for build step."""
import sys, json, time, os
import xtquant.xtdata as xt

CODES = [
    "000300.SH", "000905.SH", "000001.SH", "399001.SZ",
    "399006.SZ", "000016.SH", "000852.SH", "399005.SZ",
]
START, END = "20090101", "20260821"
OUT = sys.argv[1] if len(sys.argv) > 1 else "D:/astock/benchmark/_bench_raw.json"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

print("[fetch] 下载指数日线 %s ~ %s" % (START, END), flush=True)
for c in CODES:
    try:
        xt.download_history_data(c, "1d", START, END)
    except Exception as e:
        print("  download warn %s: %s" % (c, e), flush=True)

out = {}
for c in CODES:
    try:
        raw = xt.get_local_data(field_list=["close"], stock_list=[c], period="1d",
                                start_time=START, end_time=END, count=-1)
        df = raw.get(c)
        if df is None or len(df) == 0:
            print("  [skip] %s 无数据" % c, flush=True)
            continue
        series = df["close"] if hasattr(df, "columns") else df
        rows = []
        for dt, v in series.items():
            if v is None:
                continue
            dint = int(dt)  # xtdata 返回 YYYYMMDD 整数索引
            ds = "%s-%s-%s" % (str(dint)[:4], str(dint)[4:6], str(dint)[6:8])
            rows.append([ds, float(v)])
        rows.sort(key=lambda x: x[0])
        out[c] = rows
        print("  [ok] %s %d 行 %s~%s" % (c, len(rows), rows[0][0], rows[-1][0]), flush=True)
    except Exception as e:
        print("  [err] %s: %s" % (c, e), flush=True)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f)
print("[fetch] 写入 %s，共 %d 个指数" % (OUT, len(out)), flush=True)
