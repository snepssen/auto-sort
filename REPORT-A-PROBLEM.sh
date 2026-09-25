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

problem="$(mktemp 2>/dev/null || echo /tmp/auto-sort-report-error.$$)"
if [ -n "$PYTHON" ] && "$PYTHON" autosort.py diagnose --desktop 2>"$problem"; then
  rm -f "$problem"
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
  echo "system: $(uname -srm)"
  if command -v sw_vers >/dev/null 2>&1; then
    echo "macOS: $(sw_vers -productVersion)"
  elif [ -r /etc/os-release ]; then
    echo "linux: $(. /etc/os-release && echo "$PRETTY_NAME")"
  fi
  echo "desktop: ${XDG_CURRENT_DESKTOP:-unknown} (${XDG_SESSION_TYPE:-unknown})"
  echo "python: ${PYTHON:-not found (auto-sort needs Python 3.8 or newer)}"
  if [ -s "$problem" ]; then
    echo
    echo "what went wrong making the full report:"
    tail -n 30 "$problem" | sed "s|$HOME|~|g"
  fi
} >"$file"
rm -f "$problem"

echo "The report is on your Desktop:"
echo "  $file"
if command -v open >/dev/null 2>&1 && [ "$(uname)" = Darwin ]; then
  open "$file"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$file" >/dev/null 2>&1 &
fi
echo
echo "You can close this window."
