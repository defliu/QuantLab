# coding: utf-8
"""Project_16 g2 大QMT 文件桥 · 构建脚本。

读 strategy/strategy_p16_g2_bridge_src.py (UTF-8 源码)，
替换 BUILD_TAG 为当前时间戳，GBK 编码写出到 build/strategy_p16_g2_bridge.py，
并做 Python 3.6 语法检查（QMT 内置兼容）。

用法:
  python build_p16_g2.py
产物:
  build/strategy_p16_g2_bridge.py   # coding=gbk, BUILD_TAG=最新时间戳
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "strategy", "strategy_p16_g2_bridge_src.py")
OUT = os.path.join(HERE, "build", "strategy_p16_g2_bridge.py")

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


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(SRC, "r", encoding="utf-8") as f:
        src = f.read()

    # 1. 语法检查（py3.6）：用当前解释器 py_compile 先过一遍
    subprocess.run([sys.executable, "-m", "py_compile", SRC], check=True)

    # 2. 禁用语法扫描
    src_lines = src.split("\n")
    for i, line in enumerate(src_lines, 1):
        for pat, desc in FORBIDDEN:
            if re.search(pat, line):
                raise SystemExit("[PY36-REJECT] 第%d行 含 %s: %s" % (i, desc, line.strip()))

    # 3. 替换 BUILD_TAG
    tag = time.strftime("%Y%m%d-%H%M%S")
    new_src = re.sub(r'BUILD_TAG\s*=\s*"\d{8}-?\d{0,6}"', 'BUILD_TAG = "%s"' % tag, src, count=1)
    if "BUILD_TAG = \"%s\"" % tag not in new_src:
        raise SystemExit("BUILD_TAG 替换失败")

    # 4. 首行编码声明统一替换为 gbk（源码可能是 # coding=utf-8）
    new_src = re.sub(r'^# coding[ \t]*=[ \t]*[\w-]+', '# coding=gbk',
                     new_src, count=1, flags=re.MULTILINE)
    if not new_src.lstrip().startswith("# coding=gbk"):
        raise SystemExit("产物首行必须是 # coding=gbk")

    # 5. GBK 写出
    with open(OUT, "w", encoding="gbk") as f:
        f.write(new_src)
    size = os.path.getsize(OUT)
    print("生成: %s (%d bytes) BUILD_TAG=%s" % (OUT, size, tag))
    return tag


if __name__ == "__main__":
    build()