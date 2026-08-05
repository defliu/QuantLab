# coding: utf-8
"""对比 原版 / 硬质量 / 温和质量 三版 ATR 低波回测结果。"""
import json, glob, os, csv
import numpy as np

REP = "D:/QuantLab/reports"

VARIANTS = [
    ("atr_lowvol_fw", "原版(纯低波广度)"),
    ("atr_lowvol_quality_fw", "硬质量(红利2%+FCF+ROE稳8)"),
    ("atr_lowvol_quality_mild_fw", "温和质量(关红利+FCF宽松+ROE稳4)"),
]


def find(name_sub):
    all_dirs = [d for d in glob.glob(os.path.join(REP, "*"))
                if os.path.isdir(d)]
    matched = [d for d in all_dirs if name_sub in os.path.basename(d)]
    matched.sort(key=lambda d: os.path.getmtime(d))
    return matched[-1] if matched else None


def load_sum(d):
    with open(os.path.join(d, "summary.json"), encoding="utf-8") as f:
        return json.load(f)


def yearly(d):
    rows = []
    with open(os.path.join(d, "equity_curve.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    per_year = {}
    for r in rows:
        y = r["date"][:4]
        try:
            v = float(r["total_asset"])
        except Exception:
            continue
        per_year.setdefault(y, []).append(v)
    return {y: (vals[-1] / vals[0] - 1)
            for y, vals in per_year.items() if len(vals) >= 2}


def fmt(v):
    return "%.2f%%" % (v * 100) if isinstance(v, (int, float)) else str(v)


dirs = {}
sums = {}
years = {}
for sub, label in VARIANTS:
    d = find(sub)
    if not d:
        print("[MISSING] %s (%s)" % (sub, label))
        continue
    dirs[label] = d
    sums[label] = load_sum(d)
    years[label] = yearly(d)

labels = [l for l, _ in VARIANTS if l in sums]
if not labels:
    raise SystemExit("no results found")

print("=" * 90)
print("ATR 低波 三版对比 (2023-01 ~ 2026-07, 等权/不杠杆/季频/100只上限)")
print("=" * 90)
fields = ["total_return", "annual_return", "max_drawdown", "sharpe",
          "calmar", "win_rate", "excess_return", "information_ratio"]
hdr = "%-16s" % "指标" + "".join("%16s" % l.split("(")[0] for l in labels)
print(hdr)
for fld in fields:
    row = "%-16s" % fld
    for l in labels:
        v = sums[l]["performance"].get(fld)
        row += "%16s" % (fmt(v) if v is not None else "-")
    print(row)
print()
print("期末持仓数:")
for l in labels:
    print("  %s = %d" % (l, sums[l]["portfolio_end"]["n_positions"]))
print()
print("年度收益(策略):")
allyears = sorted(set().union(*[set(years[l].keys()) for l in labels]))
print("%-6s" % "年" + "".join("%16s" % l.split("(")[0] for l in labels))
for y in allyears:
    row = "%-6s" % y
    for l in labels:
        v = years[l].get(y)
        row += "%16s" % (fmt(v) if v is not None else "-")
    print(row)
