@echo off
setlocal
cd /d "%~dp0"

where cl.exe >nul 2>&1
if %errorlevel% equ 0 (
    cl.exe /nologo /EHsc /O2 /W3 MouseCornerInjector.cpp /Fe:MouseCornerInterceptor.exe /link ws2_32.lib ..\Listener\interception.lib
    if errorlevel 1 exit /b 1
    goto :success
)

where g++.exe >nul 2>&1
if %errorlevel% equ 0 (
    g++.exe -std=c++17 -O2 -Wall MouseCornerInjector.cpp -o MouseCornerInterceptor.exe -lws2_32 -L..\Listener -linterception
    if errorlevel 1 exit /b 1
    goto :success
)

echo [build] ERROR: Neither cl.exe nor g++.exe was found in PATH.
exit /b 1

:success
copy /Y ..\Listener\interception.dll interception.dll >nul
if errorlevel 1 (
    echo [build] ERROR: Could not copy Listener\interception.dll beside the mouse interceptor.
    exit /b 1
)
echo [build] SUCCESS: MouseCornerInterceptor.exe created.
echo [build] Run MouseCornerInterceptor.exe, then Listener\proxy.exe, then agent.py.
endlocal
