# coding: utf-8
"""Project_16 V1.3 QMT 内置风控 · 构建脚本。

读 strategy/strategy_p16_v13_risk_src.py (UTF-8 源码，裁剪自 g2 桥风控模块)，
替换 BUILD_TAG 为当前时间戳，GBK 编码写出到 build/strategy_p16_v13_risk.py，
并做 Python 3.6 语法检查 + MOCK 残留检查（QMT 内置兼容，红线见 AGENTS.md）。

用法:
  python build_p16_v13_risk.py
产物:
  build/strategy_p16_v13_risk.py   # coding=gbk, BUILD_TAG=最新时间戳
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "strategy", "strategy_p16_v13_risk_src.py")
OUT = os.path.join(HERE, "build", "strategy_p16_v13_risk.py")

# QMT 生产产物禁用语法（Python 3.6 不兼容）
FORBIDDEN = [
    (r"[:,].*\| *None", "类型联合 str | None"),
    (r"(?<![:\w])dict\[", "dict[...] 泛型"),
    (r"(?<![:\w])list\[", "list[...] 泛型"),
    (r"(?<![:\w])tuple\[", "tuple[...] 泛型"),
    (r":= *", "海象运算符 :="),
    (r"\bmatch\b *\w", "match/case"),
    (r"\bf[\"']", "f-string"),
]
# MOCK / 测试残留（生产版红线：不得混入 mock 或测试代码）
FORBIDDEN_TEXT = [
    ("MOCK", "MOCK 残留"),
    ("mock", "mock 残留"),
    ("pytest", "pytest 残留"),
    ("unittest", "unittest 残留"),
    ("assert ", "assert 残留"),
]


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(SRC, "r", encoding="utf-8") as f:
        src = f.read()

    # 1. 语法检查（py3.6）：用当前解释器 py_compile 先过一遍
    subprocess.run([sys.executable, "-m", "py_compile", SRC], check=True)

    # 1.1 死代码静态扫描前置（T-20260919-002 三道防线·A，2026-09-20）：命中 P1 建列后判 / P2 passorder 真值判断即中止构建
    _scan = subprocess.run([sys.executable, r"D:\QuantLab\scripts\scan_deadcode.py", "--paths", SRC],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if _scan.stdout:
        # GBK 控制台无法编码替换字符时降级打印，不阻断构建
        try:
            print(_scan.stdout.decode("utf-8", "replace")[-2000:])
        except UnicodeEncodeError:
            print(repr(_scan.stdout[-500:]))
    if _scan.returncode != 0:
        raise SystemExit("[DEADCODE-REJECT] 死代码扫描命中（P1建列后判 / P2 passorder真值判断），中止构建")

    # 2. 禁用语法扫描
    src_lines = src.split("\n")
    for i, line in enumerate(src_lines, 1):
        for pat, desc in FORBIDDEN:
            if re.search(pat, line):
                raise SystemExit("[PY36-REJECT] 第%d行 含 %s: %s" % (i, desc, line.strip()))

    # 2.5 MOCK / 测试残留扫描（AGENTS：生产版不得混入 mock 或测试代码）
    for i, line in enumerate(src_lines, 1):
        for token, desc in FORBIDDEN_TEXT:
            if token in line:
                raise SystemExit("[MOCK-REJECT] 第%d行 含 %s: %s" % (i, desc, line.strip()))

    # 3. 替换 BUILD_TAG（T-20261003-003 加固：源码赋值必须恰为 1 处；原 count=1 弱校验会静默漏替第二处）
    tag = time.strftime("%Y%m%d-%H%M%S")
    _assign = re.compile(r'BUILD_TAG\s*=\s*"\d{8}-?\d{0,6}"')
    _n_assign = len(_assign.findall(src))
    if _n_assign != 1:
        raise SystemExit("[BUILD_TAG-REJECT] 源码 BUILD_TAG 赋值应恰为 1 处，实测 %d 处" % _n_assign)
    new_src = _assign.sub('BUILD_TAG = "%s"' % tag, src, count=1)
    if len(_assign.findall(new_src)) != 1 or 'BUILD_TAG = "%s"' % tag not in new_src:
        raise SystemExit("[BUILD_TAG-REJECT] BUILD_TAG 替换失败或替换后校验不通过")

    # 4. 首行编码声明统一替换为 gbk（源码可能是 # coding=utf-8）
    new_src = re.sub(r'^# coding[ \t]*=[ \t]*[\w-]+', '# coding=gbk',
                     new_src, count=1, flags=re.MULTILINE)
    if not new_src.lstrip().startswith("# coding=gbk"):
        raise SystemExit("产物首行必须是 # coding=gbk")

    # 5. GBK 写出（T-20261003-003 加固：写后回读复核，防截断/未刷新/编码异常）
    with open(OUT, "w", encoding="gbk") as f:
        f.write(new_src)
    with open(OUT, "r", encoding="gbk") as f:
        _back = f.read()
    if (not _back.lstrip().startswith("# coding=gbk")
            or 'BUILD_TAG = "%s"' % tag not in _back
            or len(_back.split("\n")) != len(src.split("\n"))):
        raise SystemExit("[BUILD_TAG-REJECT] 产物写后复核失败（首行/BUILD_TAG/行数）")
    size = os.path.getsize(OUT)
    print("生成: %s (%d bytes) BUILD_TAG=%s" % (OUT, size, tag))
    return tag


if __name__ == "__main__":
    build()
