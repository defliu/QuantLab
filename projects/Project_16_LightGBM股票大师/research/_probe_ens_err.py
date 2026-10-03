# -*- coding: utf-8 -*-
"""临时探针：定位 g2_ens 真实批次 diagnose_batch 的 KeyError 根因。"""
import io
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = HERE
if RESEARCH not in sys.path:
    sys.path.insert(0, RESEARCH)

import corr_diag_io as cio  # noqa: E402

print("--- default_paths ---")
try:
    p = cio.default_paths()
    for k in sorted(p):
        print("  %s = %s" % (k, p[k]))
except Exception:
    traceback.print_exc()

print("--- resolve_batch_path(g2_ens, 20260930) ---")
try:
    bp = cio.resolve_batch_path("g2_ens", "20260930")
    print("  ->", bp)
except Exception:
    traceback.print_exc()

print("--- diagnose_batch(g2_ens, 20260930) ---")
try:
    r = cio.diagnose_batch("g2_ens", "20260930", today="20260930")
    print("  overall =", r.get("overall"))
    print("  codes =", r.get("codes"))
except Exception:
    traceback.print_exc()

print("--- load_batch_inputs(g2_ens, 20260930) ---")
try:
    inp = cio.load_batch_inputs("g2_ens", "20260930")
    print("  keys =", sorted(inp))
    print("  io_status =", inp.get("io_status"))
    print("  errors =", inp.get("errors"))
    print("  path =", inp.get("path"))
except Exception:
    traceback.print_exc()

print("--- diagnose_batch(g2, 20260930) 对照 ---")
try:
    r2 = cio.diagnose_batch("g2", "20260930", today="20260930")
    print("  overall =", r2.get("overall"))
    print("  codes =", r2.get("codes"))
except Exception:
    traceback.print_exc()