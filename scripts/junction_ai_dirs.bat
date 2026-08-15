@echo off
setlocal enabledelayedexpansion
set SRC_BASE=C:\Users\Administrator
set DST_BASE=D:\AI

for %%d in (.codex .vscode .codebuddy .config .vscode-shared) do (
    echo --- %%d ---
    
    REM clean stale backup
    if exist "%SRC_BASE%\%%d.bak" (
        rmdir /s /q "%SRC_BASE%\%%d.bak" 2>nul
        echo   cleaned stale .bak
    )
    
    REM rename original to .bak
    if exist "%SRC_BASE%\%%d" (
        move "%SRC_BASE%\%%d" "%SRC_BASE%\%%d.bak" >nul 2>&1
        if errorlevel 1 (
            echo   FAILED rename %%d
            goto :next
        )
        echo   renamed -> %%d.bak
    ) else (
        echo   %%d already missing, checking junction...
    )
    
    REM create junction
    mklink /J "%SRC_BASE%\%%d" "%DST_BASE%\%%d" >nul 2>&1
    if errorlevel 1 (
        echo   FAILED mklink, restoring...
        if exist "%SRC_BASE%\%%d.bak" (
            move "%SRC_BASE%\%%d.bak" "%SRC_BASE%\%%d" >nul 2>&1
        )
        goto :next
    )
    echo   [OK] junction created
    
    REM delete backup
    if exist "%SRC_BASE%\%%d.bak" (
        rmdir /s /q "%SRC_BASE%\%%d.bak" 2>nul
        if not exist "%SRC_BASE%\%%d.bak" (
            echo   [OK] backup deleted, C: space freed
        ) else (
            echo   WARN: backup not fully deleted
        )
    )
    :next
    echo.
)

echo === final verification ===
for %%d in (.codex .vscode .codebuddy .config .vscode-shared) do (
    dir /AL "%SRC_BASE%\%%d" >nul 2>&1
    if errorlevel 1 (
        echo   %%d : NOT JUNCTION (check it!)
    ) else (
        echo   %%d : JUNCTION OK
    )
)
echo done.
