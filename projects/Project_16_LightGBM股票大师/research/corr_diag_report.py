# -*- coding: utf-8 -*-
"""相关性诊断报告输出：JSON（机读）+ Markdown（人读）。

设计约束（与 core/io 层一致）：
1. **不取系统时间**：generated_at 必须由调用方显式注入，否则同一批次
   两次运行产物不同，无法做回归 diff。
2. **确定性排序**：所有 dict 在序列化前按键排序，所有 list 按显式 key
   排序。同一输入必须产出逐字节相同的报告。
3. **原子写**：先写临时文件再 os.replace。报告会被下游 nightly 任务
   读取，半截文件会导致读到空 JSON。
4. **不吞异常**：写失败必须抛出，不能静默返回 False 让调用方以为成功。
"""
import io
import json
import os
import tempfile

import corr_diag_core as core

SCHEMA_VERSION = "corr_diag_report/v1"

# 报告里各维度的展示顺序（固定，不随 dict 顺序漂移）
SECTION_ORDER = ["industry", "style", "index", "correlation"]
SECTION_TITLE = {
    "date": "日期口径",
    "industry": "行业集中度",
    "style": "风格集中度",
    "index": "宽基成分重叠",
    "correlation": "高相关资产簇",
}

_STATUS_ICON = {
    core.STATUS_OK: "OK",
    core.STATUS_WARN: "WARN",
    core.STATUS_FAIL: "FAIL",
    core.STATUS_SKIP: "SKIP",
}


# ---------------------------------------------------------------------------
# 规范化：把 core/io 的输出整理成可稳定序列化的结构
# ---------------------------------------------------------------------------

def _sorted_deep(obj):
    """递归把 dict 的键排序，set 转 sorted(list)。"""
    if isinstance(obj, dict):
        return dict((k, _sorted_deep(obj[k])) for k in sorted(obj))
    if isinstance(obj, (set, frozenset)):
        return sorted(_sorted_deep(x) for x in obj)
    if isinstance(obj, (list, tuple)):
        return [_sorted_deep(x) for x in obj]
    return obj


def _quality_rows(sec):
    """把 correlation.quality 摊平成稳定排序的表格行。"""
    rows = []
    for q in (sec.get("quality") or []):
        rows.append({
            "code": q.get("code"),
            "n": q.get("n"),
            "zero_ratio": q.get("zero_ratio"),
            "std": q.get("std"),
            "passed": bool(q.get("passed")),
            "reason": q.get("reason") or "",
        })
    rows.sort(key=lambda r: (r["passed"], str(r["code"])))
    return rows


def _pair_rows(sec):
    """高相关对按相关度降序、代码升序排（相关度相同时顺序才稳定）。"""
    rows = []
    for p in (sec.get("pairs") or []):
        rows.append({
            "a": p.get("a"),
            "b": p.get("b"),
            "n": p.get("n"),
            "corr": p.get("corr"),
            "high": bool(p.get("high")),
            "note": p.get("note") or "",
        })
    rows.sort(key=lambda r: (
        -(r["corr"] if r["corr"] is not None else -999.0),
        str(r["a"]), str(r["b"])))
    return rows


