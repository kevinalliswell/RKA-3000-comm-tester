@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Please run: py -3 -m venv .venv
  echo Then run: .venv\Scripts\python -m pip install -e .
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m rka_comm_tester
