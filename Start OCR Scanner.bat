@echo off
setlocal
cd /d "%~dp0"
title OCR Frame Inspector

echo ==================================================
echo              OCR Frame Inspector                  
echo ==================================================

rem Check if port 8000 is already active
netstat -ano | findstr 127.0.0.1:8000 | findstr LISTENING >nul
if %errorlevel% equ 0 (
    echo [*] Server is already running on http://127.0.0.1:8000
    start http://127.0.0.1:8000
    pause
    exit /b
)

rem Launch browser after a short wait
start /b powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"

rem Start server
if exist ".venv\Scripts\ocr-scanner-web.exe" (
    echo [*] Starting server via virtual environment...
    ".venv\Scripts\ocr-scanner-web.exe" --host 127.0.0.1 --port 8000
) else (
    echo [*] Starting server via uv...
    uv run ocr-scanner-web --host 127.0.0.1 --port 8000
)

pause
