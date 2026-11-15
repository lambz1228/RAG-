@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "BIOKB_DATA_DIR=%CD%\data\biokb-v26-authors-20261105"
if not exist "%BIOKB_DATA_DIR%\knowledge.sqlite3" (
  echo [BioKB] 扩充版数据库不存在，请先完成文献入库。
  exit /b 2
)
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
"%PYTHON_EXE%" app.py
exit /b %ERRORLEVEL%
