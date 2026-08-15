@echo off
setlocal enabledelayedexpansion
set BASE=C:\Users\Administrator
set DST=D:\AI

echo === Step 1: Clean .vscode.bak ===
if exist "%BASE%\.vscode.bak" (
    rmdir /s /q "%BASE%\.vscode.bak"
    if not exist "%BASE%\.vscode.bak" (echo   [OK] .vscode.bak deleted) else (echo   [FAIL] .vscode.bak still exists)
) else (
    echo   .vscode.bak already gone
)

echo.
echo === Step 2: Create junctions ===
for %%d in (.codebuddy .config .vscode-shared) do (
    echo --- %%d ---
    set SRC=%BASE%\%%d
    set BAK=%BASE%\%%d.bak
    set TGT=%DST%\%%d

    if exist "!SRC!" (
        rem Check if already a junction
        dir "!SRC!" 2>nul | findstr /C:"<JUNCTION>" >nul
        if !errorlevel! equ 0 (
            echo   already a junction
        ) else (
            echo   renaming to .bak ...
            move "!SRC!" "!BAK!" >nul 2>&1
            if exist "!BAK!" (
                echo   [OK] renamed
                echo   creating junction !SRC! -^> !TGT!
                mklink /J "!SRC!" "!TGT!" >nul 2>&1
                if !errorlevel! equ 0 (
                    echo   [OK] junction created
                    echo   deleting backup ...
                    rmdir /s /q "!BAK!" >nul 2>&1
                    if exist "!BAK!" (echo   [WARN] backup not deleted) else (echo   [OK] backup deleted, C: freed)
                ) else (
                    echo   [FAIL] mklink failed, restoring ...
                    move "!BAK!" "!SRC!" >nul 2>&1
                )
            ) else (
                echo   [FAIL] rename failed
            )
        )
    ) else (
        echo   SRC missing, checking .bak ...
        if exist "!BAK!" (
            echo   .bak exists, restoring ...
            move "!BAK!" "!SRC!" >nul 2>&1
            echo   restored, skipping junction
        ) else (
            echo   [ERROR] both missing!
        )
    )
)

echo.
echo === Final check ===
for %%d in (.codex .vscode .codebuddy .config .vscode-shared) do (
    dir "%BASE%\%%d" 2>nul | findstr /C:"<JUNCTION>" >nul
    if !errorlevel! equ 0 (
        echo   %%d : JUNCTION
    ) else if exist "%BASE%\%%d" (
        echo   %%d : REAL DIR
    ) else (
        echo   %%d : MISSING
    )
)
echo done.