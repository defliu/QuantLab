# coding: utf-8
python_path = "python"
import subprocess, sys
result = subprocess.run([python_path, r"D:\QuantLab\projects\Project_16_LightGBM股票大师\push_alert_card.py", "--title", "集合竞价预判 2026-09-28", "--body", "步骤1-5全部完成。G2候选7只，F2资金流T-4过期需注意，eastmoney_curl熔断。推送失败仅记录。", "--level", "warn"], capture_output=True, text=True, encoding="utf-8")
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("RC:", result.returncode)
