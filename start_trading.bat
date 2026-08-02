@echo off
chcp 65001 >nul
echo ========================================
echo   QuantLab 实盘交易系统
echo   账号: 67014907
echo ========================================
echo.

:: 检查Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python未安装或未加入PATH
    pause
    exit /b 1
)

:: 检查QMT是否运行
tasklist | findstr /i "XtMiniQmt.exe" >nul
if %errorlevel% neq 0 (
    echo [WARNING] miniQMT未运行
    echo 请先启动: E:\国金QMT交易端模拟\XtMiniQmt.exe
    echo.
    choice /C YN /M "是否继续启动（Y=继续 N=退出）"
    if %errorlevel% equ 2 exit /b 0
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

echo [INFO] 启动实盘交易系统...
echo [INFO] 日志目录: %cd%\logs
echo [INFO] 配置文件: config\trading_config.yaml
echo.

:: 启动
python live_trading.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 程序异常退出，错误码: %errorlevel%
    pause
)
