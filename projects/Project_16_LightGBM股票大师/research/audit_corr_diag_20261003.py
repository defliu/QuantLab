# -*- coding: utf-8 -*-
"""corr_diag 夜间批次（10-02 23:10 ~ 10-03 02:34）独立审计脚本（只读）。

审计纪律（AGENTS）：不 import corr_diag 任何模块、不引用其输出，
直接用原始数据（selection CSV / stock_daily.parquet / sw_l1 parquet）
复算 reports/corr_diag/ 三份报告的关键数字。

复算项：
  [A] 三策略批次候选清单 = 报告 codes（来源 CSV 逐一对）
  [B] market_last_date == 20260911（pyarrow 行组统计）
  [C] ens 7 票 60 日收益两两相关（21 对全表 vs 报告表，容差 5e-4）
  [D] ens 行业映射（申万一级最新有效截面自建）→ 覆盖 7/7 / 6 行业 /
      轻工制造 2 只 / HHI 0.1837
  [E] 三份报告 thresholds(JSON) 与 md 阈值表一致性

用法：python audit_corr_diag_20261003.py
退出码 0 = 全部 PASS。
"""
import io
import json
import os
import sys

import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASTOCK = "D:/astock"
MAIN_DAILY = os.path.join(ASTOCK, "daily", "stock_daily.parquet")
SW_L1 = os.path.join(ASTOCK, "index", "sw_l1_daily.parquet")
RPT_DIR = os.path.join(PROJ, "results", "corr_diag")
BATCH = "20260930"
END_DATE = 20260911  # 报告声称的行情末日，本脚本 [B] 独立验证
WINDOW = 60

CASES = [
    ("v13", os.path.join(PROJ, "data", "selections",
                         "%s_selection_full.csv" % BATCH)),
    ("g2", os.path.join(PROJ, "data", "selections", "g2",
                        "%s_g2_top10.csv" % BATCH)),
    ("ens", os.path.join(PROJ, "data", "selections", "g2_ens",
                         "%s_g2_top10.csv" % BATCH)),
]

_NPASS = [0]
_NFAIL = [0]


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    if ok:
        _NPASS[0] += 1
    else:
        _NFAIL[0] += 1
    print("[%s] %s%s" % (tag, name, ("  | " + detail) if detail else ""))
    return ok


def norm_code(raw):
    """归一化 6 位代码为带后缀形式；脏值返回 None。独立实现。"""
    s = str(raw).strip().upper()
    if "." in s:
        head, tail = s.split(".", 1)
        if tail in ("SH", "SZ", "BJ") and len(head) == 6:
            return "%s.%s" % (head, tail)
        return None
    if len(s) == 6 and s.isdigit():
        if s[0] == "6":
            return s + ".SH"
        if s[0] in ("4", "8", "9"):
            return s + ".BJ"
        return s + ".SZ"
    return None


def load_codes_from_csv(path):
    """从选股 CSV 提取代码集合（自适应列名）。"""
    df = pd.read_csv(path, dtype=str)
    col = None
    for c in ("code", "ts_code", "stock_code", "symbol", "股票代码"):
        if c in df.columns:
            col = c
            break
    if col is None:
        # 退化：第一列
        col = df.columns[0]
    codes = set()
    for v in df[col]:
        n = norm_code(v)
        if n:
            codes.add(n)
    return codes, len(df)


