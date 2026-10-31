# coding: utf-8
"""p16_paper_forward_report 回归：解析口径 / 卡片结构 / 缺报告退出码。

只读 data/real 下 2026-10-30 的真实产物，不推送、不写生产文件。
运行：python _test_paper_forward_report.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import p16_paper_forward_report as R  # noqa: E402

PASS = []
FAIL = []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, (" | " + detail) if detail else ""))


def near(a, b, tol=1e-9):
    return a is not None and abs(a - b) <= tol


def main():
    d = "2026-10-30"
    fs = R.parse_forward_stats(R._read(os.path.join(R.REAL, "forward_stats_20261030.md")))
    ok("fs.filled", fs["filled"] == 224, str(fs.get("filled")))
    ok("fs.pending", fs["pending"] == 12, str(fs.get("pending")))
    ok("fs.total", fs["total"] == 236, str(fs.get("total")))
    ok("fs.hold", fs["hold"] == 10, str(fs.get("hold")))
    ok("fs.all.excess", near(fs["rows"]["all"]["excess"], -0.108), str(fs["rows"]["all"]["excess"]))
    ok("fs.all.t", near(fs["rows"]["all"]["t"], -0.17), str(fs["rows"]["all"]["t"]))
    ok("fs.top3.n", fs["rows"]["top3"]["n"] == 91, str(fs["rows"]["top3"]["n"]))

    ex = R.parse_exit(R._read(os.path.join(R.REAL, "paper_forward_exit_2026-10-30.md")))
    ok("ex.due", ex["due"] == 91, str(ex.get("due")))
    ok("ex.not_due", ex["not_due"] == 10, str(ex.get("not_due")))
    ok("ex.total", ex["total"] == 101, str(ex.get("total")))
    live = ex["rules"].get("实盘口径:-7%+8%追盈+15%")
    fixed = ex["rules"].get("固定-7%/+15%(回测原口径)")
    ok("ex.live_rule", live is not None and near(live["mean"], -1.419),
       str(live))
    ok("ex.fixed_rule", fixed is not None and near(fixed["mean"], -1.662),
       str(fixed))
    ok("ex.verdict", "维持现规则" in ex["verdict"], ex["verdict"][:40])

    sh = R.parse_shadow(R._read(os.path.join(R.REAL, "paper_shadow_b21_20261030.md")))
    ok("sh.forward", sh["forward"] == 0, str(sh.get("forward")))
    ok("sh.backfill", sh["backfill"] == 102, str(sh.get("backfill")))
    ok("sh.asof", sh["asof"] == "20261029", str(sh.get("asof")))
    ok("sh.ret10", near(sh["all"]["ret10"], 0.60), str(sh["all"].get("ret10")))
    ok("sh.win15", near(sh["all"]["win15"], 57), str(sh["all"].get("win15")))
    ok("sh.name_hit", sh.get("name_hit") == 0 and sh.get("name_all") == 66,
       "%s/%s" % (sh.get("name_hit"), sh.get("name_all")))

    mark = R.load_downgrade()
    ok("dg.G2", mark and mark["G2"]["frozen"] is False)
    ok("dg.V13", mark and mark["V1.3"]["frozen"] is True)
    ok("dg.ENS", mark and mark["ENS"]["frozen"] is True)

    body = R.build_body(fs, ex, sh, mark, d, [], [])
    for sec in ("① 43f", "② 21f", "③ 出场规则", "④ 纸面自动降级闸"):
        ok("body.section %s" % sec, sec in body)
    ok("body.no_None", "None" not in body)
    ok("body.frozen_flag", "冻结" in body)

    card = R.build_card("P16 纸面前向日报", "2026-10-31 · 数据日 2026-10-30", body, "warn")
    ok("card.schema", card.get("schema") == "2.0")
    ok("card.template", card["header"]["template"] == "orange")
    ok("card.body", card["body"]["elements"][0]["elements"][0]["content"] == body)

    ok("select_date.explicit", R.select_date("20261030", 0)[0] == d)
    latest, warns = R.select_date("", 0)
    ok("select_date.latest", latest is not None and len(latest) == 10, str(latest))

    # 不存在的日期 → 无报告，退出 4，且不推送
    r = subprocess.run([sys.executable, os.path.join(HERE, "p16_paper_forward_report.py"),
                        "--date", "20990101", "--no-send"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    ok("missing.date_rc4", r.returncode == 4, "rc=%s" % r.returncode)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
