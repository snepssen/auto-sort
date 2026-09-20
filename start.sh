#!/bin/sh
# Starts auto-sort from a copied checkout without requiring a shell profile.
cd "$(dirname "$0")" || exit 1

for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "${PYTHON:-}" ]; then
  echo "auto-sort needs Python 3.8 or newer." >&2
  exit 1
fi

# Optional tools prompt only when they are absent; decline/failure never blocks.
if ! "$PYTHON" bootstrap.py --optional-check >/dev/null 2>&1; then
  "$PYTHON" bootstrap.py || true
fi
# No arguments means somebody double-clicked it rather than typed it, so do
# the useful thing instead of printing a usage message at them.
if [ "$#" -eq 0 ]; then
  set -- start
fi
exec "$PYTHON" autosort.py "$@"
