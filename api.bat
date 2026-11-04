@echo off
setlocal
cd /d "%~dp0"

if not exist "frontend\dist\" (
    echo Frontend build directory not found: frontend\dist
    echo Build the frontend first:
    echo   cd frontend
    echo   npm install
    echo   npm run build
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Could not find .venv\Scripts\python.exe. Install the project environment first.
    exit /b 1
)

set "CHORE_CHART_DB=%~dp0chore_chart.sqlite3"
".venv\Scripts\python.exe" -m uvicorn chore_chart.api:app --app-dir "%~dp0src" --reload --host 0.0.0.0 --port 8000
exit /b %errorlevel%