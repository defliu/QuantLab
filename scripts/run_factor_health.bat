@echo off
REM ========================================
REM  Factor Health factor health monitoring - weekly task
REM  run: python research_audit/factor_health.py
REM  out: D:\QuantLab\research_audit\factor_health_report.md + factor_health_ic.csv
REM  schedule: every Friday 19:30 (after P10 CSV daily 18:30 update)
REM ========================================
chcp 65001 >nul
cd /d D:\QuantLab

set PYEXE=D:\hermes\hermes-agent\venv\Scripts\python.exe
if not exist "%PYEXE%" set PYEXE=python

echo [%date% %time%] Running Factor Health Monitor...
"%PYEXE%" research_audit\factor_health.py
if errorlevel 1 (
    echo [ERROR] Factor Health Monitor failed
    exit /b 1
)

echo [%date% %time%] Factor Health Monitor complete
