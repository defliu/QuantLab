@echo off
cd /d "D:\QuantLab\projects\Project_16_LightGBM股票大师"
"D:\Python311\python.exe" reconcile_ens.py --date 20261001
exit /b %ERRORLEVEL%