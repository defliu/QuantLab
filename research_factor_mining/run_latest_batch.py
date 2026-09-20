# coding=utf-8
"""自动找出 candidates/ 下最新未跑过的批次并执行 S1+S2（schtasks 周末批入口）

幂等：已生成 ic_screen/batch_<id>_report.md 的批次跳过，全部跑过则读最新报告打印漏斗。
退出码：0=成功/无新批次；非0=跑批失败（供 ps1 fail-loud）。
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAND = os.path.join(HERE, "candidates")
IC = os.path.join(HERE, "ic_screen")


def main():
    yaml_files = sorted(glob.glob(os.path.join(CAND, "batch_*.yaml")),
                        key=os.path.getmtime)
    if not yaml_files:
        print("NO_BATCH_YAML in %s" % CAND)
        return 2
    pending = []
    for y in yaml_files:
        bid = os.path.basename(y).replace("batch_", "").replace(".yaml", "")
        if not os.path.exists(os.path.join(IC, "batch_%s_report.md" % bid)):
            pending.append((y, bid))
    if not pending:
        latest = yaml_files[-1]
        bid = os.path.basename(latest).replace("batch_", "").replace(".yaml", "")
        print("ALL_BATCHES_DONE latest=%s" % bid)
        rp = os.path.join(IC, "batch_%s_report.md" % bid)
        if os.path.exists(rp):
            with open(rp, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("| ") and ("S1判定" not in line and "---" not in line):
                        print(line.rstrip()[:200])
        return 0
    target, bid = pending[-1]
    print("RUN_BATCH %s (%s)" % (bid, target))
    rc = subprocess.call([sys.executable, os.path.join(HERE, "run_screen.py"),
                          "--batch", os.path.relpath(target, HERE)],
                         cwd=HERE)
    print("RUN_BATCH_EXIT %d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
