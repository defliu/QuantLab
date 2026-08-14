# coding: utf-8
"""从 universe_all_a.csv 抽样生成小 universe（快速验证用）。

用法：
  python gen_universe_sample.py [N]
  默认 N=500，随机抽样 N 只
"""
import random
import csv
import sys

SRC = "D:/QuantLab/data/universe_all_a.csv"
DST = "D:/QuantLab/data/universe_sample.csv"

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    with open(SRC, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    random.seed(42)
    sample = random.sample(rows, min(n, len(rows)))
    with open(DST, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "name", "sector", "enabled"])
        writer.writeheader()
        for r in sample:
            writer.writerow(r)
    print("Sample universe: %d codes -> %s" % (len(sample), DST))

if __name__ == "__main__":
    main()
