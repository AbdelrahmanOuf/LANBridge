@echo off
setlocal DisableDelayedExpansion
title LANBridge Pro
pushd "%~dp0"
if errorlevel 1 goto folder_error

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\run.ps1"
set "LANBRIDGE_EXIT_CODE=%ERRORLEVEL%"
popd
if "%LANBRIDGE_EXIT_CODE%"=="0" exit /b 0
goto failed

:folder_error
set "LANBRIDGE_EXIT_CODE=1"
echo ERROR: Cannot open the LANBridge Pro project folder.

:failed
echo.
echo LANBridge Pro stopped with exit code %LANBRIDGE_EXIT_CODE%.
echo Read the error above. Source execution requires Python 3.12 x64.
echo First-time setup downloads the required packages. Keep this window open.
echo Press any key to close this window.
pause >nul
exit /b %LANBRIDGE_EXIT_CODE%
