@echo off
echo ========================================
echo   QuantLab Data Update
echo ========================================
echo.

cd /d E:\QuantLab
python scripts\update_data.py

echo.
echo ========================================
echo   Press any key to exit...
echo ========================================
pause >nul
