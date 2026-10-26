@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto setup
".venv\Scripts\python.exe" -c "import free_drawing_mcp.desktop" >nul 2>&1
if errorlevel 1 goto setup
goto launch

:setup
where uv >nul 2>&1
if errorlevel 1 (
  echo Install uv, then run: uv sync --extra desktop
  pause
  exit /b 1
)
echo Setting up Free Drawing...
uv sync --extra desktop
if errorlevel 1 (
  echo Setup could not finish.
  pause
  exit /b 1
)

:launch
start "" "%~dp0.venv\Scripts\pythonw.exe" -m free_drawing_mcp.desktop
