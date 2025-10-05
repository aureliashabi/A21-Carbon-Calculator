@echo off
REM Try standard Desktop first
set PROJECT_DIR=%USERPROFILE%\Desktop\A21 CarbonCalc
if not exist "%PROJECT_DIR%" (
    REM Fallback to OneDrive Desktop
    set PROJECT_DIR=%USERPROFILE%\OneDrive\Desktop\A21 CarbonCalc
)

REM Start FastAPI backend in new cmd window
start cmd /k "cd /d %PROJECT_DIR% && python -m uvicorn server:app --reload --port 8000"

REM Start Streamlit frontend in new cmd window
start cmd /k "cd /d %PROJECT_DIR% && python -m streamlit run app.py"

REM Optional: wait 3 seconds then close main window
echo All services started. This window will close in 3 seconds...
timeout /t 3 /nobreak >nul
exit