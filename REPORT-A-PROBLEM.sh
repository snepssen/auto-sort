#!/bin/sh
# If auto-sort is not doing what it should, run this (double-click it, or
# type ./REPORT-A-PROBLEM.sh). It writes a report to your Desktop and opens
# it. Nothing is sent anywhere: you read it, and you decide.
cd "$(dirname "$0")" || exit 1

PYTHON=
for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

# A broken import can print paths, source lines or document text. Do not
# retain stderr for the fallback: Python may be unavailable to sanitise it.
if [ -n "$PYTHON" ] && "$PYTHON" autosort.py diagnose --desktop 2>/dev/null; then
  echo
  echo "You can close this window."
  exit 0
fi

# The full report could not be made -- no Python, or auto-sort itself
# failing. Write what this script can find out, so there is still
# something to send.
desktop=
if command -v xdg-user-dir >/dev/null 2>&1; then
  desktop="$(xdg-user-dir DESKTOP 2>/dev/null)"
fi
[ -d "$desktop" ] || desktop="$HOME/Desktop"
[ -d "$desktop" ] || desktop="$HOME"
file="$desktop/auto-sort problem report $(date '+%Y-%m-%d %H%M').txt"
{
  echo "auto-sort problem report (short form: the full one could not be made)"
  echo
  echo "WHAT HAPPENED? Write it here, in your own words: what you did, what"
  echo "you expected, and what you saw instead."
  echo
  echo
  echo
  echo "HOW TO SEND IT: open https://github.com/snepssen/auto-sort/issues/new"
  echo "(a free GitHub account is needed), give it a short title, and drag"
  echo "this file into the box."
  echo
  echo "------------------------------------------------------------------------"
  case "$(uname -s)" in
    Darwin) echo "system: macOS" ;;
    Linux) echo "system: Linux" ;;
    *) echo "system: other" ;;
  esac
  case "${XDG_SESSION_TYPE:-}" in
    wayland) echo "session: Wayland" ;;
    x11) echo "session: X11" ;;
    tty) echo "session: terminal" ;;
    *) echo "session: unknown" ;;
  esac
  echo "python: ${PYTHON:-not found (auto-sort needs Python 3.8 or newer)}"
  if [ -n "$PYTHON" ]; then
    echo "full report: failed"
  else
    echo "full report: unavailable without Python 3.8 or newer"
  fi
  echo "Error details are omitted to keep file paths and document text private."
  echo "Review your description and screenshots too: issues are public."
  echo "Nothing is sent automatically."
} >"$file"

echo "The report is on your Desktop:"
echo "  $file"
if command -v open >/dev/null 2>&1 && [ "$(uname)" = Darwin ]; then
  open "$file"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$file" >/dev/null 2>&1 &
fi
echo
echo "You can close this window."
