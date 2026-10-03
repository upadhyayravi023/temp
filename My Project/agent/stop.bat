@echo off
taskkill /F /IM agent.exe /T >nul 2>&1
taskkill /F /IM proxy.exe /T >nul 2>&1
taskkill /F /IM MouseCornerInterceptor.kiosk.exe /T >nul 2>&1
echo All processes stopped.
