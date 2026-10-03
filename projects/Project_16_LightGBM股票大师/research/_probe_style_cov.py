# coding=gbk
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import corr_diag_core as core
import corr_diag_io as cio


def main():
    p = cio.default_paths()
    print("[PATHS]")
    for k in sorted(p):
        v = p[k]
        print("  %-20s exists=%s %s"
              % (k, os.path.exists(v) if v else False, v))

    print("")
    print("[DIR LIST] %s" % p.get("index_weight_dir"))
    d = p.get("index_weight_dir")
    if d and os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            print("  %s" % name)

    style_codes = [c for c, _ in core.STYLE_BENCHMARKS]
    index_codes = [c for c, _ in core.BROAD_BENCHMARKS]
    allx, meta = cio.load_index_members(
        p["index_weight_dir"], style_codes + index_codes)
    print("")
    print("[LOAD_INDEX_MEMBERS] %s" % meta)
    print("")
    print("[MEMBER COUNTS]")
    for c in style_codes + index_codes:
        s = allx.get(c)
        nm = core._style_name(c)
        print("  %-12s %-10s n=%s" % (c, nm, len(s) if s else None))

    print("")
    print("[STYLE COVERAGE PER BATCH]")
    cases = [
        ("v13", "20260930", "20260930_selection_full.csv"),
        ("g2", "20260930", "20260930_g2_top10.csv"),
        ("ens", "20260930", "20260930_g2_top10.csv"),
    ]
    for kind, bd, fn in cases:
        inp = cio.load_batch_inputs(kind, bd, filename=fn)
        codes = inp["codes"]
        sm = inp["style_members"]
        hit = {}
        covered = set()
        for idx, mem in sorted(sm.items()):
            h = [c for c in codes if c in mem]
            if h:
                hit[idx] = h
                covered.update(h)
        print("  [%s %s] n=%d style_benchmarks_loaded=%d"
              % (kind, bd, len(codes), len(sm)))
        for idx in sorted(sm):
            h = hit.get(idx, [])
            print("      %-12s %-10s hit=%d %s"
                  % (idx, core._style_name(idx), len(h), h[:6]))
        print("      covered=%d/%d  uncovered=%s"
              % (len(covered), len(codes),
                 sorted(set(codes) - covered)))


if __name__ == "__main__":
    main()
    print("[PROBE-STYLE-COV-DONE]")
