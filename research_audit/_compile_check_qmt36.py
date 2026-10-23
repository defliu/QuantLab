# -*- coding: utf-8 -*-
"""QMT python3.6 语法编译验证（py_compile 严格模式）。"""
import py_compile
import os

FILES = [
    r"d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_g2_bridge_src.py",
    r"d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_ens_bridge_src.py",
    r"d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\strategy_p16_v13_risk_src.py",
    r"d:\QuantLab\projects\Project_10_价值小盘V2\strategy_v2.py",
]

OUT = r"d:\QuantLab\research_audit\_compile_check_result.txt"
TMP = r"d:\QuantLab\research_audit\_pyc_tmp.pyc"

lines = []
for p in FILES:
    try:
        py_compile.compile(p, cfile=TMP, doraise=True)
        lines.append("OK   " + p)
    except Exception as e:
        lines.append("FAIL " + p + " : " + repr(e))

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
print("done")
