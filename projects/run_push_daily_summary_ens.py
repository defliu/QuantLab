#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run push_daily_summary_card.py for 20261001 (ensemble summary)"""
import subprocess
import sys

p = subprocess.run(
    [r"D:\Python311\python.exe", "push_daily_summary_card.py", "--date", "20261001",
     "--summary", "融合对账:无差额|无孤儿"],
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