def build_payload(result, generated_at, kind=None, batch_date=None):
    """把 core.diagnose 的返回值整理成报告 payload（含确定性排序）。"""
    if generated_at is None:
        raise ValueError("generated_at 必须显式注入，不允许在模块内取系统时间")
    if not isinstance(result, dict):
        raise TypeError("result 必须是 core.diagnose 的 dict 返回值")

    sections = result.get("sections") or {}
    io_info = _sorted_deep(result.get("io") or {})

    out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "kind": kind,
        "batch_date": batch_date,
        "overall": result.get("overall"),
        "n_candidates": result.get("n_candidates"),
        "codes": sorted(str(c) for c in (result.get("codes") or [])),
        "thresholds": _sorted_deep(result.get("thresholds") or {}),
        "section_status": _sorted_deep(result.get("section_status") or {}),
        "reasons": list(result.get("reasons") or []),
        "io": io_info,
        "io_reasons": list(result.get("io_reasons") or []),
        "sections": {},
    }

    if result.get("date"):
        out["sections"]["date"] = {
            "status": result["date"].get("status"),
            "reasons": list(result["date"].get("reasons") or []),
            "stale": result["date"].get("stale"),
        }
        # 字段名必须与 core.resolve_batch_dates 的输出一致：
        # row_trade_date（权威快照日） / row_trade_dates_all（全部去重列表）。
        # 写成 row_trade_dates（复数）会静默取不到值，日期表永远少一行。
        for k in ("file_date", "row_trade_date", "row_trade_dates_all",
                  "market_last_date", "today", "signal_date"):
            if k in result["date"]:
                out["sections"]["date"][k] = _sorted_deep(result["date"][k])

    ind = sections.get("industry")
    if ind is not None:
        out["sections"]["industry"] = {
            "status": ind.get("status"),
            "reasons": list(ind.get("reasons") or []),
            "n_mapped": ind.get("n_mapped"),
            "n_missing": ind.get("n_missing"),
            "missing_ratio": ind.get("missing_ratio"),
            "missing_codes": list(ind.get("missing_codes") or []),
            "n_industries": ind.get("n_industries"),
            "top1_industry": ind.get("top1_industry"),
            "top1_ratio": ind.get("top1_ratio"),
            "hhi": ind.get("hhi"),
            "distribution": _sorted_deep(ind.get("distribution") or {}),
        }

    sty = sections.get("style")
    if sty is not None:
        out["sections"]["style"] = {
            "status": sty.get("status"),
            "reasons": list(sty.get("reasons") or []),
            "n_in_any_style": sty.get("n_in_any_style"),
            "n_no_style": sty.get("n_no_style"),
            "no_style_codes": list(sty.get("no_style_codes") or []),
            "n_buckets": sty.get("n_buckets"),
            "bucket_top1": sty.get("bucket_top1"),
            "bucket_top1_ratio": sty.get("bucket_top1_ratio"),
            "bucket_counts": _sorted_deep(sty.get("bucket_counts") or {}),
        }

    idx = sections.get("index")
    if idx is not None:
        out["sections"]["index"] = {
            "status": idx.get("status"),
            "reasons": list(idx.get("reasons") or []),
            "n_indexes": idx.get("n_indexes"),
            "top1_index": idx.get("top1_index"),
            "top1_index_name": idx.get("top1_index_name"),
            "top1_ratio": idx.get("top1_ratio"),
            "coverage": _sorted_deep(idx.get("coverage") or {}),
        }

    cor = sections.get("correlation")
    if cor is not None:
        out["sections"]["correlation"] = {
            "status": cor.get("status"),
            "reasons": list(cor.get("reasons") or []),
            "window": cor.get("window"),
            "n_input": cor.get("n_input"),
            "n_passed": cor.get("n_passed"),
            "n_rejected": cor.get("n_rejected"),
            "n_clusters": cor.get("n_clusters"),
            "largest_cluster": cor.get("largest_cluster"),
            "max_corr": cor.get("max_corr"),
            "mean_corr": cor.get("mean_corr"),
            "clusters": [sorted(c) for c in (cor.get("clusters") or [])],
            "quality": _quality_rows(cor),
            "pairs": _pair_rows(cor),
        }

    # 固定键序，便于人工 diff
    ordered_keys = (["date"] + SECTION_ORDER)
    out["sections"] = dict(
        (k, out["sections"][k]) for k in ordered_keys if k in out["sections"])
    return out


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_markdown(payload):
    """渲染人读 Markdown。数值缺失一律显式写 NA，不留空让人误读为 0。"""
    L = []
    A = L.append

    def _pct(x):
        return "NA" if x is None else "%.1f%%" % (float(x) * 100.0)

    def _num(x, nd=4):
        return "NA" if x is None else ("%." + str(nd) + "f") % float(x)

    A("# 同批次选股相关性诊断报告")
    A("")
    A("- schema: `%s`" % payload.get("schema_version"))
    A("- 生成时间: %s（调用方注入）" % payload.get("generated_at"))
    A("- 策略: %s / 批次: %s" % (payload.get("kind"), payload.get("batch_date")))
    A("- 结论: **%s**" % _STATUS_ICON.get(payload.get("overall"),
                                          str(payload.get("overall"))))
    A("- 候选数: %s" % payload.get("n_candidates"))
    A("")

    codes = payload.get("codes") or []
    A("## 候选清单")
    A("")
    A("`%s`" % "`, `".join(codes) if codes else "（空）")
    A("")

    sec_st = payload.get("section_status") or {}
    A("## 分维度状态")
    A("")
    A("| 维度 | 状态 |")
    A("| --- | --- |")
    # SECTION_ORDER 是 list，这里必须用 list 相加；
    # 写成 ("date",) + SECTION_ORDER 会抛 TypeError（tuple 不能加 list）。
    all_keys = ["date"] + SECTION_ORDER
    for key in all_keys:
        if key in payload.get("sections") or key in sec_st:
            A("| %s | %s |" % (SECTION_TITLE.get(key, key),
                               _STATUS_ICON.get(sec_st.get(key), "-")))
    A("")

    A("## 告警与原因")
    A("")
    reasons = list(payload.get("reasons") or [])
    if reasons:
        for r in reasons:
            A("- %s" % r)
    else:
        A("- 无")
    A("")

    io_info = payload.get("io") or {}
    A("## 数据读取")
    A("")
    A("- 来源: `%s`" % io_info.get("path"))
    A("- 候选行数: %s" % io_info.get("n_rows"))
    A("- IO 状态: %s" % _STATUS_ICON.get(io_info.get("io_status"),
                                          str(io_info.get("io_status"))))
    io_reasons = list(payload.get("io_reasons") or [])
    if io_reasons:
        A("- IO 告警:")
        for e in io_reasons:
            A("  - %s" % e)
    A("")

    secs = payload.get("sections") or {}

    date_sec = secs.get("date")
    if date_sec:
        A("## %s" % SECTION_TITLE["date"])
        A("")
        A("| 字段 | 值 |")
        A("| --- | --- |")
        for k in ("file_date", "signal_date", "row_trade_date",
                  "market_last_date", "today", "stale"):
            if k in date_sec:
                A("| %s | %s |" % (k, date_sec[k]))
        rtd_all = date_sec.get("row_trade_dates_all")
        if isinstance(rtd_all, (list, tuple)) and rtd_all:
            A("| row_trade_dates_all | %s |"
              % ", ".join(str(x) for x in rtd_all))
        A("")

    ind = secs.get("industry")
    if ind:
        A("## %s" % SECTION_TITLE["industry"])
        A("")
        A("- 状态: %s" % _STATUS_ICON.get(ind.get("status"), "-"))
        A("- 覆盖: %s/%s（缺失 %s）" % (ind.get("n_mapped"),
                                       payload.get("n_candidates"),
                                       _pct(ind.get("missing_ratio"))))
        A("- 行业数: %s" % ind.get("n_industries"))
        A("- 第一大行业: %s（%s）" % (ind.get("top1_industry"),
                                     _pct(ind.get("top1_ratio"))))
        A("- HHI: %s" % _num(ind.get("hhi")))
        dist = ind.get("distribution") or {}
        if dist:
            A("")
            A("| 行业 | 只数 |")
            A("| --- | --- |")
            for name, cnt in dist.items():
                A("| %s | %s |" % (name, cnt))
        A("")

    sty = secs.get("style")
    if sty:
        A("## %s" % SECTION_TITLE["style"])
        A("")
        A("- 状态: %s" % _STATUS_ICON.get(sty.get("status"), "-"))
        A("- 命中风格基准: %s 只，未命中 %s 只" % (sty.get("n_in_any_style"),
                                                sty.get("n_no_style")))
        A("- 有效风格桶: %s" % sty.get("n_buckets"))
        A("- 第一大风格桶: %s（%s）" % (sty.get("bucket_top1"),
                                       _pct(sty.get("bucket_top1_ratio"))))
        bc = sty.get("bucket_counts") or {}
        if bc:
            A("")
            A("| 风格桶 | 只数 |")
            A("| --- | --- |")
            for k, v in bc.items():
                A("| %s | %s |" % (k, v))
        A("")

    idx = secs.get("index")
    if idx:
        A("## %s" % SECTION_TITLE["index"])
        A("")
        A("- 状态: %s" % _STATUS_ICON.get(idx.get("status"), "-"))
        A("- 命中宽基数: %s" % idx.get("n_indexes"))
        A("- 第一大宽基: %s(%s)（%s）" % (idx.get("top1_index"),
                                          idx.get("top1_index_name"),
                                          _pct(idx.get("top1_ratio"))))
        cov = idx.get("coverage") or {}
        if cov:
            A("")
            A("| 宽基 | 只数 | 占比 |")
            A("| --- | --- | --- |")
            for code, v in cov.items():
                A("| %s(%s) | %s | %s |" % (code, v.get("name"),
                                            v.get("n"), _pct(v.get("ratio"))))
        A("")

    cor = secs.get("correlation")
    if cor:
        A("## %s" % SECTION_TITLE["correlation"])
        A("")
        A("- 状态: %s" % _STATUS_ICON.get(cor.get("status"), "-"))
        A("- 窗口: %s 交易日；输入 %s，通过筛查 %s，剔除 %s"
          % (cor.get("window"), cor.get("n_input"), cor.get("n_passed"),
             cor.get("n_rejected")))
        A("- 簇数: %s；最大簇: %s" % (cor.get("n_clusters"),
                                      cor.get("largest_cluster")))
        A("- 相关系数: max %s / mean %s" % (_num(cor.get("max_corr"), 4),
                                             _num(cor.get("mean_corr"), 4)))
        clusters = cor.get("clusters") or []
        if clusters:
            A("")
            A("| 簇 | 成分 |")
            A("| --- | --- |")
            for i, cl in enumerate(clusters, 1):
                A("| C%d | %s |" % (i, ", ".join(cl)))
        quality = cor.get("quality") or []
        if quality:
            A("")
            A("| 代码 | 样本 | 零收益占比 | 标准差 | 筛查 | 原因 |")
            A("| --- | --- | --- | --- | --- | --- |")
            for q in quality:
                A("| %s | %s | %s | %s | %s | %s |" % (
                    q.get("code"), q.get("n"), _pct(q.get("zero_ratio")),
                    q.get("std"), "PASS" if q.get("passed") else "REJECT",
                    q.get("reason") or ""))
        pairs = cor.get("pairs") or []
        if pairs:
            A("")
            A("| A | B | 样本 | 相关系数 | 高相关 |")
            A("| --- | --- | --- | --- | --- |")
            for p in pairs:
                A("| %s | %s | %s | %s | %s |" % (
                    p.get("a"), p.get("b"), p.get("n"),
                    _num(p.get("corr"), 4), "Y" if p.get("high") else ""))
        A("")

    A("## 阈值")
    A("")
    A("| 阈值 | 值 |")
    A("| --- | --- |")
    for k, v in (payload.get("thresholds") or {}).items():
        A("| %s | %s |" % (k, v))
    A("")
    A("> 阈值仅供人工审核参考，尚未经真实批次与历史样本校准，"
      "**不构成生产交易闸门**。")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------

def atomic_write(path, text):
    """先写同目录临时文件再 os.replace，保证读者永远看到完整内容。"""
    if not path:
        raise ValueError("path 不能为空")
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    fd, tmp = tempfile.mkstemp(prefix=".corr_diag_", suffix=".tmp", dir=d or None)
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def dump_json(payload):
    """确定性 JSON 文本：键排序、无空格抖动、保留中文。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      indent=2, separators=(",", ": ")) + "\n"


def write_reports(out_dir, stem, result, generated_at, kind=None,
                  batch_date=None):
    """同时落 JSON + Markdown，返回 (json_path, md_path)。"""
    payload = build_payload(result, generated_at=generated_at, kind=kind,
                            batch_date=batch_date)
    json_path = os.path.join(out_dir, "%s.json" % stem)
    md_path = os.path.join(out_dir, "%s.md" % stem)
    atomic_write(json_path, dump_json(payload))
    atomic_write(md_path, render_markdown(payload))
    return json_path, md_path
