@echo off
chcp 65001 >nul
echo ========================================
echo   QuantLab 实盘交易系统
echo   账号: 67014907
echo   使用QMT内置Python 3.6.8
echo ========================================
echo.

:: 设置QMT Python路径
set QMT_PYTHON=E:\国金QMT交易端模拟\bin.x64\python3\python.exe

:: 检查QMT Python
if not exist "%QMT_PYTHON%" (
    echo [ERROR] QMT Python不存在: %QMT_PYTHON%
    echo 请检查QMT安装路径
    pause
    exit /b 1
)

:: 检查QMT是否运行
tasklist | findstr /i "XtMiniQmt.exe" >nul
if %errorlevel% neq 0 (
    echo [WARNING] QMT未运行
    echo.
    echo 请先启动QMT:
    echo   E:\国金QMT交易端模拟\bin.x64\XtMiniQmt.exe
    echo.
    echo 登录账号: 67014907
    echo.
    choice /C YN /M "是否自动启动QMT (Y=是 N=继续)"
    if %errorlevel% equ 1 (
        start "" "E:\国金QMT交易端模拟\bin.x64\XtMiniQmt.exe"
        echo 等待QMT启动...
        timeout /t 10 /nobreak >nul
    )
)

:: 创建日志目录
if not exist "logs" mkdir logs
if not exist "data" mkdir data

:: 检查配置文件
if not exist "config\trading_config.yaml" (
    echo [ERROR] 配置文件不存在: config\trading_config.yaml
    pause
    exit /b 1
)

echo.
echo [INFO] 使用QMT Python: %QMT_PYTHON%
echo [INFO] 日志目录: %cd%\logs
echo [INFO] 配置文件: config\trading_config.yaml
echo.
echo [INFO] 启动实盘交易系统...
echo.

:: 使用QMT Python运行
"%QMT_PYTHON%" live_trading.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 程序异常退出，错误码: %errorlevel%
    pause
)
