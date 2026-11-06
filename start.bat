@echo off
rem One-step launcher for Windows:
rem   creates .venv on first run, installs requirements, starts the simulator.
rem Usage:  start.bat            (extra arguments are passed to main.py)
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :deps
echo Creating virtual environment in .venv ...
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto :error

:deps
".venv\Scripts\python.exe" -c "import numpy, scipy, PySide6, pyqtgraph" >nul 2>nul
if %errorlevel%==0 goto :run
echo Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

:run
".venv\Scripts\python.exe" main.py %*
exit /b %errorlevel%

:error
echo.
echo Setup failed. Make sure Python 3.10 or newer is installed - see INSTALLATION.md.
exit /b 1
