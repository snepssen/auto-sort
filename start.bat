@echo off
setlocal
cd /d "%~dp0"
rem No arguments means somebody double-clicked it rather than typed it, so do
rem the useful thing instead of printing a usage message at them.
set ARGS=%*
if "%~1"=="" set ARGS=start
where py >nul 2>nul
if not errorlevel 1 (
  py -3 bootstrap.py --optional-check >nul 2>nul
  if errorlevel 1 py -3 bootstrap.py
  py -3 autosort.py %ARGS%
  exit /b %errorlevel%
)
where python >nul 2>nul
if not errorlevel 1 (
  python bootstrap.py --optional-check >nul 2>nul
  if errorlevel 1 python bootstrap.py
  python autosort.py %ARGS%
  exit /b %errorlevel%
)
echo auto-sort needs Python 3.8 or newer.
exit /b 1
