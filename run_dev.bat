@echo off
setlocal
set "PY=python"
where py >nul 2>&1 && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto :err
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)
".venv\Scripts\python.exe" main.py
pause
exit /b 0
:err
echo Setup failed.
pause
exit /b 1
