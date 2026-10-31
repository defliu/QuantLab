# coding: utf-8
"""把 2026-10-26~10-30 的候选（修复口径）补进 paper_forward_live.csv，标 source=backfill。

背景（2026-10-31，T-20261031-003）：
  10-24~10-30 增量库断链 → 候选未落盘。修复 build_g2_daily 后已用主库重建这五天的候选
  （临时目录验证：10-26/27/29/30 空仓，10-28 一只）。本脚本把结果补进台账，保持序列连续。

口径断代：
  旧行（8/17~10/23）是「amount_ma5 失效 + 宇宙未筛」口径，标 source=forward（原样保留）；
  本脚本补入的行标 source=backfill —— **事后重算，不当 OOS 证据**。
  统计侧（forward_stats / paper_forward_downgrade）应排除 backfill。

用法：python _backfill_live_20261031.py [--dry-run]
"""
import argparse
import io
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "data", "real")
LIVE = os.path.join(REAL, "paper_forward_live.csv")
CAND_DIR = os.path.join(os.environ.get("TEMP", ""), "g2tmp", "selections_g2")
DATES = ["20261026", "20261027", "20261028", "20261029", "20261030"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(LIVE, encoding="utf-8-sig")
    if "source" not in df.columns:
        df["source"] = "forward"
    before = len(df)
    df = df.drop_duplicates(subset=["date", "code"], keep="last").reset_index(drop=True)
    if len(df) != before:
        print("去重：%d -> %d 行" % (before, len(df)))

    rows = []
    for d in DATES:
        p = os.path.join(CAND_DIR, "%s_g2_top10.csv" % d)
        if not os.path.exists(p):
            print("%s 空仓（无候选）" % d)
            continue
        c = pd.read_csv(p, encoding="utf-8-sig").sort_values("total_new", ascending=False).reset_index(drop=True)
        for i, r in c.iterrows():
            rows.append({
                "date": "%s-%s-%s" % (d[:4], d[4:6], d[6:]),
                "code": r["ts_code"],
                "total_new": float(r["total_new"]),
                "prob": float(r["prob"]),
                "rank": i + 1,
                "source": "backfill",
            })
        print("%s 补入 %d 只" % (d, len(c)))

    if not rows:
        print("无可补候选")
        return 0
    new = pd.DataFrame(rows)
    df = pd.concat([df, new], ignore_index=True)
    df = df.drop_duplicates(subset=["date", "code"], keep="last")
    if args.dry_run:
        print("[dry-run] 将补入 %d 行，总 %d 行" % (len(new), len(df)))
        print(new.to_string(index=False))
        return 0
    df.to_csv(LIVE + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(LIVE + ".tmp", LIVE)
    print("已补入 %d 行 | 总行数 %d | backfill=%d | forward=%d" % (
        len(new), len(df), int((df["source"] == "backfill").sum()), int((df["source"] == "forward").sum())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
