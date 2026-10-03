# coding: utf-8
"""选股相关性诊断 · 探针2（只读）。

目标（不写任何业务数据）：
  1. 申万行业分类：确认可用层级、con_codes 空值比例、行业名分布；
  2. 行业备选口径：stock_basic.industry 的实际取值形态；
  3. 宽基指数：index_basic 名称 -> index_code 映射；
  4. index_weight 文件族：实际覆盖哪些 index_code、最新日期、样本数；
  5. 主行情最新日期与候选覆盖能力。
"""
import glob
import io
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

ASTOCK = "D:/astock"
IW = os.path.join(ASTOCK, "index_weight")
REPORT = os.path.join(os.environ.get("TEMP", "."), "probe2_report.txt")

_buf = []
_stdout_print = print


def print(*args, **kwargs):
    _buf.append(" ".join(str(a) for a in args))
    _stdout_print(*args, **kwargs)


def flush_report():
    with io.open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_buf))
    _stdout_print("REPORT=%s lines=%d" % (REPORT, len(_buf)))


def is_codes(x):
    return isinstance(x, (list, tuple, np.ndarray)) and len(x) > 0


def main():
    pd.set_option("display.width", 240)
    pd.set_option("display.max_rows", 200)

    print("=" * 70)
    print("[1] 申万 sw_l1_daily 结构")
    sw = pd.read_parquet(os.path.join(ASTOCK, "index", "sw_l1_daily.parquet"))
    sw_dates = sorted(sw.index.get_level_values(0).unique())
    print("总天数=%d 首=%s 末=%s" % (len(sw_dates), str(sw_dates[0])[:10], str(sw_dates[-1])[:10]))
    print("index names=%s" % list(sw.index.names))
    print("columns=%s" % list(sw.columns))
    latest = sw_dates[-1]
    sub = sw.loc[latest]
    print("最新截面(%s) 行数=%d" % (str(latest)[:10], len(sub)))
    sub2 = sub.reset_index()
    mask = sub2["con_codes"].map(is_codes)
    print("con_codes 有效行=%d / 无效行=%d" % (int(mask.sum()), int((~mask).sum())))
    valid = sub2[mask]
    valid = valid.assign(
        n_codes=valid["con_codes"].map(len),
        first_code=valid["con_codes"].map(lambda x: str(x[0])),
    )
    cols = [c for c in ["ts_code", "name", "n_codes", "first_code"] if c in valid.columns]
    print("--- 有效 con_codes 前 25 行 ---")
    print(valid[cols].head(25).to_string())
    print("--- 有效 con_codes n_codes 分布 ---")
    print(valid["n_codes"].describe().to_string())
    print("--- 无效 con_codes 的 name 样例（最多 20）---")
    print(sub2[~mask]["name"].head(20).tolist())

    print("")
    print("=" * 70)
    print("[2] sw_l1_daily 名称分类学：区分一级/风格/综合")
    names = sub2["name"].astype(str)
    print("名称总数=%d 唯一=%d" % (len(names), names.nunique()))
    kw = {
        "大盘": "大盘",
        "中盘": "中盘",
        "小盘": "小盘",
        "成长": "成长",
        "价值": "价值",
        "综合": "综合",
    }
    for k, v in kw.items():
        hit = names[names.str.contains(k, na=False)]
        print("  含'%s' -> %d 个，样例=%s" % (k, len(hit), hit.head(6).tolist()))
    # 申万一级典型行业名
    l1_guess = ["农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "家用电器",
                "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业",
                "交通运输", "房地产", "商贸零售", "社会服务", "综合", "建筑材料",
                "建筑装饰", "电力设备", "机械设备", "国防军工", "计算机",
                "传媒", "通信", "银行", "非银金融", "汽车", "煤炭", "石油石化",
                "环保", "美容护理"]
    hit_l1 = names[names.isin(l1_guess)]
    print("  精确匹配申万一级名(%d个定义) -> %d 命中: %s"
          % (len(l1_guess), len(hit_l1), hit_l1.tolist()))

    print("")
    print("=" * 70)
    print("[3] stock_basic.industry 取值形态")
    sb = pd.read_parquet(os.path.join(ASTOCK, "basic", "stock_basic.parquet"))
    print("shape=%s cols=%s" % (sb.shape, list(sb.columns)))
    if "industry" in sb.columns:
        vc = sb["industry"].astype(str).value_counts()
        print("industry 唯一值=%d" % len(vc))
        print(vc.head(40).to_string())
        print("...尾部...")
        print(vc.tail(15).to_string())
        # 与申万一级名重合度
        inter = set(vc.index) & set(l1_guess)
        print("与申万一级名重合=%d 个 -> %s" % (len(inter), sorted(inter)[:20]))

    print("")
    print("=" * 70)
    print("[4] 宽基指数名称 -> index_code")
    ib = pd.read_parquet(os.path.join(IW, "index_basic.parquet"))
    print("index_basic shape=%s cols=%s" % (ib.shape, list(ib.columns)))
    want = ["沪深300", "中证500", "中证1000", "中证2000", "上证50", "科创50",
            "创业板指", "创业板50", "中证100", "上证180", "上证380"]
    hit = ib[ib["name"].isin(want)]
    show = [c for c in ["ts_code", "name", "market", "publisher", "category",
                        "list_date", "base_date"] if c in hit.columns]
    print(hit[show].to_string())
    print("index_basic 全部 category=%s" % ib["category"].value_counts().to_dict())

    print("")
    print("=" * 70)
    print("[5] index_weight 文件族覆盖（只统计前若干文件 + 目标指数）")
    all_files = sorted(glob.glob(os.path.join(IW, "index_weight_*.parquet")))
    # glob 的 * 会吞掉 _data_ 子族，必须按文件名区分，否则两族重复计数
    files = [p for p in all_files if "_data_" not in os.path.basename(p)]
    files_data = [p for p in all_files if "_data_" in os.path.basename(p)]
    print("index_weight_*.parquet=%d 个" % len(files))
    print("index_weight_data_*.parquet=%d 个" % len(files_data))

    targets = {
        "000300.SH": "沪深300", "000905.SH": "中证500", "000852.SH": "中证1000",
        "932000.CSI": "中证2000", "000016.SH": "上证50", "000688.SH": "科创50",
        "399006.SZ": "创业板指", "399673.SZ": "创业板50", "000010.SH": "上证180",
        "000009.SH": "上证380", "399300.SZ": "沪深300(SZ)", "399905.SZ": "中证500(SZ)",
        "399852.SZ": "中证1000(SZ)",
    }

    def summarize(paths, tag):
        cnt = Counter()
        date_by_code = {}
        n_by_code = Counter()
        bad = []
        for p in paths:
            try:
                d = pd.read_parquet(p, columns=["index_code", "trade_date"])
            except Exception as exc:
                bad.append((os.path.basename(p), str(exc)))
                continue
            if d.empty or "index_code" not in d.columns:
                continue
            code = str(d["index_code"].iloc[0])
            cnt[code] += 1
            mx = str(d["trade_date"].max())[:10]
            cur = date_by_code.get(code)
            if cur is None or mx > cur:
                date_by_code[code] = mx
            n_by_code[code] += len(d)
        print("--- %s: 唯一 index_code=%d ---" % (tag, len(cnt)))
        rows = []
        for code, nfile in sorted(cnt.items()):
            if code not in targets:
                continue
            rows.append((code, targets[code], nfile, date_by_code.get(code, ""), n_by_code[code]))
        out = pd.DataFrame(rows, columns=["index_code", "name", "files", "max_date", "rows"])
        print("目标宽基命中 %d/%d :" % (len(out), len(targets)))
        print(out.to_string() if len(out) else "  （无）")
        missing = sorted(set(targets) - set(cnt.keys()))
        print("目标宽基缺失 %d 个: %s" % (len(missing), missing))
        if bad:
            print("读取失败 %d 个，样例=%s" % (len(bad), bad[:3]))
        return out

    out_main = summarize(files, "index_weight")
    out_data = summarize(files_data, "index_weight_data")

    print("")
    print("--- 目标宽基 date 格式样例（原始值）---")
    for p in files[:3]:
        d = pd.read_parquet(p, columns=["index_code", "trade_date"])
        print("%s -> index_code=%r trade_date dtype=%s sample=%r"
              % (os.path.basename(p), str(d["index_code"].iloc[0]),
                 d["trade_date"].dtype, d["trade_date"].iloc[0]))

    print("")
    print("=" * 70)
    print("[6] 主行情最新日期")
    md = pd.read_parquet(
        os.path.join(ASTOCK, "daily", "stock_daily.parquet"),
        columns=["close", "pct_chg"],
    )
    md_dates = sorted(md.index.get_level_values(0).unique())
    print("主行情末日=%s 总天数=%d" % (str(md_dates[-1])[:10], len(md_dates)))
    print("最近8个=%s" % [str(d)[:10] for d in md_dates[-8:]])

    print("")
    print("[PROBE2-DONE]")
    flush_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
