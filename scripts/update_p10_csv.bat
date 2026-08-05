@echo off
REM ========================================
REM   Project_10 QMT 预生成 CSV 每日管道
REM   步骤: 1) 更新 E:/astock 数据  2) 生成 D:/QMT_POOL/ CSV
REM   建议计划任务: 每个交易日 18:30
REM ========================================
chcp 65001 >nul
cd /d E:\QuantLab

echo [%date% %time%] Step 1/2: 更新 astock 数据...
python scripts\update_data.py
if errorlevel 1 (
    echo [WARN] 数据更新失败或非交易日, 继续用现有数据生成 CSV
)

echo [%date% %time%] Step 2/2: 生成 QMT 预生成 CSV...
python scripts\gen_qmt_csv.py
if errorlevel 1 (
    echo [ERROR] CSV 生成失败
    exit /b 1
)

echo [%date% %time%] 管道完成
