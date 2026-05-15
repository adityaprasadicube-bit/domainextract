@echo off
cd /d "%~dp0"
title CDR WebApp Launcher

echo ============================================
echo        CDR WebApp - Docker Launcher
echo ============================================
echo.

REM ---- Check if Docker is running ----
echo [1/4] Checking Docker status...
docker info >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Docker is not running!
    echo         Please start Docker Desktop and try again.
    echo.
    pause
    exit /b 1
)
echo [OK] Docker is running.
echo.

REM ---- Check / Load Docker Image ----
REM Use relative path (faster on Docker Desktop than full C:\ path)
set IMAGE_NAME=cdrwebapp:v1.0

echo [2/4] Checking Docker image...
docker image inspect %IMAGE_NAME% >nul 2>&1
if errorlevel 1 (
    echo [INFO] Loading image from cdrwebapp_v1.0.tar...
    if not exist "cdrwebapp_v1.0.tar" (
        echo [ERROR] cdrwebapp_v1.0.tar not found. Place it in the same folder as StartApp.bat
        pause
        exit /b 1
    )
    docker load -i cdrwebapp_v1.0.tar
    if errorlevel 1 (
        echo [ERROR] Failed to load image.
        pause
        exit /b 1
    )
) else (
    echo [OK] Image exists. Skipping load.
)
echo.

REM ---- Stop and Remove Old Container ----
echo [3/4] Cleaning up old container (if any)...
docker stop ip-search-engine >nul 2>&1
docker rm ip-search-engine >nul 2>&1
echo [OK] Old container removed.
echo.

REM ---- Start New Container ----
echo [4/4] Starting application container...
docker run -d --name ip-search-engine ^
    -p 8000:8000 ^
    -e MONGO_HOST=host.docker.internal ^
    -e MONGO_PORT=27017 ^
    --add-host=host.docker.internal:host-gateway ^
    --restart unless-stopped ^
    %IMAGE_NAME%

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start the container.
    echo         Check if port 8000 is already in use.
    echo         Run: netstat -ano ^| findstr :8000
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   [SUCCESS] Application is now running!
echo ============================================
echo.
echo   URL       : http://localhost:8000
echo   Container : ip-search-engine
echo.
echo   To stop the app, run:
echo   docker stop ip-search-engine
echo.
pause