def main():
    print("=" * 70)
    print("corr_diag 独立审计  batch=%s  (独立复算，不引用 corr_diag 输出)" % BATCH)
    print("=" * 70)

    reports = {}
    for kind, _ in CASES:
        jp = os.path.join(RPT_DIR, "corr_%s_%s.json" % (kind, BATCH))
        reports[kind] = json.load(open(jp, encoding="utf-8"))

    # ---------- [A] 候选清单 ----------
    for kind, csv_path in CASES:
        if not os.path.exists(csv_path):
            check("[A] %s 选股文件存在" % kind, False, csv_path)
            continue
        codes_csv, n_rows = load_codes_from_csv(csv_path)
        codes_rep = set(reports[kind]["codes"])
        ok = codes_csv == codes_rep
        check("[A] %s 候选清单 CSV==报告 (%d 只, csv %d 行)" %
              (kind, len(codes_rep), n_rows), ok,
              "csv_only=%s rep_only=%s" %
              (sorted(codes_csv - codes_rep), sorted(codes_rep - codes_csv)))
        check("[A] %s n_candidates 字段一致" % kind,
              reports[kind]["n_candidates"] == len(codes_rep))

    # ---------- [B] 行情末日 ----------
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(MAIN_DAILY)
        mx = None
        for i in range(pf.num_row_groups):
            rg = pf.metadata.row_group(i)
            for j in range(rg.num_columns):
                nm = rg.column(j).path_in_schema
                if nm == "trade_date":
                    st = rg.column(j).statistics
                    v = st.max
                    if mx is None or v > mx:
                        mx = v
        mx_s = pd.Timestamp(mx).strftime("%Y%m%d")
        check("[B] 主库 trade_date 最大值 == 20260911", mx_s == "20260911",
              "实测 max=%s" % mx_s)
    except Exception as exc:  # noqa: BLE001
        check("[B] 主库 trade_date 最大值", False, "读取异常: %s" % exc)

    # ---------- [C] ens 60 日两两相关 ----------
    ens_codes = list(reports["ens"]["codes"])
    df = pd.read_parquet(MAIN_DAILY, columns=["pct_chg"]).reset_index()
    df = df[(df["ts_code"].isin(ens_codes)) &
            (df["trade_date"] <= pd.Timestamp("2026-09-11"))]
    piv = df.pivot_table(index="trade_date", columns="ts_code",
                         values="pct_chg", aggfunc="last").sort_index()
    piv = piv.tail(WINDOW)
    n_by_code = piv.count()
    check("[C] 每票窗口内样本 == 60", bool((n_by_code == WINDOW).all()),
          "实测=%s" % dict(n_by_code))
    corr = piv.corr()

    # 报告 21 对全表（从 md 解析，另一独立路径：不解析 JSON sections）
    md = open(os.path.join(RPT_DIR, "corr_ens_%s.md" % BATCH),
              encoding="utf-8").read()
    sec = md.split("## 高相关资产簇")[1].split("## 阈值")[0]
    rep_pairs = {}
    for line in sec.splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) == 5 and parts[0] and ".S" in parts[0] or (
                len(parts) == 5 and ".BJ" in parts[0]):
            a, b, _, r, _ = parts
            try:
                rep_pairs[(a, b)] = float(r)
            except ValueError:
                pass
    check("[C] 报告对数 == 21", len(rep_pairs) == 21,
          "实测=%d" % len(rep_pairs))
    bad = []
    for (a, b), r_rep in rep_pairs.items():
        r_mine = corr.loc[a, b]
        if abs(r_mine - r_rep) > 5e-4:
            bad.append("%s/%s rep=%.4f mine=%.4f" % (a, b, r_rep, r_mine))
    check("[C] 21 对相关系数全部复算吻合(±5e-4)", not bad,
          "; ".join(bad[:6]))
    mx_pair = None
    mx_val = -9.0
    for (a, b), r in rep_pairs.items():
        if r > mx_val:
            mx_val, mx_pair = r, (a, b)
    check("[C] max 相关对 == 002181.SZ/920237.BJ @0.4754",
          mx_pair == ("002181.SZ", "920237.BJ") and abs(mx_val - 0.4754) < 5e-4,
          "实测 %s @%.4f" % (mx_pair, mx_val))

    # ---------- [D] ens 行业映射（申万一级自建） ----------
    sw = pd.read_parquet(SW_L1).reset_index()
    ind_map = None
    for td in sorted(sw["trade_date"].unique(), reverse=True):
        cross = sw[sw["trade_date"] == td]
        valid = cross[cross["con_codes"].apply(
            lambda x: x is not None and len(x) > 0)]
        if len(valid) >= 10:
            ind_map = {}
            for _, row in valid.iterrows():
                name = row["name"]
                for c in row["con_codes"]:
                    cs = str(c)
                    n = norm_code(cs) or cs
                    ind_map.setdefault(n, name)
            print("  [D] 采用申万截面 trade_date=%s (%d 行业)" % (td, len(valid)))
            break
    check("[D] 申万有效截面找到", ind_map is not None)
    if ind_map:
        counts = {}
        miss = 0
        for c in ens_codes:
            ind = ind_map.get(c)
            if ind is None:
                miss += 1
            else:
                counts[ind] = counts.get(ind, 0) + 1
        check("[D] 覆盖 7/7（缺失 0）", miss == 0, "缺失=%d" % miss)
        check("[D] 行业数 == 6", len(counts) == 6, "实测=%s" % counts)
        top1 = max(counts.items(), key=lambda kv: kv[1])
        check("[D] 第一大行业 == 轻工制造(2只)", top1 == ("轻工制造", 2),
              "实测=%s" % (top1,))
        hhi = sum((v * v) for v in counts.values()) / float(
            len(ens_codes) ** 2)
        check("[D] HHI == 0.1837", abs(hhi - 0.1837) < 5e-4,
              "实测=%.4f" % hhi)

    # ---------- [E] JSON thresholds 与 md 阈值表 ----------
    for kind, _ in CASES:
        jp = os.path.join(RPT_DIR, "corr_%s_%s.json" % (kind, BATCH))
        mp = jp.replace(".json", ".md")
        th_json = json.load(open(jp, encoding="utf-8"))["thresholds"]
        md_txt = open(mp, encoding="utf-8").read()
        sec = md_txt.split("## 阈值")[1]
        th_md = {}
        for line in sec.splitlines():
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) == 2:
                try:
                    th_md[parts[0]] = float(parts[1])
                except ValueError:
                    pass
        ok = set(th_md) == set(th_json) and all(
            abs(float(th_json[k]) - th_md[k]) < 1e-12 for k in th_md)
        check("[E] %s 阈值 JSON==md (%d 项)" % (kind, len(th_md)), ok,
              "json_only=%s md_only=%s" %
              (sorted(set(th_json) - set(th_md)),
               sorted(set(th_md) - set(th_json))))

    print("=" * 70)
    print("[AUDIT] PASS=%d FAIL=%d" % (_NPASS[0], _NFAIL[0]))
    print("[CORR-DIAG-AUDIT-DONE]")
    return 0 if _NFAIL[0] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
