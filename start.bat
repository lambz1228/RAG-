@echo off
setlocal
cd /d "%~dp0"
set "BIOKB_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "BIOKB_PYTHON=%CD%\.venv\Scripts\python.exe"
set "BIOKB_OLLAMA_STARTER="
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="BIOKB_LOCAL_OLLAMA_STARTER" set "BIOKB_OLLAMA_STARTER=%%B"
    )
)
if defined BIOKB_LLM_BACKEND if /i not "%BIOKB_LLM_BACKEND%"=="ollama" set "BIOKB_OLLAMA_STARTER="
if defined BIOKB_OLLAMA_STARTER (
    powershell.exe -NoProfile -File "%BIOKB_OLLAMA_STARTER%"
    if errorlevel 1 exit /b 1
)
"%BIOKB_PYTHON%" app.py
exit /b %ERRORLEVEL%
