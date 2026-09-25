@echo off
rem If auto-sort is not doing what it should, double-click this.
rem It writes a report to your Desktop and opens it. Nothing is sent
rem anywhere: you read it, and you decide.
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=python"

set "PROBLEM=%TEMP%\auto-sort-report-error.txt"
if defined PY (
  %PY% autosort.py diagnose --desktop 2> "%PROBLEM%"
  if not errorlevel 1 goto done
)

rem The full report could not be made: no Python, or auto-sort itself
rem failing. Write what this script can find out instead.
set "DESK="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%D"
if not defined DESK set "DESK=%USERPROFILE%"
set "STAMP="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HHmm'"`) do set "STAMP=%%T"
set "FILE=%DESK%\auto-sort problem report %STAMP%.txt"
set "PYTHON_LINE=python: not found - auto-sort needs Python 3.8 or newer"
if defined PY set "PYTHON_LINE=python: %PY%"
(
  echo auto-sort problem report ^(short form: the full one could not be made^)
  echo.
  echo WHAT HAPPENED? Write it here, in your own words: what you did, what
  echo you expected, and what you saw instead.
  echo.
  echo.
  echo.
  echo HOW TO SEND IT: open https://github.com/snepssen/auto-sort/issues/new
  echo ^(a free GitHub account is needed^), give it a short title, and drag
  echo this file into the box.
  echo.
  echo ------------------------------------------------------------------------
  ver
  echo %PYTHON_LINE%
  echo.
  echo what went wrong making the full report:
  if exist "%PROBLEM%" type "%PROBLEM%"
) > "%FILE%"
echo The report is on your Desktop:
echo   %FILE%
start "" notepad "%FILE%"

:done
if exist "%PROBLEM%" del "%PROBLEM%"
echo.
echo Press any key to close this window.
pause >nul
