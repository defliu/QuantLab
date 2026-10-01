import subprocess, sys, os
result = subprocess.run([r"C:\Users\Administrator\AppData\Local\uv\cache\builds-v0.tmpA4nBOU\Scripts\python.exe", r"D:\QuantLab\projects\Project_16_LightGBM股票大师\rebalance_ens.py", "--date", "20260928"], capture_output=True, text=True, cwd=r"D:\QuantLab\projects\Project_16_LightGBM股票大师")
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("RC:", result.returncode)
