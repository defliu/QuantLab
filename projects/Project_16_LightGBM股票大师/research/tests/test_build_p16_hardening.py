# coding: utf-8
"""build_p16_*.py 构建脚本加固回归测试（T-20261003-003）。

覆盖：
  S1. 源码绊线：三构建脚本均含「恰 1 处断言 / 写后回读复核 / MOCK 残留扫描」
  A.  拒绝路径（合成源码，走临时 SRC+OUT）：0 处 / 2 处 / 单引号形态 → [BUILD_TAG-REJECT]
  A4. 接受路径（合成源码）：恰 1 处 → 新 tag 注入 + 旧 tag 清除 + 首行 gbk + 行数守恒
  B.  三脚本对真实 src 全链路 build() → 临时 OUT：tag 格式/唯一、首行 gbk、行数守恒、无残留旧 tag
  C.  全程 build/ 生产产物 sha256 逐字节不变（防测试误写，粘贴窗口红线）

用法（自建 runner，直接执行，非 pytest）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_build_p16_hardening.py
"""
import hashlib
import importlib.util
import os
import re
import shutil
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILDERS = [
    ("build_p16_g2.py", "strategy_p16_g2_bridge.py"),
    ("build_p16_v13_risk.py", "strategy_p16_v13_risk.py"),
    ("build_p16_ens.py", "strategy_p16_ens_bridge.py"),
]

PASS = 0
FAIL = 0


def _check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS %s" % name)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail))


def _load(builder_py):
    spec = importlib.util.spec_from_file_location(
        builder_py.replace(".py", "_hard"), os.path.join(PROJ, builder_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _synth_src(tag_lines):
    body = ["# coding=utf-8", '"""synthetic src for hardening test."""', ""]
    body.extend(tag_lines)
    body.extend(["", "X = 1", ""])
    return "\n".join(body)


def main():
    tmpdir = tempfile.mkdtemp(prefix="p16_build_hard_")
    prod_outs = [os.path.join(PROJ, "build", art) for _, art in BUILDERS]
    prod_hash_before = {}
    for p in prod_outs:
        if os.path.exists(p):
            prod_hash_before[p] = _sha256(p)

    # S1. 源码绊线：加固标记必须存在（防回归被静默移除）
    for builder_py, _ in BUILDERS:
        with open(os.path.join(PROJ, builder_py), "r", encoding="utf-8") as f:
            bsrc = f.read()
        _check("S1_%s_count_assert" % builder_py, "_n_assign != 1" in bsrc, "缺「恰 1 处」断言")
        _check("S1_%s_readback" % builder_py, "产物写后复核失败" in bsrc, "缺写后回读复核")
        _check("S1_%s_mock_scan" % builder_py, "[MOCK-REJECT]" in bsrc, "缺 MOCK 残留扫描")

    # A. 合成源码：拒绝路径（必须 SystemExit 且带 BUILD_TAG-REJECT）
    mod = _load("build_p16_g2.py")
    cases = [
        ("A1_zero_assign", []),
        ("A2_two_assign", ['BUILD_TAG = "20260901-000000"', 'BUILD_TAG = "20260902-000000"']),
        ("A3_single_quote", ["BUILD_TAG = '20260901-000000'"]),
    ]
    for name, tag_lines in cases:
        spath = os.path.join(tmpdir, name + ".py")
        with open(spath, "w", encoding="utf-8") as f:
            f.write(_synth_src(tag_lines))
        mod.SRC = spath
        mod.OUT = os.path.join(tmpdir, name + ".out.py")
        try:
            mod.build()
            _check(name, False, "预期 SystemExit，实际构建成功")
        except SystemExit as e:
            _check(name, "BUILD_TAG-REJECT" in str(e), "拒绝信息不含 BUILD_TAG-REJECT: %s" % e)
        _check(name + "_no_out", not os.path.exists(mod.OUT), "拒绝路径不应写出产物")

    # A4. 合成源码：恰 1 处 → 成功 + 注入 + 守恒
    spath = os.path.join(tmpdir, "a4_ok.py")
    with open(spath, "w", encoding="utf-8") as f:
        f.write(_synth_src(['BUILD_TAG = "20260901-000000"']))
    with open(spath, "r", encoding="utf-8") as f:
        synth_text = f.read()
    mod.SRC = spath
    mod.OUT = os.path.join(tmpdir, "a4_ok.out.py")
    tag = mod.build()
    with open(mod.OUT, "r", encoding="gbk") as f:
        back = f.read()
    _check("A4_tag_injected", 'BUILD_TAG = "%s"' % tag in back, back[:120])
    _check("A4_first_line_gbk", back.startswith("# coding=gbk"), back.split("\n")[0])
    _check("A4_old_tag_gone", "20260901-000000" not in back, "旧 tag 残留")
    _check("A4_linecount_kept", len(back.split("\n")) == len(synth_text.split("\n")), "行数不守恒")

    # B. 三脚本 × 真实 src 全链路 → 临时 OUT（不触生产产物）
    for builder_py, art in BUILDERS:
        m = _load(builder_py)
        _check("B_%s_src_exists" % builder_py, os.path.exists(m.SRC), m.SRC)
        m.OUT = os.path.join(tmpdir, "real_" + art)
        t = m.build()
        with open(m.OUT, "r", encoding="gbk") as f:
            back = f.read()
        with open(m.SRC, "r", encoding="utf-8") as f:
            src_text = f.read()
        _check("B_%s_tag_format" % builder_py, re.match(r"^\d{8}-\d{6}$", t) is not None, str(t))
        _check("B_%s_tag_once" % builder_py, back.count('BUILD_TAG = "%s"' % t) == 1, "tag 出现次数 != 1")
        _check("B_%s_first_line" % builder_py, back.startswith("# coding=gbk"), back.split("\n")[0])
        _check("B_%s_linecount" % builder_py, len(back.split("\n")) == len(src_text.split("\n")), "行数不守恒")
        stripped = back.replace('BUILD_TAG = "%s"' % t, "")
        _check("B_%s_placeholder_gone" % builder_py,
               re.search(r'BUILD_TAG\s*=\s*"\d{8}-\d{6}"', stripped) is None, "疑似残留旧 tag")

    # C. 生产产物逐字节不变（粘贴窗口红线：本次加固不触发重建、不产生新 tag）
    for p in prod_outs:
        _check("C_prod_unchanged_%s" % os.path.basename(p),
               os.path.exists(p) and _sha256(p) == prod_hash_before[p], "生产产物被改写!")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("=" * 60)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
