#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run reconcile_ens.py for 20261001"""
import subprocess
import sys

p = subprocess.run(
    [r"D:\Python311\python.exe", "reconcile_ens.py", "--date", "20261001"],
    cwd=r"D:\QuantLab\projects\Project_16_LightGBM股票大师",
    capture_output=True,
    text=True,
    timeout=180,
)
print("RC:", p.returncode)
print("=== STDOUT ===")
print(p.stdout)
print("=== STDERR ===")
print(p.stderr)
sys.exit(p.returncode)