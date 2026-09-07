# coding: utf-8
"""Pull missing ETF daily bars via xtdata (QMT python) -> data/etf_daily/<code>.csv
Run with QMT pythonw.exe. All output goes to _fetch_etf_xtdata.log (no console).
"""
import sys, os, csv, time, traceback
sys.path.insert(0, r"D:/国金QMT交易端模拟/bin.x64/Lib/site-packages")

OUTDIR = r"D:/QuantLab/data/etf_daily"
os.makedirs(OUTDIR, exist_ok=True)
LOGF = r"D:/QuantLab/data/_fetch_etf_xtdata.log"
FIELDS = ["open", "high", "low", "close", "volume", "amount"]

# code -> file suffix (match existing naming: 510300_SH.csv)
TARGETS = [
    ("518880.SH", "518880_SH"),
    ("511010.SH", "511010_SH"),
    ("511260.SH", "511260_SH"),
    ("159985.SZ", "159985_SZ"),
]
START, END = "20100101", "20260829"


def log(msg):
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    # also try console (harmless if no tty)
    try:
        print(msg, flush=True)
    except Exception:
        pass


log("=== ETF fetch start %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
import xtquant.xtdata as xt
log("xtdata connected: %s" % getattr(xt, "connect", "n/a"))

for code, suffix in TARGETS:
    try:
        log("[dl] %s %s~%s" % (code, START, END))
        xt.download_history_data(code, "1d", START, END)
        raw = xt.get_local_data(field_list=FIELDS, stock_list=[code], period="1d",
                                 start_time=START, end_time=END, count=-1)
        df = raw.get(code)
        if df is None or len(df) == 0:
            log("  [skip] %s 无数据" % code)
            continue
        rows = []
        for dt, r in df.iterrows():
            # xtdata get_local_data index is int YYYYMMDD -> normalize to YYYY-MM-DD
            s = str(dt)
            ds = "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
            try:
                rows.append([
                    ds,
                    float(r.get("amount", 0) or 0),
                    float(r.get("close", 0) or 0),
                    float(r.get("high", 0) or 0),
                    float(r.get("low", 0) or 0),
                    float(r.get("open", 0) or 0),
                    float(r.get("volume", 0) or 0),
                    code,
                ])
            except Exception as e:
                log("  [row-err] %s %s: %s" % (code, ds, e))
        rows.sort(key=lambda x: x[0])
        outp = os.path.join(OUTDIR, suffix + ".csv")
        with open(outp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "amount", "close", "high", "low", "open", "volume", "code"])
            w.writerows(rows)
        log("  [ok] %s -> %s  %d 行 %s~%s" % (code, outp, len(rows),
              rows[0][0] if rows else "-", rows[-1][0] if rows else "-"))
    except Exception as e:
        log("  [ERR] %s: %s" % (code, repr(e)))
        log(traceback.format_exc())
log("=== ETF fetch done %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
