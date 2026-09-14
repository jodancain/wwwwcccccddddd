@echo off
chcp 65001 >nul 2>&1
title WeChatAI - 智能微信助手
cd /d "%~dp0"

set "PROJECT_DIR=%CD%"
set "BACKEND_DIR=%PROJECT_DIR%\backend"
if exist "%PROJECT_DIR%\..\backend\.env" set "BACKEND_DIR=%PROJECT_DIR%\..\backend"
set "BACKEND_PYTHON=python"
if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" set "BACKEND_PYTHON=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "WECHATAI_FRONTEND_DIST=%PROJECT_DIR%\frontend\dist"

echo ==========================================
echo    WeChatAI - Cursor 版微信
echo ==========================================
echo.

:: Check Python
"%BACKEND_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 未安装，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: Check Node
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js 未安装，请先安装 Node.js 18+
    pause
    exit /b 1
)

:: Prefer the sibling deployed backend when it owns the live .env and database.
if not exist "%BACKEND_DIR%\.env" (
    if exist ".env" (
        echo [INFO] 复制 .env 到 backend 目录...
        copy ".env" "%BACKEND_DIR%\.env" >nul
    ) else (
        echo [ERROR] 未找到 .env 配置文件
        echo        请复制 .env.example 为 .env 并填入你的 API Key
        pause
        exit /b 1
    )
)

:: Install backend deps if needed
if not exist "%BACKEND_DIR%\data" (
    echo [INFO] 首次运行，安装后端依赖...
    pushd "%BACKEND_DIR%"
    "%BACKEND_PYTHON%" -m pip install -r requirements.txt -q
    popd
    echo.
)

:: Install frontend deps if needed
if not exist "frontend\node_modules" (
    echo [INFO] 首次运行，安装前端依赖...
    cd frontend
    call npm install --silent
    cd ..
    echo.
)

:: Ensure the OpenClaw Weixin gateway is available for inbound forwarding
:: and proactive daily-summary delivery. A disabled scheduled task is common
:: on Windows, so the helper can launch the existing gateway.cmd directly.
echo [INFO] 配置 OpenClaw 仅转发到 WeChatAI Agent...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\configure_openclaw_direct_relay.ps1"
if errorlevel 1 (
    echo [WARN] OpenClaw 直连 Agent 配置未完成；请检查 OpenClaw CLI。
)

echo [INFO] 检查 OpenClaw 微信网关...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\ensure_openclaw_gateway.ps1"
if errorlevel 1 (
    echo [WARN] OpenClaw 网关未就绪；网页仍会启动，但微信收发暂不可用。
)
echo.

:: Check if frontend is built, if so use production mode
if exist "frontend\dist\index.html" (
    echo [INFO] 检测到前端构建文件，使用生产模式
    echo [INFO] 启动后端...
    start "" /B /D "%BACKEND_DIR%" "%BACKEND_PYTHON%" run.py
    echo.
    echo ==========================================
    echo    WeChatAI 已启动!
    echo    打开浏览器访问: http://localhost:8090
    echo ==========================================
    echo.
    echo 按 Ctrl+C 停止服务
    pause >nul
) else (
    echo [INFO] 使用开发模式启动...
    echo.

    :: Start backend
    echo [1/2] 启动后端 (端口 8090)...
    start "WeChatAI-Backend" /D "%BACKEND_DIR%" "%BACKEND_PYTHON%" run.py

    :: Wait for backend
    timeout /t 3 /nobreak >nul

    :: Start frontend
    echo [2/2] 启动前端 (端口 5175)...
    cd frontend
    start "WeChatAI-Frontend" cmd /c "npx vite --host 0.0.0.0 --port 5175"
    cd ..

    timeout /t 4 /nobreak >nul

    echo.
    echo ==========================================
    echo    WeChatAI 已启动!
    echo    打开浏览器访问: http://localhost:5175
    echo ==========================================
    echo.
    echo 关闭此窗口将同时停止所有服务
    pause >nul

    :: Kill child processes on exit
    taskkill /fi "WINDOWTITLE eq WeChatAI-Backend" /f >nul 2>&1
    taskkill /fi "WINDOWTITLE eq WeChatAI-Frontend" /f >nul 2>&1
)
