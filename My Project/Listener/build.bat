@echo off
REM ============================================================
REM  build.bat — Compile proxy.cpp with either MSVC or MinGW
REM  Run this from the Listener\ directory.
REM ============================================================

setlocal

set "SRC=proxy.cpp"
set "OUT=proxy.exe"
set "LIBS=ws2_32.lib interception.lib"

REM ── Try MSVC (cl.exe) first ──────────────────────────────────────────────────
where cl.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo [build] Using MSVC cl.exe ...
    cl.exe /nologo /EHsc /O2 /W3 %SRC% /Fe:%OUT% /link %LIBS%
    if %errorlevel% equ 0 (
        echo [build] SUCCESS: %OUT% created.
    ) else (
        echo [build] MSVC build FAILED. Check errors above.
        exit /b 1
    )
    goto :done
)

REM ── Try MinGW g++ ────────────────────────────────────────────────────────────
where g++.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo [build] Using MinGW g++ ...
    g++.exe -std=c++17 -O2 -Wall %SRC% -o %OUT% -lws2_32 -L. -linterception
    if %errorlevel% equ 0 (
        echo [build] SUCCESS: %OUT% created.
    ) else (
        echo [build] MinGW build FAILED. Check errors above.
        exit /b 1
    )
    goto :done
)

REM ── No compiler found ────────────────────────────────────────────────────────
echo [build] ERROR: Neither cl.exe (MSVC) nor g++.exe (MinGW) found in PATH.
echo.
echo   MSVC:  Run this script from a "Developer Command Prompt for VS"
echo          or run:  "C:\Program Files\Microsoft Visual Studio\...\vcvars64.bat"
echo.
echo   MinGW: Install via https://www.mingw-w64.org/ and add bin\ to PATH.
exit /b 1

:done
echo.
echo [build] Run: proxy.exe
echo         Also run act-as-a-senior-c-windows\MouseCornerInterceptor.exe for MCQ option movement,
echo         then start agent.py in a separate window.
endlocal
