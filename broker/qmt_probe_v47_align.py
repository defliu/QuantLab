# coding: utf-8
"""QMT v4.7 data-side probe -- out-of-sandbox alignment checker.

Reads the [P47] JSON records printed by broker/qmt_sandbox_probe_v47_data.py
(PROBE_TAG 20261027-04) from a QMT FormulaOutput log, then diffs the raw API
returns against D:/astock (the in-repo authoritative data asset).

The probe itself must not touch files or CSV (sandbox denies file IO), so all
comparison logic lives here and runs in a normal shell.

Usage
  python broker/qmt_probe_v47_align.py
  python broker/qmt_probe_v47_align.py --client guojin --date 20261027
  python broker/qmt_probe_v47_align.py --log <path> --out <report.md>
  python broker/qmt_probe_v47_align.py --dump-json <path>   # raw parsed records

Exit code 0 when the report was produced, 2 when no probe records were found.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys
import time

PROBE_TAG = "20261027-04"
CLIENT_LOGS = {
    "guojin": r"D:\国金QMT交易端模拟\userdata\log\XtClient_FormulaOutput_%s.log",
    "qmt": r"D:\QMT交易端模拟\userdata\log\XtClient_FormulaOutput_%s.log",
}
ASTOCK_DAILY = r"D:/astock/daily/stock_daily.parquet"
ASTOCK_BASIC = r"D:/astock/daily/daily_basic.parquet"
ASTOCK_FINA = r"D:/astock/finance/fina_indicator.parquet"

DEFAULTS = {
    "log": None,
    "client": "guojin",
    "date": time.strftime("%Y%m%d"),
    "out": os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "projects", "Project_16_LightGBM股票大师", "results",
        "QMT_v47数据面对齐_%s.md" % time.strftime("%Y%m%d")),
    "dump_json": None,
}


# ---------------------------------------------------------------- log parse
def parse_log(path, tag=PROBE_TAG):
    """Return (meta, records) for the LAST complete run of `tag` in the log."""
    if not os.path.exists(path):
        raise SystemExit("[align] log not found: %s" % path)
    text = io.open(path, "r", encoding="gbk", errors="replace").read()
    recs = []
    for line in text.split("\n"):
        i = line.find("[P47] ")
        if i < 0:
            continue
        payload = line[i + 6:].strip()
        try:
            rec = json.loads(payload)
        except ValueError:
            continue
        recs.append(rec)
    # split into runs; keep the last one that has a 'begin' meta for our tag
    runs = []
    cur = None
    for rec in recs:
        if rec.get("k") == "meta" and rec.get("phase") == "begin":
            if rec.get("tag") != tag:
                cur = None
                continue
            cur = {"meta": rec, "recs": []}
            runs.append(cur)
        elif cur is not None:
            cur["recs"].append(rec)
    if not runs:
        return None, []
    return runs[-1]["meta"], runs[-1]["recs"]


def index_records(recs):
    idx = {"inst": {}, "cap": {}, "tshare": {}, "fin": {},
           "sector": {}, "fdata": {}, "mdx_none": {}, "mdx_front": {},
           "turnover": None, "turnover_single": {}}
    for rec in recs:
        if rec.get("k") != "call":
            continue
        api = rec.get("api")
        code = rec.get("code")
        if api == "get_instrument_detail":
            idx["inst"][code] = rec
        elif api == "get_float_caps":
            idx["cap"][code] = rec
        elif api == "get_total_share":
            idx["tshare"][code] = rec
        elif api == "get_finance":
            idx["fin"][code] = rec
        elif api == "get_stock_list_in_sector":
            idx["sector"][rec.get("sector")] = rec
        elif api == "get_financial_data":
            key = rec.get("field") or rec.get("mode") or "?"
            idx["fdata"][key] = rec
        elif api == "mdx_none":
            idx["mdx_none"][code] = rec
        elif api == "mdx_front_ratio":
            idx["mdx_front"][code] = rec
        elif api == "get_turnover_rate":
            idx["turnover"] = rec
        elif api == "get_turn_over_rate":
            idx["turnover_single"][code] = rec
    return idx


# ---------------------------------------------------------------- astock
BASIC_COLS = ["ts_code", "trade_date", "float_share", "total_share",
              "circ_mv", "turnover_rate", "pe_ttm", "pb", "dv_ttm"]


def _read_subset(path, cols, codes):
    """Read only the sample codes. pyarrow filters cut the 14M-row scan."""
    import pandas as pd
    try:
        df = pd.read_parquet(path, columns=cols,
                             filters=[("ts_code", "in", list(codes))])
    except Exception:
        df = pd.read_parquet(path, columns=cols)
    # astock parquet metadata declares ts_code/trade_date as the pandas
    # index, so a `columns=` read returns them as the index, not columns
    # (pandas 3.x). Restore them explicitly.
    if ("ts_code" not in df.columns
            or ("trade_date" in cols and "trade_date" not in df.columns)):
        df = df.reset_index()
    if "ts_code" not in df.columns:
        df = pd.read_parquet(path, columns=cols)
        df = df.reset_index()
    if "ts_code" not in df.columns:
        raise SystemExit("[align] cannot recover ts_code from %s" % path)
    return df[df["ts_code"].isin(codes)]


def _load_astock(codes, date):
    import pandas as pd
    out = {}
    try:
        daily = _read_subset(ASTOCK_DAILY, ["ts_code", "trade_date",
                                            "close", "adj_factor"], codes)
    except Exception as exc:
        print("[align] WARN daily read failed: %s" % exc)
        daily = pd.DataFrame(columns=["ts_code", "trade_date", "close",
                                      "adj_factor"])
    if len(daily):
        daily["trade_date"] = (daily["trade_date"].astype(str)
                               .str.replace("-", "").str[:8])
        daily = daily[daily["trade_date"] <= str(date)]
        for code, g in daily.groupby("ts_code"):
            g = g.sort_values("trade_date")
            out.setdefault(code, {})["daily"] = g.reset_index(drop=True)
    try:
        basic = _read_subset(ASTOCK_BASIC, BASIC_COLS, codes)
    except Exception as exc:
        print("[align] WARN daily_basic read failed: %s" % exc)
        basic = pd.DataFrame(columns=BASIC_COLS)
    if len(basic) and "ts_code" in basic.columns:
        basic["trade_date"] = (basic["trade_date"].astype(str)
                               .str.replace("-", "").str[:8])
        basic = basic[basic["trade_date"] <= str(date)]
        for code, g in basic.groupby("ts_code"):
            g = g.sort_values("trade_date")
            out.setdefault(code, {})["basic"] = g.iloc[-1].to_dict()
    return out


_FINA_CACHE = {}


def _load_fina(code):
    import pandas as pd
    if "df" not in _FINA_CACHE:
        try:
            _FINA_CACHE["df"] = pd.read_parquet(
                ASTOCK_FINA, columns=["ts_code", "end_date", "ann_date",
                                      "roe", "profit_dedt"])
        except Exception:
            _FINA_CACHE["df"] = None
    df = _FINA_CACHE["df"]
    if df is None:
        return None
    g = df[df["ts_code"] == code].sort_values("end_date")
    if not len(g):
        return None
    return g.iloc[-1].to_dict()


# ---------------------------------------------------------------- helpers
def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _get_ci(d, name):
    """Case-insensitive dict lookup."""
    if not isinstance(d, dict):
        return None
    for k in d:
        if str(k).lower() == name.lower():
            return d[k]
    return None


def _rel(a, b):
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b)


def _implied(ratio):
    if ratio is None or ratio <= 0:
        return "?"
    cands = [("1e4 (yuan)", 1e4), ("1 (10k yuan)", 1.0),
             ("1e-4 (100M yuan)", 1e-4), ("1e-8", 1e-8)]
    best = min(cands, key=lambda kv: abs((ratio / kv[1]) - 1.0)
               if ratio > 0 else 9e9)
    return best[0]


def _grade(err, exact=1e-4, near=1e-2):
    if err is None:
        return "无法判定"
    if err <= exact:
        return "一致"
    if err <= near:
        return "近似"
    return "不一致"


def _fmt(v, nd=4):
    if v is None:
        return "-"
    if isinstance(v, float):
        if v == 0:
            return "0"
        if abs(v) >= 1e6 or abs(v) < 1e-3:
            return "%.4e" % v
        return ("%%.%df" % nd) % v
    return str(v)


# ---------------------------------------------------------------- report
def build_report(meta, idx, date):
    lines = []
    A = lines.append
    A("# QMT v4.7 数据面探针 · 沙箱外对齐报告")
    A("")
    A("- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    A("- 探针：`broker/qmt_sandbox_probe_v47_data.py`（PROBE_TAG `%s`）" % PROBE_TAG)
    A("- 探针运行元信息：py=%s ctx=%s ts=%s sample_n=%s" % (
        meta.get("py"), meta.get("ctx"), meta.get("ts"), meta.get("sample_n")))
    A("- 样本：%s" % ", ".join(meta.get("sample") or []))
    A("- 对照基准：`D:/astock`（daily / daily_basic / fina_indicator），"
      "取 `trade_date <= %s` 的最后一个交易日" % date)
    A("")
    A("> 说明：本报告由脚本自动生成，数字来自探针原始返回；"
      "「结论」小节需要人工按业务口径复核后再定稿。")
    A("")

    codes = list(meta.get("sample") or [])
    aks = _load_astock(codes, date)

    # ---- 1. API availability / coarse status
    A("## 1. 概览")
    A("")
    A("| API | 调用数 | OK | FAIL | 备注 |")
    A("|---|---|---|---|---|")
    for api, bucket in (("get_instrument_detail", idx["inst"]),
                        ("get_float_caps", idx["cap"]),
                        ("get_total_share", idx["tshare"]),
                        ("get_finance", idx["fin"]),
                        ("get_stock_list_in_sector", idx["sector"]),
                        ("get_financial_data", idx["fdata"]),
                        ("get_market_data_ex(none)", idx["mdx_none"]),
                        ("get_market_data_ex(front_ratio)", idx["mdx_front"])):
        ok = sum(1 for r in bucket.values() if r.get("ok"))
        fail = len(bucket) - ok
        errs = sorted(set(str(r.get("err"))[:60] for r in bucket.values()
                          if not r.get("ok")))
        note = "; ".join(errs[:2])
        A("| %s | %d | %d | %d | %s |" % (api, len(bucket), ok, fail, note))
    A("")

    # ---- 2. shares / caps
    A("## 2. 股本与市值（FloatVolume / TotalVolume / get_float_caps / get_total_share）")
    A("")
    A("| code | FloatVolume/1e4 (万股) | astock float_share | rel_err | 判定 | "
      "get_float_caps | astock circ_mv(万元) | ratio | 推断单位 |")
    A("|---|---|---|---|---|---|---|---|---|")
    share_rows = []
    for code in codes:
        r = idx["inst"].get(code) or {}
        v = r.get("v") if isinstance(r.get("v"), dict) else {}
        fv = _num(_get_ci(v, "FloatVolume"))
        tv = _num(_get_ci(v, "TotalVolume"))
        basic = (aks.get(code) or {}).get("basic") or {}
        fs = _num(basic.get("float_share"))
        ts = _num(basic.get("total_share"))
        fv_wan = fv / 1e4 if fv is not None else None
        tv_wan = tv / 1e4 if tv is not None else None
        err_fs = _rel(fv_wan, fs)
        cap_rec = idx["cap"].get(code) or {}
        cap = _num(cap_rec.get("v"))
        circ = _num(basic.get("circ_mv"))
        ratio = (cap / circ) if (cap is not None and circ) else None
        A("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            code, _fmt(fv_wan), _fmt(fs), _fmt(err_fs, 6), _grade(err_fs),
            _fmt(cap), _fmt(circ), _fmt(ratio, 2), _implied(ratio)))
        share_rows.append({
            "code": code, "fv_wan": fv_wan, "fs": fs, "err_fs": err_fs,
            "tv_wan": tv_wan, "ts": ts, "err_ts": _rel(tv_wan, ts),
            "cap": cap, "circ": circ, "ratio": ratio,
            "tshare_raw": _num((idx["tshare"].get(code) or {}).get("v")),
        })
    A("")
    A("**TotalVolume 校验（同表另一列）**")
    A("")
    A("| code | TotalVolume/1e4 | astock total_share | rel_err | 判定 | get_total_share 原值 |")
    A("|---|---|---|---|---|---|")
    for row in share_rows:
        A("| %s | %s | %s | %s | %s | %s |" % (
            row["code"], _fmt(row["tv_wan"]), _fmt(row["ts"]),
            _fmt(row["err_ts"], 6), _grade(row["err_ts"]),
            _fmt(row["tshare_raw"])))
    A("")

    # ---- 3. turnover
    A("## 3. 换手率（get_turnover_rate / get_turn_over_rate）")
    A("")
    tr = idx["turnover"] or {}
    A("- 批量调用：ok=%s ms=%s" % (tr.get("ok"), tr.get("ms")))
    if tr.get("ok"):
        v = tr.get("v")
        if isinstance(v, dict) and v.get("__df__"):
            A("- 返回 DataFrame shape=%s，列=%s，索引前 5=%s" % (
                v.get("shape"), v.get("columns"), (v.get("index") or [])[:5]))
            A("- 前 3 行：")
            for row in (v.get("rows") or [])[:3]:
                A("  - `%s`" % json.dumps(row, ensure_ascii=False))
        else:
            A("- 返回非 DataFrame：`%s`" % json.dumps(v, ensure_ascii=False)[:400])
    else:
        A("- 错误：%s" % tr.get("err"))
    A("")
    A("单票 get_turn_over_rate（对照 astock turnover_rate，单位需看绝对值量级）：")
    A("")
    A("| code | QMT | astock turnover_rate(%%) | ratio |")
    A("|---|---|---|---|")
    for code, rec in sorted(idx["turnover_single"].items()):
        q = _num(rec.get("v")) if rec.get("ok") else None
        basic = (aks.get(code) or {}).get("basic") or {}
        a = _num(basic.get("turnover_rate"))
        A("| %s | %s | %s | %s |" % (code, _fmt(q) if rec.get("ok") else "ERR",
                                     _fmt(a), _fmt((q / a) if (q is not None and a) else None, 3)))
    A("")

    # ---- 4. finance
    A("## 4. 财务（get_finance / get_financial_data）")
    A("")
    A("### 4.1 get_finance 键清单")
    A("")
    seen = {}
    for code in codes:
        rec = idx["fin"].get(code) or {}
        if rec.get("ok") and isinstance(rec.get("v"), dict):
            for k in rec["v"]:
                seen[k] = seen.get(k, 0) + 1
    if seen:
        A("共 %d 个键（出现次数）：" % len(seen))
        A("")
        A("```")
        for k in sorted(seen):
            A("%-40s %d" % (k, seen[k]))
        A("```")
    else:
        errs = sorted(set(str((idx["fin"].get(c) or {}).get("err"))[:80]
                          for c in codes if not (idx["fin"].get(c) or {}).get("ok")))
        A("全部调用失败/无字段。错误样例：%s" % "; ".join(errs[:3]))
    A("")
    A("### 4.2 get_finance 字段 vs astock（按名称模糊匹配）")
    A("")
    A("| code | 匹配字段 | QMT 值 | astock 值 | rel_err |")
    A("|---|---|---|---|---|")
    matched_any = False
    for code in codes[:8]:
        rec = idx["fin"].get(code) or {}
        v = rec.get("v") if rec.get("ok") and isinstance(rec.get("v"), dict) else {}
        fina = _load_fina(code)
        for key in ("roe", "pe", "pe_ttm", "pb"):
            q = _num(_get_ci(v, key))
            if q is None:
                continue
            a = None
            if fina and key in fina:
                a = _num(fina.get(key))
            if a is None:
                basic = (aks.get(code) or {}).get("basic") or {}
                a = _num(basic.get(key))
            matched_any = True
            A("| %s | %s | %s | %s | %s |" % (
                code, key, _fmt(q), _fmt(a), _fmt(_rel(q, a), 6)))
    if not matched_any:
        A("| - | 未匹配到 roe/pe/pb 类字段 | - | - | - |")
    A("")
    A("### 4.3 get_financial_data 字段探测")
    A("")
    A("| 字段 | ok | 形状/返回 | 错误 |")
    A("|---|---|---|---|")
    for fld, rec in sorted(idx["fdata"].items()):
        v = rec.get("v")
        shape = "-"
        if isinstance(v, dict) and v.get("__df__"):
            shape = "df shape=%s cols=%s" % (v.get("shape"), v.get("columns"))
        elif isinstance(v, dict):
            shape = json.dumps(v, ensure_ascii=False)[:160]
        else:
            shape = str(v)[:160]
        A("| %s | %s | %s | %s |" % (
            fld, rec.get("ok"), shape, rec.get("err") or "-"))
    A("")

    # ---- 5. sectors
    A("## 5. 板块（get_stock_list_in_sector）")
    A("")
    A("| alias | ok | n | 样本命中 | 未命中样本 | head |")
    A("|---|---|---|---|---|---|")
    for alias in sorted(idx["sector"]):
        rec = idx["sector"][alias]
        v = rec.get("v") or {}
        A("| %s | %s | %s | %s | %s | %s |" % (
            alias, rec.get("ok"), v.get("n"),
            v.get("sample_hit"),
            ",".join(v.get("sample_miss") or []) or "-",
            ",".join(v.get("head") or []) or "-"))
    A("")

    # ---- 6. adjustment
    A("## 6. 复权口径（dividend_type=none vs front_ratio）")
    A("")
    A("front_ratio 的前复权价 = 原始价 × adj_t / adj_latest；"
      "astock 的 `adj_factor` 是后复权因子，故可对比两者的**比值**。")
    A("")
    A("| code | rows(none/front) | front/raw 比值(末 3 日) | astock adj_t/adj_last | 最大偏差 | 判定 |")
    A("|---|---|---|---|---|---|")
    for code in codes:
        rn = idx["mdx_none"].get(code) or {}
        rf = idx["mdx_front"].get(code) or {}
        vn = rn.get("v") if isinstance(rn.get("v"), dict) else None
        vf = rf.get("v") if isinstance(rf.get("v"), dict) else None
        if not vn or not vf:
            A("| %s | - | - | - | - | 取数失败 |" % code)
            continue
        cn = [x for x in (vn.get("close") or [])]
        cf = [x for x in (vf.get("close") or [])]
        ratios = []
        for a, b in zip(cn, cf):
            if a and b:
                ratios.append(float(b) / float(a))
        daily = (aks.get(code) or {}).get("daily")
        adj_ratio = None
        if daily is not None and len(daily):
            g = daily.tail(len(ratios))
            aa = [float(x) for x in g["adj_factor"]]
            if aa and aa[-1]:
                adj_ratio = [x / aa[-1] for x in aa]
        dev = None
        if adj_ratio is not None and len(adj_ratio) == len(ratios):
            dev = max(abs(x - y) for x, y in zip(ratios, adj_ratio))
        A("| %s | %d/%d | %s | %s | %s | %s |" % (
            code, len(cn), len(cf),
            ",".join("%.6f" % x for x in ratios),
            ",".join("%.6f" % x for x in adj_ratio) if adj_ratio else "-",
            _fmt(dev, 8),
            _grade(dev, 1e-4, 1e-2) if dev is not None else "无法判定"))
    A("")

    # ---- 7. verdict checklist
    A("## 7. 待人工定稿的判定项")
    A("")
    A("按工单 T1 口径，逐项标注「可用 / 口径一致 / 缺失需外挂」，"
      "并区分「沙箱拦截」「需下载财务数据包」「字段名/参数错」三种失败原因：")
    A("")
    for item in (
        "get_instrument_detail → float_share / total_share（P16 的 float_share 来源）",
        "get_float_caps → 流通市值（单位需按上表 ratio 确认）",
        "get_turnover_rate → 换手率（历史批量是否可用）",
        "get_finance → 是否含 PE/PB/ROE 等 P16 所需字段",
        "get_financial_data → 字段名是否有效、是否需下载财务数据包",
        "get_stock_list_in_sector → 板块成分是否可用于 ST/行业/宇宙过滤",
        "get_market_data_ex(front_ratio) → 复权口径是否与 astock adj_factor 一致",
    ):
        A("- [ ] %s" % item)
    A("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--client", default=DEFAULTS["client"], choices=sorted(CLIENT_LOGS))
    ap.add_argument("--date", default=DEFAULTS["date"])
    ap.add_argument("--out", default=DEFAULTS["out"])
    ap.add_argument("--dump-json", default=None)
    args = ap.parse_args()

    log = args.log or (CLIENT_LOGS[args.client] % args.date)
    meta, recs = parse_log(log)
    if not meta:
        print("[align] no [P47] run with tag %s in %s" % (PROBE_TAG, log))
        return 2
    print("[align] run ts=%s seg=%s records=%d" % (
        meta.get("ts"), meta.get("seg"), len(recs)))
    idx = index_records(recs)
    if args.dump_json:
        with io.open(args.dump_json, "w", encoding="utf-8") as f:
            f.write(json.dumps({"meta": meta, "records": recs},
                               ensure_ascii=False, indent=1))
        print("[align] dumped raw records -> %s" % args.dump_json)
    report = build_report(meta, idx, args.date)
    out = args.out
    if out:
        d = os.path.dirname(out)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(report)
        print("[align] report -> %s" % out)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
