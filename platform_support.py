"""Find optional enrichment programs without making them dependencies.

Adapted for auto-sort from siphon's self-contained bootstrap pattern.  The
table lives here rather than in a shared checkout so this program can still be
started from a copied folder or USB drive.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


class Program(object):
    def __init__(self, key, binaries, purpose, packages):
        self.key = key
        self.binaries = tuple(binaries)
        self.purpose = purpose
        self.required = False
        self.packages = dict(packages)

    def package_for(self, manager):
        return self.packages.get(manager)

    def install_line(self):
        manager = current_manager()
        package = self.package_for(manager) if manager else None
        if not package:
            return ""
        return " ".join(MANAGERS[manager]["command"] + package.split())


PROGRAMS = {
    "ffprobe": Program(
        "ffprobe", ("ffprobe",),
        "reading detailed audio and video metadata",
        {"brew": "ffmpeg", "apt": "ffmpeg", "dnf": "ffmpeg",
         "pacman": "ffmpeg", "winget": "Gyan.FFmpeg"}),
    "exiftool": Program(
        "exiftool", ("exiftool",),
        "reading metadata from uncommon cameras and RAW formats",
        {"brew": "exiftool", "apt": "libimage-exiftool-perl",
         "dnf": "perl-Image-ExifTool", "pacman": "perl-image-exiftool",
         "winget": "ExifTool.ExifTool"}),
}


# `needs_root` means bootstrap shows the exact command but never runs sudo.
MANAGERS = {
    "brew": {"probe": "brew", "command": ["brew", "install"],
             "needs_root": False, "label": "Homebrew"},
    "winget": {"probe": "winget", "command": ["winget", "install", "-e", "--id"],
               "needs_root": False, "label": "winget"},
    "apt": {"probe": "apt-get", "command": ["sudo", "apt-get", "install", "-y"],
            "needs_root": True, "label": "apt"},
    "dnf": {"probe": "dnf", "command": ["sudo", "dnf", "install", "-y"],
            "needs_root": True, "label": "dnf"},
    "pacman": {"probe": "pacman", "command": ["sudo", "pacman", "-S", "--noconfirm"],
               "needs_root": True, "label": "pacman"},
}

_manager = None
_cache = {}
_EXTRA_PATHS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                os.path.expanduser("~/.local/bin"), "/usr/bin")


def current_manager():
    global _manager
    if _manager is None:
        for key in ("brew", "winget", "apt", "dnf", "pacman"):
            if shutil.which(MANAGERS[key]["probe"]):
                _manager = key
                break
        else:
            _manager = False
    return _manager or None


def locate(*binaries):
    """Find an executable even when Finder did not inherit the shell PATH."""
    for binary in binaries:
        found = shutil.which(binary)
        if found:
            return found
        for directory in _EXTRA_PATHS:
            candidate = os.path.join(directory, binary)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def find(key):
    if key not in _cache:
        _cache[key] = locate(*PROGRAMS[key].binaries)
    return _cache[key]


def forget():
    _cache.clear()


def version(key):
    path = find(key)
    if not path:
        return None
    try:
        done = subprocess.run([path, "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (done.stdout or done.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def missing():
    return [program for key, program in PROGRAMS.items() if not find(key)]


def immutable_root():
    """Best-effort detection of an OS-managed, read-only root filesystem.

    Not every read-only signal is worth acting on -- a plain `mount -o ro`
    means nothing here -- so this only reports True for image-based distros
    that need an explicit unlock step before their own package manager can
    write anything. SteamOS is the case that matters: `sudo pacman -S` looks
    like a working command and fails anyway, which is worse than not
    printing one at all.
    """
    if shutil.which("steamos-readonly"):
        return True
    if os.path.exists("/run/ostree-booted"):
        return True
    return False


# ---------------------------------------------------------------------------
# Python packages: there are none, and that is the point
# ---------------------------------------------------------------------------
#
# auto-sort had exactly one third-party dependency for about a day: PyObjC,
# to put an icon in a Mac's menu bar. It is gone. The Objective-C runtime is
# a plain C library and `ctypes` speaks C, so `tray.py` now talks to it
# directly the way the Windows backend always did.
#
# That mattered for more than tidiness. PyObjC cannot be installed at all on
# a Homebrew, Debian or Fedora Python -- they are marked externally managed
# under PEP 668 and refuse `pip install` -- so the icon was unreachable on
# the machines most likely to run this, and working around it meant auto-sort
# building and owning a forty-megabyte virtual environment. Removing the
# dependency removed the whole problem and about two hundred lines with it.
#
# These two functions remain because `bootstrap` and the tests ask, and
# because an honest empty answer is worth more than a missing one.

MODULES = {}


def missing_modules():
    return []
