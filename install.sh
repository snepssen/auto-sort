#!/bin/sh
# Install auto-sort, or update it. Paste this into a terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/snepssen/auto-sort/main/install.sh | sh
#
# It finds Python, fetches install.py, and runs it. Everything else --
# what it asks, what it does, where it puts things -- is in install.py.
set -e

INSTALLER="https://raw.githubusercontent.com/snepssen/auto-sort/${AUTO_SORT_BRANCH:-main}/install.py"

PYTHON=
for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo
  echo "  auto-sort needs Python 3.8 or newer, and this computer does not"
  echo "  have it yet."
  echo
  case "$(uname)" in
    Darwin)
      echo "  On a Mac, this gets it (a window will ask to install the"
      echo "  command line tools; say yes):"
      echo
      echo "    xcode-select --install"
      ;;
    *)
      echo "  Install it with your system's software centre, or for example:"
      echo
      echo "    sudo apt install python3      (Debian, Ubuntu, Mint)"
      echo "    sudo dnf install python3      (Fedora)"
      echo "    sudo pacman -S python         (Arch, Manjaro)"
      ;;
  esac
  echo
  echo "  Then paste the install line again."
  exit 1
fi

script="$(mktemp 2>/dev/null || echo "/tmp/auto-sort-install.$$").py"
trap 'rm -f "$script"' EXIT
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$INSTALLER" -o "$script"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$script" "$INSTALLER"
else
  "$PYTHON" -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$INSTALLER" "$script"
fi

# The questions need a keyboard, and when this script arrives through a
# pipe its own input is the pipe. The terminal is still there to ask.
if [ -r /dev/tty ] && [ -t 1 ]; then
  "$PYTHON" "$script" "$@" </dev/tty
else
  "$PYTHON" "$script" "$@"
fi
