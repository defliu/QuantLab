import py_compile
import re

src = r"D:\QuantLab\projects\Project_10_价值小盘V2\debug_pending.py"
out = r"D:\QuantLab\projects\Project_10_价值小盘V2\build\debug_pending.py"

text = open(src, encoding="utf-8").read()
lines = text.split("\n")
if lines[0].strip().lower().startswith("# coding="):
    lines[0] = "# coding=gbk"
else:
    lines.insert(0, "# coding=gbk")
gbk_text = "\n".join(lines)

# 语法检查：先按 utf-8 编译源
py_compile.compile(src, doraise=True)
print("py_compile(utf-8 source) OK")

# 3.6 语法检查
bad = []
if re.search(r"f[\"']|:=|match\s+\w+\s*:", text):
    bad.append("f-string/walrus/match")
if any(x in text for x in ("list[str]", "dict[str", "| None")):
    bad.append("3.10 type syntax")
print("3.6 syntax check:", "OK" if not bad else " | ".join(bad))

data = gbk_text.encode("gbk")
with open(out, "wb") as f:
    f.write(data)
print("GBK artifact:", out, len(data), "bytes")

gb = open(out, "rb").read()
print("first line:", gb[:12])
print("GBK roundtrip == source:", gb.decode("gbk") == gbk_text)