@echo off
rem Double-click: find PC IP, write .env, start all services, open the browser.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-windows.ps1"
pause
