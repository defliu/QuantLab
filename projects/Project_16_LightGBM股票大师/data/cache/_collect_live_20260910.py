# -*- coding: utf-8 -*-
import sys, os, json, time, urllib.request
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from scripts.data_source_router import record

CAND = ["601890.SH", "600551.SH", "301210.SZ", "600830.SH", "000949.SZ",
        "603207.SH", "601058.SH", "002888.SZ", "300990.SZ", "002396.SZ"]

def tc_code(code):
    p = "sh" if code.endswith(".SH") else "sz"
    return p + code[:6]

def std_from_raw(raw):
    if raw.startswith(("0", "3")):
        return raw + ".SZ"
    return raw + ".SH"

def fetch_quotes(codes):
    q = ",".join(tc_code(c) for c in codes)
    url = "http://qt.gtimg.cn/q=" + q
    t0 = time.time()
    req = urllib.request.Request(url)
    content = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
    lat = int((time.time() - t0) * 1000)
    out = {}
    for chunk in content.replace("\n", "").strip().split(";"):
        chunk = chunk.strip()
        if "=" not in chunk or "v_" not in chunk:
            continue
        try:
            f = chunk.split('"')[1].split("~")
            if len(f) < 50:
                continue
            code = std_from_raw(f[2])
            prev = float(f[4])
            price = float(f[3])
            out[code] = {
                "name": f[1],
                "price": price,
                "prev_close": prev,
                "open": float(f[5]) if f[5] else 0,
                "vol_hand": float(f[6]) if f[6] else 0,
                "high": float(f[33]) if f[33] else 0,
                "low": float(f[34]) if f[34] else 0,
                "amount_wan": float(f[37]) if f[37] else 0,
                "turnover": float(f[38]) if f[38] else 0,
                "pe": float(f[39]) if f[39] else 0,
                "liangbi": float(f[49]) if f[49] else 0,
                "time": f[30] if len(f) > 30 else "",
                "pct_chg": float(f[32]) if f[32] else round((price - prev) / prev * 100, 2) if prev else 0,
            }
        except Exception:
            continue
    return out, lat

t0 = time.time()
try:
    quotes, lat = fetch_quotes(CAND)
    record("tencent", True, lat)
    print("TENCENT_OK latency=%dms stocks=%d" % (lat, len(quotes)))
    for c in CAND:
        q = quotes.get(c)
        if q:
            print("%s %s: price=%.2f pct=%.2f%% turnover=%.2f%% pe=%.1f liangbi=%.2f t=%s" % (
                c, q["name"], q["price"], q["pct_chg"], q["turnover"], q["pe"], q["liangbi"], q["time"]))
        else:
            print("MISSING %s" % c)
except Exception as e:
    record("tencent", False)
    print("TENCENT_FAIL: %r" % e)
    quotes = {}

os.makedirs("data/cache", exist_ok=True)
with open("data/cache/tencent_quotes_live_20260910.json", "w", encoding="utf-8") as f:
    json.dump({"date": "20260910", "latency_ms": lat, "stocks": quotes}, f, ensure_ascii=False, indent=2)
print("saved tencent_quotes_live_20260910.json")

t1 = time.time()
try:
    content = urllib.request.urlopen("http://qt.gtimg.cn/q=sh000300", timeout=10).read().decode("gbk")
    lat2 = int((time.time() - t1) * 1000)
    line = content.replace("\n", "").strip()
    if "=" in line:
        f = line.split('"')[1].split("~")
        name = f[1]
        price = float(f[3])
        pct = float(f[32]) if len(f) > 32 and f[32] else 0.0
        print("INDEX %s: price=%.2f pct=%.2f%% lat=%dms" % (name, price, pct, lat2))
        record("tencent_index", True, lat2)
    else:
        print("INDEX_PARSE_FAIL: %r" % content[:200])
except Exception as e:
    record("tencent_index", False)
    print("INDEX_FAIL: %r" % e)
