@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto run

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found. Create the virtual environment first.
    pause
    exit /b 1
)
set "PYTHON_EXE=python"

:run
"%PYTHON_EXE%" "%~dp0launcher.py"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
