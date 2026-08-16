# coding: utf-8
"""通宵 MAX 扫描结果汇总：读各 report 目录 summary.json，输出对照表（纯 json，无 pandas 依赖）。

用法：python research/compile_max_sweep.py
产物：打印对照表 + 写 results/MAX扫描汇总_20260816.md（含结论占位）。
"""
import json
import os

ROOT = "D:/QuantLab"
REPORTS = [
    ("基线(ATR%排序)", None, "reports/20260815_110119_ccfb82_atr_10w_price50"),
    ("MAX=0.10", "max010", "reports/20260815_222334_6537d9_atr_10w_price50_max010"),
    ("MAX=0.15", "max015", None),  # 由脚本自动发现 20260815_*_max015
    ("MAX=0.20(已测)", "max020", "reports/20260815_174019_e77de8_atr_10w_price50_a_max"),
    ("MAX=0.25", "max025", None),
    ("MAX=0.30", "max030", None),
    ("MAX=0.20+流动性池", "max020_liquid", None),
    ("流动性池基线(无MAX)", "liquid", None),
]


def find_report(tag):
    import glob
    pat = os.path.join(ROOT, "reports", "20260815_*_atr_10w_price50_*" + tag + "*")
    hits = sorted(glob.glob(pat))
    if not hits:
        return None
    # 取最新的、含 summary.json 的
    for h in reversed(hits):
        if os.path.exists(os.path.join(h, "summary.json")):
            return h
    return None


def main():
    print("%-20s %8s %8s %9s %7s %6s %6s %7s" % ("版本", "总收益", "年化CAGR", "回撤", "夏普", "卡玛C", "胜率", "交易"))
    rows = []
    for label, tag, path in REPORTS:
        if path is None and tag:
            path = find_report(tag)
        if not path:
            print("%-20s (未完成)" % label)
            rows.append({"label": label, "done": False})
            continue
        with open(os.path.join(path, "summary.json"), "r", encoding="utf-8") as f:
            p = json.load(f)["performance"]
        # 新 summary 自带 cagr/cagr_calmar；旧报告无则回退线性口径（T1 双口径后应全部带 cagr）
        ann = p.get("cagr", p["annual_return"])
        cmar = p.get("cagr_calmar")
        if cmar is None:
            cmar = p["annual_return"] / abs(p["max_drawdown"]) if p["max_drawdown"] else 0
        print("%-20s %7.1f%% %7.2f%% %8.2f%% %6.3f %6.2f %6.1f%% %7d" % (
            label, p["total_return"]*100, ann*100, p["max_drawdown"]*100,
            p["sharpe"], cmar, p["win_rate"]*100, p["n_trades"]))
        rows.append({"label": label, "done": True, "perf": p, "cagr": ann,
                     "cagr_calmar": cmar, "annual_return": p.get("annual_return"),
                     "dir": path})


if __name__ == "__main__":
    main()
