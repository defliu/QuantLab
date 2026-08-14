# coding: utf-8
"""生成 universe_all_a.csv —— 从 astock parquet 提取全 A 股代码。

输出格式（符合 data/universe.py 的 schema 要求）：
  code,name,sector,enabled
  000001.SZ,平安银行,银行,true
  ...

用法：
  python gen_universe_all_a.py
"""
import pyarrow.parquet as pq
import csv

PARQUET_PATH = "E:/astock/daily/stock_daily.parquet"
OUTPUT_PATH = "D:/QuantLab/data/universe_all_a.csv"

def main():
    # 读取所有唯一代码（字段名是 ts_code）
    t = pq.read_table(PARQUET_PATH, columns=["ts_code"])
    codes_raw = t["ts_code"].to_pylist()
    # 过滤 None 和空字符串
    codes = sorted(set(c for c in codes_raw if c and isinstance(c, str)))

    print("Total unique codes: %d" % len(codes))
    print("Sample codes: %s" % codes[:5])

    # 写入 CSV
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["code", "name", "sector", "enabled"])
        for code in codes:
            # 过滤非 A 股代码（BJ 北交所暂不纳入）
            if code.endswith(".BJ"):
                continue
            writer.writerow([code, "", "", "true"])

    print("Universe CSV written to: %s" % OUTPUT_PATH)
    print("Total A-share codes (excl BJ): %d" % sum(1 for c in codes if not c.endswith(".BJ")))

if __name__ == "__main__":
    main()
