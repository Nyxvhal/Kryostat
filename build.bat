@echo off
setlocal
title Kryostat build
echo ============================================
echo   KRYOSTAT - build exe + installer
echo ============================================
echo.

set "PY=python"
where py >nul 2>&1 && set "PY=py -3"

echo [1/5] Creating virtual environment...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto :err
)
set "VPY=%CD%\.venv\Scripts\python.exe"

echo [2/5] Installing dependencies...
"%VPY%" -m pip install --upgrade pip wheel >nul 2>&1
"%VPY%" -m pip install -r requirements.txt || goto :err
"%VPY%" -m pip install pyinstaller || goto :err

echo [3/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/5] Building Kryostat.exe ...
"%VPY%" -m PyInstaller --noconfirm --clean Kryostat.spec || goto :err

echo [5/5] Building installer (Inno Setup)...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    "%ISCC%" installer.iss || goto :err
    echo.
    echo DONE: installer  -^> dist\KryostatSetup.exe
) else (
    echo Inno Setup not found - skipping installer.
    echo Download: https://jrsoftware.org/isdl.php
)

echo.
echo DONE: application -^> dist\Kryostat\Kryostat.exe
echo.
pause
exit /b 0

:err
echo.
echo BUILD FAILED. Scroll up for the error message.
pause
exit /b 1
