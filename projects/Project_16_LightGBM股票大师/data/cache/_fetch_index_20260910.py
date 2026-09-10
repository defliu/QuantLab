# -*- coding: utf-8 -*-
import urllib.request, time, json, sys, os
sys.path.insert(0, os.getcwd())
from scripts.data_source_router import record
t1 = time.time()
content = urllib.request.urlopen("http://qt.gtimg.cn/q=sh000300", timeout=10).read().decode("gbk")
lat = int((time.time() - t1) * 1000)
f = content.split('"')[1].split("~")
name, price, prev = f[1], float(f[3]), float(f[4])
pct = float(f[32]) if f[32] else round((price - prev) / prev * 100, 2)
print("INDEX %s price=%.2f pct=%.2f%% lat=%dms" % (name, price, pct, lat))
record("tencent_index", True, lat)
os.makedirs("data/cache", exist_ok=True)
json.dump({"date": "20260910", "data": {name: {"code": "000300", "price": price, "chg_pct": pct}},
           "ts": time.strftime("%Y%m%d%H%M%S")},
          open("data/cache/tencent_index_live_20260910.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
