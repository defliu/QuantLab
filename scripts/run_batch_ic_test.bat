@echo off
echo ========================================
echo   Batch IC Test: alpha101 + gtja191
echo   This may take 2-3 hours...
echo ========================================
echo.

cd /d E:\QuantLab
python scripts\batch_ic_test.py

echo.
echo ========================================
echo   IC Test Complete!
echo   Results in: reports/factor_ic_report.csv
echo ========================================
pause
