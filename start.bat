@echo off
setlocal
cd /d "%~dp0"
set "BIOKB_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "BIOKB_PYTHON=%CD%\.venv\Scripts\python.exe"
"%BIOKB_PYTHON%" app.py
exit /b %ERRORLEVEL%
