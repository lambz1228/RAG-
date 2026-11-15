@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if /I "%~1"=="gui" (
  "%PYTHON_EXE%" gui.py
) else (
  "%PYTHON_EXE%" app.py
)
exit /b %ERRORLEVEL%
