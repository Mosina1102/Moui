@echo off
CHCP 65001 > nul
title Moui 主程序

REM ============ 1. 数据库：没起就后台拉起（幂等，重复运行不会开第二个） ============
netstat -ano | findstr ":27017" | findstr "LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在启动数据库 MongoDB...
    start "Moui-MongoDB" /min "mongodb\bin\mongod.exe" --dbpath=mongodb\db --logpath=mongodb\logs\mongod.log --port 27017
    echo 等待数据库就绪...
    timeout /t 4 /nobreak >nul
) else (
    echo 数据库已在运行，跳过
)

REM ============ 2. 环境检查 ============
if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [错误] 未找到虚拟环境，请先运行「第一次启动点我！！！.bat」完成安装
    echo.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

if not exist "config\bot_config.toml" (
    echo.
    echo [错误] 未找到 config\bot_config.toml，请先运行「第一次启动点我！！！.bat」
    echo.
    pause
    exit /b 1
)

REM ============ 3. NapCat 提示 ============
netstat -ano | findstr ":3001" | findstr "LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提醒] 尚未检测到 NapCat。
    echo        请另开一个窗口运行 napcat\napcat.quick.bat 登录 QQ 后再回来。
    echo        （首次部署请先运行「第一次启动点我！！！.bat」生成适配器配置）
)

REM ============ 4. 启动主程序 ============
echo.
echo 正在启动 Moui 主程序...
python bot.py
pause
