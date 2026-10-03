# -*- coding: utf-8 -*-
"""临时探针：corr_diag_io 导入冒烟 + 真实数据端到端，验证 6 处风险。"""
import io
import os
import sys
import time
import types

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUT = os.path.join(os.environ.get("TEMP", "/tmp"), "io_smoke_probe.txt")
lines = []


def w(s):
    lines.append(str(s))


def load(name):
    p = os.path.join(HERE, name)
    with io.open(p, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType(name[:-3] + "_probe")
    mod.__file__ = p
    exec(compile(src, p, "exec"), mod.__dict__)
    return mod


core = load("corr_diag_core.py")
w("[1] core loaded, THRESHOLDS keys=%d" % len(core.THRESHOLDS))
w("    STYLE_BENCHMARKS=%d BROAD_BENCHMARKS=%d"
  % (len(core.STYLE_BENCHMARKS), len(core.BROAD_BENCHMARKS)))

# 风险4: parse_compact_date 对 Timestamp / str / int / None 的行为
w("[2] parse_compact_date probes:")
for probe in ["20260930", "2026-09-30", 20260930, pd.Timestamp("2026-09-30"),
              None, "", "abc"]:
    try:
        w("    %-24r -> %r" % (probe, core.parse_compact_date(probe)))
    except Exception as e:
        w("    %-24r -> EXC %r" % (probe, e))

import corr_diag_io as cio
w("[3] cio loaded OK; default_paths:")
P = cio.default_paths()
for k in sorted(P):
    w("    %-16s exists=%s  %s" % (k, os.path.exists(P[k]), P[k]))

# 风险1: market_last_date 性能与正确性
t0 = time.time()
mld = cio.market_last_date(P["main_daily"])
w("[4] market_last_date=%s  (%.2fs)" % (mld, time.time() - t0))

# 风险4: 申万 MultiIndex 截面选取
t0 = time.time()
ind_map, m_ind = cio.load_sw_l1_industry_map(P["sw_l1"])
w("[5] sw snapshot=%s n_valid=%s n_ind_map=%d  (%.2fs)"
  % (m_ind["snap_date"], m_ind["n_valid"], len(ind_map), time.time() - t0))
w("    tried(top3)=%s" % (m_ind["tried"][:3],))
w("    status=%s errors=%s" % (m_ind["status"], m_ind["errors"][:3]))
sample = sorted(ind_map.items())[:5]
w("    sample=%s" % (sample,))

# 风险2: 成分文件探测（首次=慢，命中缓存=快）
t0 = time.time()
m1, m_i1 = cio.load_index_members(P["index_weight_dir"],
                                  [c for c, _ in core.STYLE_BENCHMARKS] +
                                  [c for c, _ in core.BROAD_BENCHMARKS])
t1 = time.time() - t0
t0 = time.time()
m2, m_i2 = cio.load_index_members(P["index_weight_dir"],
                                  [c for c, _ in core.STYLE_BENCHMARKS] +
                                  [c for c, _ in core.BROAD_BENCHMARKS])
t2 = time.time() - t0
w("[6] index_members first=%.2fs (probed=%d) cached=%.2fs (hit=%s)"
  % (t1, m_i1["n_probed"], t2, m_i2.get("cache_hit")))
w("    same_objects=%s n_idx=%d" % (m1 is m2, len(m1)))
for c in sorted(m1):
    w("      %-12s n=%-5d td=%s fams=%s"
      % (c, len(m1[c]), m_i1["per_index"][c]["max_td"],
         m_i1["per_index"][c]["families"]))
w("    cross_family=%s" % (m_i1["cross_family"],))
w("    status=%s errors=%s" % (m_i1["status"], m_i1["errors"][:3]))

# 候选文件
w("[7] candidates:")
for kind, fn in (("v13", "20260930_selection_full.csv"),
                 ("g2", "20260930_g2_top10.csv"),
                 ("ens", "20260930_g2_top10.csv")):
    path = cio.resolve_batch_path(kind, "20260930", P, fn)
    rows, m = cio.load_candidates(path)
    w("    %-4s n_rows=%d file_date=%s row_dates=%s status=%s"
      % (kind, m["n_rows"], m["file_date"],
         sorted(set(m["row_trade_dates"]))[:3], m["status"]))
    w("         codes=%s" % (m["codes"],))
    w("         ind_hit=%d/%d"
      % (len([c for c in m["codes"] if c in ind_map]), len(m["codes"])))

# 风险5: load_returns 右端截断 + 统一日期轴
allcodes = sorted(set(sum([cio.load_candidates(
    cio.resolve_batch_path(k, "20260930", P, f))[1]["codes"]
    for k, f in (("v13", "20260930_selection_full.csv"),
                 ("g2", "20260930_g2_top10.csv"),
                 ("ens", "20260930_g2_top10.csv"))], [])))
w("[8] union codes=%d" % len(allcodes))
t0 = time.time()
rets, m_ret = cio.load_returns(P["main_daily"], allcodes, window=60,
                              end_date="20260930")
w("    load_returns %.2fs window_effective=%s last=%s status=%s"
  % (time.time() - t0, m_ret.get("window_effective"), m_ret.get("last_date"),
     m_ret["status"]))
w("    n_dropped_after_end=%s latest_in_source=%s"
  % (m_ret.get("n_dropped_after_end"), m_ret.get("latest_date_in_source")))
w("    axis[:3]=%s axis[-3:]=%s len=%d"
  % (m_ret["axis"][:3], m_ret["axis"][-3:], len(m_ret["axis"])))
w("    missing=%s" % (m_ret["missing_codes"],))
lens = set(len(v) for v in rets.values())
w("    all series same length: %s -> %s" % (lens, lens == set([60])))
w("    sample rets[000001.SZ][:5]=%s" % (rets.get("000001.SZ", [])[:5],))

# 端到端
w("[9] diagnose_batch (today=20261002):")
for kind, fn in (("v13", "20260930_selection_full.csv"),
                 ("g2", "20260930_g2_top10.csv"),
                 ("ens", "20260930_g2_top10.csv")):
    t0 = time.time()
    r = cio.diagnose_batch(kind, "20260930", P, fn, today="20261002")
    w("    %-4s overall=%-5s (%.2fs) section=%s"
      % (kind, r["overall"], time.time() - t0, r.get("section_status")))
    w("         n_cand=%d io_status=%s" % (r["n_candidates"],
                                          r["io"]["io_status"]))
    for rr in r["reasons"][:6]:
        w("         - %s" % rr)

# 未来日期副本对照
w("[10] future-date clone (20261008 vs 20260930, today=20261002):")
for kind, fn in (("g2", "20261008_g2_top10.csv"),):
    r = cio.diagnose_batch(kind, "20261008", P, fn, today="20261002")
    w("     overall=%s date_status=%s"
      % (r["overall"], r.get("section_status", {}).get("date")))
    for rr in r["reasons"][:6]:
        w("     - %s" % rr)

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("WROTE", OUT)
