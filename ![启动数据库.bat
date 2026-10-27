@echo off
CHCP 65001 > nul
title Moui 数据库

netstat -ano | findstr ":27017" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo 数据库已在运行，无需重复启动。
    pause
    exit /b 0
)

echo 正在启动 MongoDB（最小化窗口运行，请勿关闭那个窗口）...
start "Moui-MongoDB" /min "mongodb\bin\mongod.exe" --dbpath=mongodb\db --logpath=mongodb\logs\mongod.log --port 27017
timeout /t 3 /nobreak >nul
netstat -ano | findstr ":27017" | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo 数据库已启动成功（端口 27017）。
) else (
    echo [错误] 数据库启动失败，请检查 mongodb\logs\mongod.log
)
pause
