# -*- coding: utf-8 -*-
"""Project_20 build: 源码 -> GBK 单文件产物（BUILD_TAG 时间戳 + 四道校验）

用法：python build.py
产物：build/strategy_all_weather_v1.py（# coding=gbk，人工粘贴进 QMT 客户端策略编辑器）
"""
import ast
import datetime
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "strategy", "strategy_all_weather_src.py")
OUT = os.path.join(HERE, "build", "strategy_all_weather_v1.py")

tag = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

with open(SRC, encoding="utf-8") as f:
    src = f.read()

# 0) 禁止项校验（QMT Py3.6.8 红线）
bad = []
if ":=" in src:
    bad.append("walrus := 禁用于 QMT 产物")
if "f'" in src or 'f"' in src:
    bad.append("f-string 禁用于 QMT 产物")
for kw in ("dict[str", "list[str", "str | None", "match "):
    if kw in src:
        bad.append("Py3.6 不兼容语法: %s" % kw)
if bad:
    raise SystemExit("BUILD FAIL: %s" % "; ".join(bad))

# 1) 语法检查
compile(src, SRC, "exec")
ast.parse(src, feature_version=(3, 6))

# 2) BUILD_TAG 替换
src = src.replace("TAG_PLACEHOLDER", tag)

# 3) GBK 转码 + 文件头
body = src
for h in ("# -*- coding: utf-8 -*-\n", "# -*- coding: utf-8 -*-\r\n"):
    body = body.replace(h, "", 1)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "wb") as f:
    f.write(b"# coding=gbk\r\n")
    f.write(body.encode("gbk"))

# 4) 四道校验
raw = open(OUT, "rb").read()
assert raw.startswith(b"# coding=gbk"), "文件头缺失"
raw.decode("gbk")  # GBK 可解码
text = raw.decode("gbk")
assert "TAG_PLACEHOLDER" not in text, "BUILD_TAG 未替换"
assert "MOCK" not in text.upper(), "MOCK 残留"
ast.parse(text, feature_version=(3, 6))
print("[BUILD OK] -> %s size=%d BUILD_TAG=%s" % (OUT, len(raw), tag))
