@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 bootstrap.py --optional-check >nul 2>nul
  if errorlevel 1 py -3 bootstrap.py
  py -3 autosort.py %*
  exit /b %errorlevel%
)
where python >nul 2>nul
if not errorlevel 1 (
  python bootstrap.py --optional-check >nul 2>nul
  if errorlevel 1 python bootstrap.py
  python autosort.py %*
  exit /b %errorlevel%
)
echo auto-sort needs Python 3.8 or newer.
exit /b 1
