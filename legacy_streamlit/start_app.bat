@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m streamlit run app.py
) else (
    python -m streamlit run app.py
)

if errorlevel 1 (
    echo.
    echo Не удалось запустить приложение. Выполните установку из README.md.
    pause
)

endlocal
