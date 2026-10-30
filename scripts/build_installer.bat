@echo off
rem ============================================================
rem MoUIMax E2 installer build script (Inno Setup ISCC)
rem Usage: build_installer.bat [/runtime]   (/runtime = rebuild E1 runtime first)
rem Output: dist\MoUIMax_Setup_<ver>_x64.exe
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

rem ---- version injection: generate installer\version.iss from root VERSION (4th-review #3) ----
if not exist VERSION (
    echo [ERROR] root VERSION file missing
    exit /b 1
)
set /p VER=<VERSION
> installer\version.iss echo #define MyAppVersion "%VER%"
echo [1/3] version.iss injected: MoUIMax %VER%

rem ---- optional: rebuild E1 runtime ----
if "%1"=="/runtime" (
    echo [2/3] rebuilding E1 runtime...
    py -3 scripts\build_runtime.py --skip-bootstrap
    if errorlevel 1 (
        echo [ERROR] runtime build failed
        exit /b 1
    )
) else (
    if not exist dist\runtime\python.exe (
        echo [ERROR] dist\runtime missing. Run first: py -3 scripts\build_runtime.py
        exit /b 1
    )
    echo [2/3] reuse existing dist\runtime
)

rem ---- locate ISCC.exe (Inno Setup command-line compiler) ----
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if %ISCC%=="" (
    echo [ERROR] ISCC.exe not found. Install Inno Setup 6: https://jrsoftware.org/isdl.php
    exit /b 1
)

rem ---- Chinese language file check (4th-review #1: setup wizard must be Chinese) ----
if not exist installer\ChineseSimplified.isl (
    echo [ERROR] installer\ChineseSimplified.isl missing - aborting
    exit /b 1
)

echo [3/3] ISCC compiling...
%ISCC% installer\moui.iss
if errorlevel 1 (
    echo [ERROR] Inno Setup compile failed
    exit /b 1
)
echo.
echo [OK] output: dist\MoUIMax_Setup_%VER%_x64.exe
endlocal
