@echo off
rem If auto-sort is not doing what it should, double-click this.
rem It writes a report to your Desktop and opens it. Nothing is sent
rem anywhere: you read it, and you decide.
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=python"

rem Do not retain raw errors: a broken import can name private files.
if defined PY (
  %PY% autosort.py diagnose --desktop 2>nul
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
  echo system: Windows
  echo %PYTHON_LINE%
  echo.
  if defined PY (echo full report: failed) else (echo full report: unavailable without Python 3.8 or newer)
  echo Error details are omitted to keep file paths and document text private.
  echo Review your description and screenshots too: issues are public.
  echo Nothing is sent automatically.
) > "%FILE%"
echo The report is on your Desktop:
echo   %FILE%
start "" notepad "%FILE%"

:done
echo.
echo Press any key to close this window.
pause >nul
