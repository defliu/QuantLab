# coding: utf-8
"""选股相关性诊断 · 数据源探针（只读）。

用途：确认 sw_l1 / index_weight / stock_basic / 主行情 的实际字段与覆盖，
为相关性诊断脚本确定口径。不写任何业务数据。

用法：
  python audit_correlation_probe.py
"""
import io
import os
import sys

import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASTOCK = "D:/astock"


def head(title):
    print("")
    print("=" * 68)
    print(title)
    print("=" * 68)


def probe(path, name, n=3):
    print("")
    print("--- %s ---" % name)
    print("path=%s exists=%s" % (path, os.path.exists(path)))
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        print("[READ-FAIL] %s" % exc)
        return None
    print("shape=%s" % (df.shape,))
    print("cols=%s" % list(df.columns))
    if "trade_date" in df.columns:
        print("trade_date range=%s ~ %s" % (
            df["trade_date"].min(), df["trade_date"].max()))
    print(df.head(n).to_string())
    return df


def main():
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)

    sw = probe(os.path.join(ASTOCK, "index", "sw_l1_daily.parquet"), "申万一级 sw_l1_daily")
    if sw is not None:
        print("")
        print("sw_l1 name 样例=%s" % sw["name"].dropna().unique()[:8].tolist())
        cc = sw["con_codes"].dropna()
        if len(cc):
            first = str(cc.iloc[0])
            print("con_codes 样例(前200字符)=%s" % first[:200])
            print("con_codes 分隔符推断: 逗号=%d 顿号=%d 空格=%d 分号=%d" % (
                first.count(","), first.count("、"),
                first.count(" "), first.count(";")))

    iw = probe(os.path.join(ASTOCK, "index_weight", "index_weight_0010.parquet"),
               "指数成分 index_weight_0010")
    if iw is not None:
        if "index_code" in iw.columns:
            vc = iw["index_code"].value_counts()
            print("")
            print("index_code 分布(前15)=%s" % vc.head(15).to_dict())

    ib = probe(os.path.join(ASTOCK, "index_weight", "index_basic.parquet"),
               "指数基础 index_basic")
    if ib is not None and "name" in ib.columns:
        print("")
        print("指数名称样例=%s" % ib["name"].dropna().unique()[:15].tolist())

    sb = probe(os.path.join(ASTOCK, "basic", "stock_basic.parquet"), "stock_basic")
    if sb is not None and "industry" in sb.columns:
        print("")
        print("industry 唯一值数=%d" % sb["industry"].nunique())
        print("industry 样例=%s" % sb["industry"].dropna().unique()[:15].tolist())

    md = probe(os.path.join(ASTOCK, "daily", "stock_daily.parquet"), "主行情 stock_daily", n=2)
    if md is not None:
        need = ["ts_code", "trade_date", "close", "pct_chg", "amount",
                "turnover_rate", "pe_ttm", "pb", "circ_mv"]
        print("")
        print("相关性所需字段齐备性:")
        for c in need:
            print("  %-14s %s" % (c, c in md.columns))

    db = probe(os.path.join(ASTOCK, "daily", "daily_basic.parquet"), "daily_basic", n=2)

    sel = os.path.join(PROJ, "data", "selections")
    head("候选文件内容")
    for rel in [
        os.path.join("g2", "20260930_g2_top10.csv"),
        os.path.join("g2_ens", "20260930_g2_top10.csv"),
        "20260930_selection_full.csv",
    ]:
        p = os.path.join(sel, rel)
        print("")
        print("--- %s ---" % rel)
        if not os.path.exists(p):
            print("[MISSING]")
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        print("shape=%s" % (df.shape,))
        print("cols=%s" % list(df.columns))
        cols = [c for c in ["ts_code", "name", "prob", "model_prob", "total_new",
                            "total", "pe_ttm", "industry_pct", "ind_label",
                            "turnover_rate", "liangbi", "vr", "trade_date"]
                if c in df.columns]
        print(df[cols].to_string())

    print("")
    print("[PROBE-DONE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())