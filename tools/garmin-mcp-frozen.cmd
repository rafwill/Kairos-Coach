@echo off
setlocal

REM Frozen MCP launcher (Windows): arranca el MCP propio de Kairos.
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "VENV_PY=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
  "%VENV_PY%" -m agent.kairos_mcp_server %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  1>&2 echo [garmin-mcp-frozen] ERROR: no se encontro python en .venv ni en PATH.
  1>&2 echo Instala dependencias con: py -m pip install -r requirements.txt
  exit /b 1
)

python -m agent.kairos_mcp_server %*
