@echo off
rem ============================================================
rem MoUIMax E3 launcher packer (PyInstaller --noconsole --onefile)
rem Requires build python: .build\py312gui (python-build-standalone, with tkinter)
rem Usage: build_launcher.bat
rem Output: dist\Launcher\launcher.exe
rem ============================================================
setlocal
cd /d "%~dp0.."
set ICON_ABS=%~dp0..\installer\moui_logo.ico

set PYGUI=.build\py312gui\python\python.exe
set TCLSRC=%~dp0..\.build\py312gui\python\tcl
set PANELSRC=%~dp0..\.build\panel
if not exist %PYGUI% (
    echo [ERROR] build python not found: .build\py312gui ^(python-build-standalone with tkinter^)
    exit /b 1
)

echo [1/4] generate brand icon + stage panel assets...
%PYGUI% scripts\make_icon.py
if errorlevel 1 exit /b 1
if not exist .build\panel\panel.html (
    echo [ERROR] panel asset missing: .build\panel\panel.html
    exit /b 1
)

echo [2/4] clean old output...
if exist dist\Launcher\launcher.exe del /q dist\Launcher\launcher.exe

echo [3/4] PyInstaller packing...
%PYGUI% -m PyInstaller ^
    --noconfirm --clean --onefile --noconsole ^
    --name launcher ^
    --icon "%ICON_ABS%" ^
    --distpath dist\Launcher ^
    --workpath .build\pyinstaller ^
    --specpath .build\pyinstaller ^
    --add-data "%PANELSRC%;panel" ^
    --paths launcher_src ^
    --hidden-import migrate_legacy ^
    --hidden-import kbridge ^
    --hidden-import webview.platforms.edgechromium ^
    --hidden-import pystray._win32 ^
    --collect-all webview ^
    --collect-all clr_loader ^
    --collect-all pythonnet ^
    launcher_src\main.py
if errorlevel 1 (
    echo [ERROR] PyInstaller pack failed
    exit /b 1
)

echo.
echo [4/4] done. output: dist\Launcher\launcher.exe
endlocal
