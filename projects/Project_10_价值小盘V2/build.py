# coding=utf-8
"""Project 10 QMT 构建器
源码: strategy_v2.py (项目根目录, UTF-8)
产物: build/strategy_v2.py (GBK, QMT 部署文件, 就在本项目内)
用法: python build.py"""
import os
import re
import time

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(PROJ_DIR, "strategy_v2.py")
BUILD_DIR = os.path.join(PROJ_DIR, "build")
DST = os.path.join(BUILD_DIR, "strategy_v2.py")
HEADER = b"# coding=gbk"


def build():
    # 1. 读取源码
    with open(SRC, "r", encoding="utf-8") as f:
        code = f.read()

    # 2. 语法检查
    try:
        compile(code, SRC, "exec")
    except SyntaxError as e:
        print("FAIL syntax error:", e)
        return False

    # 3. 禁止 Python 3.6+ 语法
    issues = []
    for i, line in enumerate(code.split("\n"), 1):
        s = line.strip()
        if ":=" in s:
            issues.append("line %d: walrus operator" % i)
        if s.startswith("def ") and "->" in s:
            issues.append("line %d: type hint" % i)
        if re.match(r"^\s*(match|case)\s+", s):
            issues.append("line %d: match/case" % i)
    if issues:
        for iss in issues:
            print("FAIL", iss)
        return False

    # 4. 注入构建版本标记（BUILD_TAG 源码为 "dev"，构建时替换为时间戳）
    tag = time.strftime("%Y%m%d-%H%M%S")
    code = code.replace('BUILD_TAG = "dev"', 'BUILD_TAG = "%s"' % tag)
    if 'BUILD_TAG = "dev"' in code:
        print("FAIL BUILD_TAG injection: placeholder not replaced")
        return False

    # 5. 转 GBK 写入 build/
    os.makedirs(BUILD_DIR, exist_ok=True)
    with open(DST, "wb") as f:
        f.write(code.encode("gbk"))

    # 6. 验证
    with open(DST, "rb") as f:
        raw = f.read()
    assert raw[:12] == HEADER, "header mismatch"

    print("OK build/strategy_v2.py (%d bytes, GBK, tag=%s)" % (len(raw), tag))
    print("   部署文件: projects/Project_10_价值小盘V2/build/strategy_v2.py")
    return True


if __name__ == "__main__":
    ok = build()
    raise SystemExit(0 if ok else 1)
