# -*- coding: utf-8 -*-
"""QMT python3.6 编译验证：build 产物（GBK 部署文件）。"""
import py_compile
import os

FILES = [
    r"d:\QuantLab\projects\Project_10_价值小盘V2\build\strategy_v2.py",
    r"d:\QuantLab\projects\Project_16_LightGBM股票大师\build\strategy_p16_g2_selfcontained.py",
    r"d:\QuantLab\projects\Project_16_LightGBM股票大师\build\strategy_p16_ens_selfcontained.py",
    r"d:\QuantLab\projects\Project_16_LightGBM股票大师\build\strategy_p16_v13_risk.py",
]

OUT = r"d:\QuantLab\research_audit\_compile_check_result2.txt"
TMP = r"d:\QuantLab\research_audit\_pyc_tmp2.pyc"

lines = []
for p in FILES:
    if not os.path.exists(p):
        lines.append("MISS " + p)
        continue
    try:
        py_compile.compile(p, cfile=TMP, doraise=True)
        lines.append("OK   " + p)
    except Exception as e:
        lines.append("FAIL " + p + " : " + repr(e))

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
print("done")
