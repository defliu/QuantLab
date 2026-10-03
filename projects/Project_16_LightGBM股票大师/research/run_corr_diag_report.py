# -*- coding: utf-8 -*-
"""生成三策略真实批次的同批次选股相关性诊断报告（JSON + Markdown）。

用法（Python 3.11 研究环境）：
    python run_corr_diag_report.py --today 20261002 --batch 20260930

设计约束：
· generated_at 由命令行显式注入（模块内不取系统时间），保证可复现；
· 输出目录默认 results/corr_diag/，文件名含 kind 与 batch；
· 单个策略失败不影响其余策略，全部结果打印汇总后按退出码返回。
"""
import argparse
import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import corr_diag_io as cio  # noqa: E402
import corr_diag_report as rep  # noqa: E402

DEFAULT_OUT = os.path.abspath(
    os.path.join(HERE, "..", "results", "corr_diag"))

CASES = [
    ("v13", "20260930", "20260930_selection_full.csv"),
    ("g2", "20260930", "20260930_g2_top10.csv"),
    ("ens", "20260930", "20260930_g2_top10.csv"),
]


def run_case(kind, batch_date, filename, out_dir, today, thresholds=None):
    res = cio.diagnose_batch(kind, batch_date, filename=filename,
                             thresholds=thresholds, today=today)
    stem = "corr_%s_%s" % (kind, batch_date)
    jp, mp = rep.write_reports(out_dir, stem, res, today,
                               kind=kind, batch_date=batch_date)
    return res, jp, mp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True,
                    help="判定未来日期副本的基准日 YYYYMMDD（必填）")
    ap.add_argument("--batch", default=None,
                    help="批次日 YYYYMMDD，缺省用各策略默认批次")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    today = args.today
    print("[CORR-DIAG-REPORT] today=%s out=%s" % (today, args.out))

    rows = []
    rc = 0
    for kind, batch_date, filename in CASES:
        bd = args.batch or batch_date
        fn = filename.replace(batch_date, bd)
        try:
            res, jp, mp = run_case(kind, bd, fn, args.out, today)
        except Exception as e:  # noqa: BLE001
            rc = 2
            print("[FAIL] %s %s -> %s: %s"
                  % (kind, bd, type(e).__name__, e))
            traceback.print_exc()
            continue
        secs = res.get("section_status") or {}
        rows.append((kind, bd, res.get("overall"), secs, jp, mp))
        print("[OK] %-4s %s overall=%-4s io=%-4s codes=%s"
              % (kind, bd, res.get("overall"),
                 (res.get("io") or {}).get("io_status"),
                 res.get("n_candidates")))
        print("     json=%s" % jp)
        print("     md  =%s" % mp)

    print("-" * 70)
    print("[SUMMARY] %d/%d 成功" % (len(rows), len(CASES)))
    for kind, bd, ov, secs, jp, mp in rows:
        print("  %-4s %s overall=%-4s 维度=%s"
              % (kind, bd, ov,
                 json.dumps(secs, ensure_ascii=False, sort_keys=True)))
    print("[CORR-DIAG-REPORT-DONE]")
    return rc


if __name__ == "__main__":
    sys.exit(main())