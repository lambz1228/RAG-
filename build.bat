@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
"%PYTHON_EXE%" -m pip install -e ".[build]"
if errorlevel 1 exit /b %ERRORLEVEL%
"%PYTHON_EXE%" -m PyInstaller --noconfirm --distpath dist --workpath build\biokb build_gui.spec
exit /b %ERRORLEVEL%
