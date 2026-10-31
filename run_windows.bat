@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Please run: py -3 -m venv .venv
  echo Then run: .venv\Scripts\python -m pip install -e .
  pause
  exit /b 1
)
if not exist "%~dp0frontend\node_modules" (
  echo Installing frontend dependencies...
  pushd "%~dp0frontend"
  call npm ci
  if errorlevel 1 (
    popd
    exit /b 1
  )
  popd
)
if not exist "%~dp0node_modules" (
  pushd "%~dp0"
  call npm ci
  if errorlevel 1 (
    popd
    exit /b 1
  )
  popd
)
pushd "%~dp0"
call npm run tauri dev
set "RKA_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %RKA_EXIT_CODE%
