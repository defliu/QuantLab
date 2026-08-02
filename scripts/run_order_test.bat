@echo off
echo ========================================
echo   QuantLab Order Test Log Check
echo ========================================
echo.

echo Checking order test log...
if exist "D:\QMT_POOL\order_test_log.txt" (
    echo.
    echo === Latest Log Entries ===
    type "D:\QMT_POOL\order_test_log.txt" | findstr /N "."
    echo.
    echo === End of Log ===
) else (
    echo No log file found.
    echo Please run the order test strategy in QMT first.
)

echo.
echo ========================================
echo   Press any key to exit...
echo ========================================
pause >nul
