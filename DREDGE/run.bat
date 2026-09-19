@echo off
cd /d "%~dp0"
where python >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo   Python is not installed or not on your PATH.
  echo   Get it from https://www.python.org/downloads/
  echo   ^(tick "Add python.exe to PATH" during install^)
  echo.
  pause
  exit /b
)
start "" pythonw dredge.py
