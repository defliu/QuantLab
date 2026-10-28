# coding=utf-8
"""构建 STRATEGY_P16_G2_EXEC 单文件产物（GBK）。

与 build_p16_g2_selfcontained.py 同约定：
  源码 UTF-8 → 注入 vendor（信号客户端）→ BUILD_TAG 必须恰 1 处 → 首行 # coding=gbk →
  py_compile 语法闸 → 以 GBK 写出 build/ → 写后回读复核。

用法：
  python build_p16_g2_exec.py            # 正常构建
  python build_p16_g2_exec.py --dry-run  # 只校验不落盘
"""
from __future__ import print_function

import io
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "strategy_p16_g2_exec.py")
CLIENT = os.path.join(HERE, "g2_signal_client.py")
OUT = os.path.join(HERE, "build", "strategy_p16_g2_exec.py")

MARK_BEGIN = "# ==== vendored: g2_signal_client.py ===="
MARK_END = "# ==== end vendor ===="


def _read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_coding(src):
    """去掉被注入文件的编码声明行（单文件只允许首行 # coding=gbk）。"""
    lines = src.splitlines()
    out = []
    for i, line in enumerate(lines):
        if i < 2 and re.match(r"^#.*coding[:=]", line):
            continue
        # __future__ 必须位于文件最前；注入到中段会 SyntaxError。print_function 在 py3
        # 下语义一致，直接去掉。
        if re.match(r"^from __future__ import", line):
            continue
        out.append(line)
    return "\n".join(out)


def _inject_vendor(src):
    if MARK_BEGIN not in src or MARK_END not in src:
        raise SystemExit("[VENDOR-REJECT] 源码缺少 vendor 标记")
    body = _strip_coding(_read(CLIENT))
    if "def fetch(" not in body or "def already_applied(" not in body:
        raise SystemExit("[VENDOR-REJECT] 客户端缺少 fetch/already_applied")
    block = MARK_BEGIN + "\n" + body + "\n" + MARK_END
    head, rest = src.split(MARK_BEGIN, 1)
    _, tail = rest.split(MARK_END, 1)
    return head + block + tail


def build(dry_run=False):
    src = _read(SRC)
    src = _inject_vendor(src)

    tag = time.strftime("%Y%m%d-%H%M%S")
    assign = re.compile(r'BUILD_TAG\s*=\s*"\d{8}-?\d{0,6}"')
    n = len(assign.findall(src))
    if n != 1:
        raise SystemExit("[BUILD_TAG-REJECT] 源码 BUILD_TAG 赋值应恰 1 处，实测 %d 处" % n)
    src = assign.sub('BUILD_TAG = "%s"' % tag, src, count=1)
    if 'BUILD_TAG = "%s"' % tag not in src:
        raise SystemExit("[BUILD_TAG-REJECT] 替换后校验失败")

    src = re.sub(r"^# coding[ \t]*=[ \t]*[\w-]+", "# coding=gbk", src, count=1, flags=re.M)
    if not src.lstrip().startswith("# coding=gbk"):
        raise SystemExit("[ENCODING-REJECT] 首行必须是 # coding=gbk")

    # 语法闸：产物以 GBK 落盘、首行声明 coding=gbk，所以临时文件也必须按 GBK 写，
    # 否则 py_compile 会按声明用 GBK 解码 UTF-8 字节而报 "illegal multibyte sequence"。
    data = src.encode("gbk", "strict")
    tmp = os.path.join(tempfile.mkdtemp(prefix="g2exec_"), "probe.py")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        subprocess.check_output([sys.executable, "-m", "py_compile", tmp], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise SystemExit("[SYNTAX-REJECT] %s" % e.output.decode("utf-8", "replace"))

    # 3.6 高危语法静态扫描
    bad = []
    if re.search(r"""\bf["']""", src):
        bad.append("f-string")
    if ":=" in src:
        bad.append("海象运算符")
    if re.search(r"^\s*match\s+", src, flags=re.M):
        bad.append("match/case")
    if bad:
        raise SystemExit("[PY36-REJECT] 命中 3.6 不支持的语法: " + ", ".join(bad))

    if "--dry-run" in sys.argv or dry_run:
        print("[DRY] BUILD_TAG=%s size=%d bytes（未落盘）" % (tag, len(data)))
        return tag

    if not os.path.isdir(os.path.dirname(OUT)):
        os.makedirs(os.path.dirname(OUT))
    with open(OUT, "wb") as f:
        f.write(data)

    back = io.open(OUT, "r", encoding="gbk").read()
    if (not back.lstrip().startswith("# coding=gbk")
            or 'BUILD_TAG = "%s"' % tag not in back
            or "def fetch(" not in back):
        raise SystemExit("[VERIFY-REJECT] 产物写后复核失败（首行/BUILD_TAG/vendor）")
    print("生成: %s (%d bytes) BUILD_TAG=%s" % (OUT, len(data), tag))
    return tag


if __name__ == "__main__":
    build()
